import astropy.units as u
from astropy.coordinates import get_body
from astropy.coordinates import SkyCoord, EarthLocation, AltAz
from astropy.time import Time
from datetime import datetime, timezone, timedelta
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import pytest
from queue import Queue
import time
import threading

from api import tm_dm, ws_dm
from dsh.dish_display import DishDisplay
from dsh.weather_display import WeatherDisplay
from dsh.drivers.driver import DishDriver
from dsh.drivers.drift.driver import DriftDriver
from dsh.drivers.md01.md01_driver import MD01Driver
from env.app import App
from ipc.message import APIMessage
from ipc.action import Action
from ipc.message import AppMessage
from ipc.tcp_client import TCPClient
from ipc.tcp_server import TCPServer
from models.comms import CommunicationStatus, InterfaceType
from models.dsh import DishManagerModel, DriverType, PointingState, DishMode, Capability
from models.health import HealthState
from models.oda import ObsList, ScanStore
from models.target import TargetModel, PointingType
from models.ws import WeatherData, WeatherStationList
from util.alarm_rsp_efficiency import get_alarm_rsp_efficiency
from util import log
from util.registry import DISH_DRIVER_NAMESPACE, resolve
from util.xbase import XBase, XStreamUnableToExtract, XSoftwareFailure

logger = logging.getLogger(__name__)

SIDEREAL_RATE_DEG_PER_SEC = 360.0 / 86164.1  # Sidereal rate in degrees per second (86164.1 seconds in a sidereal day)

# Dish Manager (DM)

class DM(App):
    """A class representing the Dish Manager."""

    dm_model = DishManagerModel(id="dm001")

    def __init__(self, app_name: str = "dm"):

        super().__init__(app_name=app_name, app_model = self.dm_model.app)

        # Telescope Manager interface
        self.tm_system = "tm"
        self.tm_api = tm_dm.TM_DM()
        # Telescope Manager TCP Server
        self.tm_endpoint = TCPServer(description=self.tm_system, queue=self.get_queue(), host=self.get_args().tm_host, port=self.get_args().tm_port)
        # Register Telescope Manager interface with the App
        self.register_interface(self.tm_system, self.tm_api, self.tm_endpoint, InterfaceType.APP_APP)
        # Set initial Telescope Manager connection status
        self.dm_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        
        # Weather Station TCP Server
        self.ws_system = "ws"
        self.ws_api = ws_dm.WS_DM()
        self.ws_endpoint = TCPServer(description=self.ws_system, queue=self.get_queue(), host=self.get_args().ws_host, port=self.get_args().ws_port)
        # Register Weather Station interface with the App
        self.register_interface(self.ws_system, self.ws_api, self.ws_endpoint, InterfaceType.APP_APP)
        self.alarm_triggered = False # Flag to track whether a weather alarm is currently triggered based on Weather Station data and thresholds. 

        # Interfaces to each respective dish need to be managed by the respective dish drivers
        self.dish_drivers = {}        # Dictionary to hold a dish driver for each dish
        self.dish_locks = {}          # Dictionary of threading locks, one per dish
        self.dish_displays = {}       # Dictionary to hold DishDisplay objects for each dish
        self.weather_displays = {}    # Dictionary to hold WeatherDisplay objects for each weather station
        self.alarm_logger = self.get_alarm_logger()
        self.last_alarm_metrics_refresh_hour = None

    def add_args(self, arg_parser): 
        """ Specifies the Dish Manager's command line arguments.
        """
        super().add_args(arg_parser)

        arg_parser.add_argument("--tm_host", type=str, required=False, help="TCP host to listen on for Telescope Manager commands", default="localhost")
        arg_parser.add_argument("--tm_port", type=int, required=False, help="TCP port for Telescope Manager commands", default=50002)
        arg_parser.add_argument("--ws_host", type=str, required=False, help="TCP host to listen on for Weather Station commands", default="localhost")
        arg_parser.add_argument("--ws_port", type=int, required=False, help="TCP port for Weather Station commands", default=51000)

    def _get_dish_lock(self, dsh_id: str) -> threading.RLock:
        """Get or create a threading lock for a specific dish."""
        if dsh_id not in self.dish_locks:
            self.dish_locks[dsh_id] = threading.RLock()
        return self.dish_locks[dsh_id]

    def process_init(self) -> Action:
        """ Processes initialisation event on startup once all app processors are running.
            Runs in single threaded mode and switches to multi-threading mode after this method completes.
        """
        logger.debug(f"DM initialisation event")

        action = Action()

        input_dir = f"./config/{self.get_args().profile}"

        # Load Weather Station configuration from disk, initialises wind and precipitation thresholds for the WeatherStationList model
        filename = "WeatherStationList.json"

        try:
            weatherstation_store = self.dm_model.weather_store.load_from_disk(input_dir=input_dir, filename=filename)
        except FileNotFoundError:
            weatherstation_store = None
            logger.warning(f"DM could not load Weather Station configuration from directory {input_dir} file {filename}")

        self.dm_model.weather_store = weatherstation_store if weatherstation_store is not None else WeatherStationList()

        # Reset runtime alarm timing so the startup grace period is measured from
        # the current DM process start, not from a persisted config timestamp.
        self.dm_model.weather_store.created_dt = datetime.now(timezone.utc)
        self.dm_model.weather_store.trigger_dt = None
        self.dm_model.weather_store.weather_summaries = self.dm_model.weather_store.get_weather_summaries()

        logger.info(f"DM initialised Weather Station configuration:\n{self.dm_model.weather_store}")
        self._log_alarm_event(event_type="snapshot", reason="startup")

        # Load Dish configuration from disk, config file is located in ./config/<profile>/<model>.json
        # <profile> can be specified as a cmd line argument (provided by the App base class) default='default'
        # Config file defines initial list of dishes to be processed by the DM and their initial parameters such as driver type and location
        filename = "DishList.json"

        try:
            dish_store = self.dm_model.dish_store.load_from_disk(input_dir=input_dir, filename=filename)
        except FileNotFoundError:
            dish_store = None
            logger.warning(f"DM could not load Dish configuration from directory {input_dir} file {filename}")
            
        if dish_store is None:
            logger.error(f"DM initialisation did not find any configured dishes.")
            return action

        self.dm_model.dish_store = dish_store
        logger.info(f"DM loaded Dish configuration from directory {input_dir} file {filename}")

        # Instantiate drivers for each dish and initiate a polling driver timer for each dish
        for dish in self.dm_model.dish_store.dish_list:
            driver_ref = None
            if dish.driver_config is not None:
                driver_ref = getattr(dish.driver_config, "driver", None) or getattr(dish.driver_config, "name", None)

            driver = None

            if driver_ref:
                ctor = resolve(DISH_DRIVER_NAMESPACE, driver_ref)
                if ctor is None:
                    logger.warning("DM could not resolve custom driver '%s' for Dish %s", driver_ref, dish.dsh_id)
                else:
                    try:
                        driver = ctor(dsh_model=dish)
                    except TypeError:
                        driver = ctor(dish)
                    logger.info("DM instantiated custom driver '%s' for Dish %s", driver_ref, dish.dsh_id)

            if driver is None:
                driver_type = dish.driver_type.name
                if driver_type == DriverType.MD01.name:
                    driver = MD01Driver(dsh_model=dish)
                    logger.info(f"DM instantiated MD01 driver for Dish {dish.dsh_id}")
                elif driver_type == DriverType.DRIFT.name:
                    driver = DriftDriver(dsh_model=dish)
                    logger.info(f"DM instantiated Drift driver for Dish {dish.dsh_id}")
                else:
                    logger.warning(f"DM cannot instantiate driver for Dish {dish.dsh_id} with unknown driver type {driver_type}")

            if driver is not None:
                self.dish_drivers[dish.dsh_id] = driver

                # Start the polling driver timer for this dish
                action.set_timer_action(Action.Timer(
                    name=f"driver_timer_{dish.dsh_id}_{type(driver).__name__}", 
                    timer_action=driver.get_poll_interval_ms())) 

        # Start server endpoints and connect client endpoints to interfaces
        self.tm_endpoint.start()
        self.ws_endpoint.start()

        return action

    def process_tm_connected(self, event) -> Action:
        """ Processes Telescope Manager connected events.
        """
        logger.info(f"DM connected to Telescope Manager: {event.remote_addr}")
        self.dm_model.tm_connected = CommunicationStatus.ESTABLISHED
        
        action = Action()

        # For each dish driver, try to set the dish to STANDBY mode
        for dish_id, dish_driver in self.dish_drivers.items():

            # If the dish does not have an operational capability, skip setting to STANDBY
            if dish_driver.get_capability() not in [Capability.OPERATE_FULL, Capability.OPERATE_DEGRADED]:
                continue

            # If the dish cannot auto-transition to STANDBY_FP from its current mode, skip setting to STANDBY
            if dish_driver.get_mode() not in [DishMode.STARTUP, DishMode.STOW]:
                continue

            dish_lock = self._get_dish_lock(dish_id)
            with dish_lock:
                try:
                    dish_driver.set_dish_mode(DishMode.STANDBY_FP)
                except XBase as e:
                    logger.error(f"DM failed to set STANDBY_FP mode for Dish {dish_id} on TM connect: {e}")
        
        # Send initial status advice message to Telescope Manager
        # Informs TM of current DM status including dish statuses
        tm_adv = self._construct_status_adv_to_tm()
        action.set_msg_to_remote(tm_adv)
        return action

    def process_tm_disconnected(self, event) -> Action:
        """ Processes Telescope Manager disconnected events.
        """
        logger.info(f"DM disconnected from Telescope Manager: {event.remote_addr}")
        self.dm_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        
        action = Action()

        # For each dish driver, set the dish to STOW mode for safety if not already in STOW
        for dish_id, dish_driver in self.dish_drivers.items():

            # If the dish does not have an operational capability, skip setting to STOW
            if dish_driver.get_capability() not in [Capability.OPERATE_FULL, Capability.OPERATE_DEGRADED]:
                continue

            # If the dish cannot auto-transition to STOW from its current mode, skip setting to STOW
            if dish_driver.get_mode() not in [DishMode.STANDBY_LP, DishMode.STANDBY_FP, DishMode.CONFIG, DishMode.OPERATE, DishMode.UNKNOWN]:
                continue

            dish_lock = self._get_dish_lock(dish_id)
            with dish_lock:
                try:
                    dish_driver.set_dish_mode(DishMode.STOW)
                except XBase as e:
                    logger.error(f"DM failed to set STOW mode for Dish {dish_id} on TM disconnect: {e}")
                    
        return action

    def process_tm_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Telescope Manager service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"DM received Telescope Manager {api_call['msg_type']} msg, action code: {api_call['action_code']}, property: {api_call.get('property','')}")

        dish_id = api_msg.get('entity', None)
        dish_driver = self.dish_drivers.get(dish_id, None) if dish_id is not None else None
        dish_lock = self._get_dish_lock(dish_id) if dish_id is not None else None

        action = Action()

        # Validate that we have a valid dish driver and lock for the requested dish id
        if dish_driver is None or dish_lock is None:
            msg = f"DM processing event for dish id {dish_id} without valid driver instance and driver lock"
            logger.error(msg + f"\n{api_call}")
            rsp_msg = self._construct_rsp_to_tm(status=tm_dm.STATUS_ERROR, message=msg, api_msg=api_msg, api_call=api_call)
            action.set_msg_to_remote(rsp_msg)
            return action

        # If the Telescope Manager API call is to set the dish mode
        if api_call.get('action_code','') == 'set' and api_call.get('property','') == tm_dm.PROPERTY_MODE:

            mode = api_call.get('value', None)
            mode = DishMode(mode) if mode is not None else None
            
            # Prevent concurrent access to the dish driver
            with dish_lock:
                try:
                    dish_driver.set_dish_mode(mode) # Handles invalid or None mode internally
                except XBase as e:
                    msg = f"DM failed to set mode {mode.name if mode is not None else 'None'} for Dish {dish_id}: {e}"
                    logger.error(msg)
                    rsp_msg = self._construct_rsp_to_tm(status=tm_dm.STATUS_ERROR, message=msg, api_msg=api_msg, api_call=api_call)
                    action.set_msg_to_remote(rsp_msg)
                    return action

            msg = f"DM successfully set mode {mode} for Dish {dish_id}."
            rsp_msg = self._construct_rsp_to_tm(status=tm_dm.STATUS_SUCCESS, message=msg, api_msg=api_msg, api_call=api_call)
            action.set_msg_to_remote(rsp_msg)
            return action

        # If the Telescope Manager API call is to set the dish capability state
        if api_call.get('action_code','') == 'set' and api_call.get('property','') == tm_dm.PROPERTY_CAPABILITY:

            capability = api_call.get('value', None)
            capability = Capability(capability) if capability is not None else None

            # Prevent concurrent access to the dish driver
            with dish_lock:
                try:
                    dish_driver.set_dish_capability(capability) # Handles invalid or None capability internally
                except XBase as e:
                    msg = f"DM failed to set capability {capability.name if capability is not None else 'None'} for Dish {dish_id}: {e}"
                    logger.error(msg)
                    rsp_msg = self._construct_rsp_to_tm(status=tm_dm.STATUS_ERROR, message=msg, api_msg=api_msg, api_call=api_call)
                    action.set_msg_to_remote(rsp_msg)
                    return action

            msg = f"DM successfully set capability {capability} for Dish {dish_id}."
            rsp_msg = self._construct_rsp_to_tm(status=tm_dm.STATUS_SUCCESS, message=msg, api_msg=api_msg, api_call=api_call)
            action.set_msg_to_remote(rsp_msg)
            return action
  
        # If the Telescope Manager API call is to set a new target for the dish
        if api_call.get('action_code','') == 'set' and api_call.get('property','') == tm_dm.PROPERTY_TARGET:

            # Retrieve the target model and unique target identifier from the API call
            target = TargetModel.from_dict(api_call['value']) if isinstance(api_call.get('value'), dict) else None
            target_id = target.obs_id + f"-{target.tgt_idx}" if target is not None else None

            target_acquired = False

            # Prevent concurrent access to the dish driver
            with dish_lock:
                try:
                    # If no target is provided, clear the current target and set dish to STANDBY mode
                    if target is None or target_id is None:
                        dish_driver.clear_target_tuple()
                        dish_driver.set_dish_mode(DishMode.STANDBY_FP)

                    # Else if a valid target is provided, set the new target and set dish to OPERATE mode (it will initiate slewing if necessary) 
                    elif target is not None and target_id is not None:
                        dish_driver.set_target_tuple(target_id, target)
                        dish_driver.set_dish_mode(DishMode.OPERATE)
                        # If the dish is already on target, we can indicate that in the response to TM so the OET workflow can be optimized accordingly 
                        target_acquired = dish_driver.get_pointing_state() == PointingState.READY

                    else:
                        raise XSoftwareFailure(f"Invalid target provided to set for dish {dish_id}\n{api_call}")

                except XBase as e:
                    msg = f"DM failed to set target id {target_id if target_id is not None else 'None'} in observation " \
                     f"{target.obs_id if target is not None else 'None' } for Dish {dish_id}: {e}"

                    logger.error(msg + f"\n{target.to_dict() if target is not None else 'No Target'}")
                    rsp_msg = self._construct_rsp_to_tm(status=tm_dm.STATUS_ERROR, message=msg, api_msg=api_msg, api_call=api_call)
                    action.set_msg_to_remote(rsp_msg)
                    dish_driver.clear_target_tuple()
                    return action

            msg = f"DM set target {target_id if target_id is not None else 'None'} for Dish {dish_id}."
            logger.info(msg + f"\n{target.to_dict() if target is not None else 'No Target'}")
            rsp_msg = self._construct_rsp_to_tm(
                status=tm_dm.STATUS_SUCCESS,
                message=msg,
                api_msg=api_msg,
                api_call=api_call,
                include_obs_data=target_acquired,
            )
            action.set_msg_to_remote(rsp_msg)

        return action

    def process_ws_connected(self, event) -> Action:
        """ Processes Weather Station connected events.
        """
        logger.info(f"DM connected to Weather Station: {event.remote_addr}")
        self.dm_model.ws_connected = CommunicationStatus.ESTABLISHED
        
        action = Action()
        return action

    def process_ws_disconnected(self, event) -> Action:
        """ Processes Weather Station disconnected events.
        """
        logger.info(f"DM disconnected from Weather Station: {event.remote_addr}")
        self.dm_model.ws_connected = CommunicationStatus.NOT_ESTABLISHED
        
        action = Action()
        return action

    def process_ws_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes messages received on the Weather Station service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.debug(f"DM received Weather Station msg, action code: {api_call['msg_type']}")

        action = Action()

        # For demonstration, we will just log the weather station data received and include it in the status update to TM
        weather = api_call.get('value', None)

        weather = WeatherData.from_dict(api_call['value']) if api_call['value'] is not None and isinstance(api_call['value'], dict) else None
        self.dm_model.weather_store.append(weather) if weather is not None else None
        return action

    def _process_weather_alarm(self, action: Action) -> Action:
        """ Processes weather alarm events triggered by the Weather Station model when a weather threshold is breached.
        """

        if not self.dm_model.weather_store.is_ws_monitoring_enabled():
            return action

        prev_alarm_status = self.alarm_triggered
        self.alarm_triggered = True

        # For each dish driver, set the dish to STOW mode for safety if not already in STOW
        for dish_id, dish_driver in self.dish_drivers.items():

            # If the dish is already in STOW mode (or other mode that prevents transitioning to STOW), skip setting to STOW
            if dish_driver.get_mode() in [DishMode.STOW, DishMode.MAINTENANCE, DishMode.SHUTDOWN, DishMode.STARTUP]:
                continue
            
            # If the dish does not have an operational capability, skip setting to STOW
            if dish_driver.get_capability() not in [Capability.OPERATE_FULL, Capability.OPERATE_DEGRADED]:
                continue

            dish_lock = self._get_dish_lock(dish_id)
            with dish_lock:
                try:
                    dish_driver.set_weather_alarm(True)
                    dish_driver.set_dish_mode(DishMode.STOW)
                except XBase as e:
                    logger.error(f"DM failed to set STOW mode for Dish {dish_id} on weather alarm: {e}")

        # If the weather alarm status has just transitioned to True, then inform the Telescope Manager
        if not prev_alarm_status and self.alarm_triggered:
            self.dm_model.weather_store.trigger_dt = datetime.now(timezone.utc)
            self._log_alarm_event(event_type="transition", active=True)
            self._send_status_adv_to_tm(action=action, message="Dish Manager weather alarm threshold breached")
        
        return action

    def _revert_weather_alarm(self, action: Action) -> Action:
        """ Reverts weather alarm state when weather conditions return to safe levels.
        """

        if not self.dm_model.weather_store.is_ws_monitoring_enabled():
            return action

        prev_alarm_status = self.alarm_triggered

        # For each dish driver, revert the weather alarm state to False
        for dish_id, dish_driver in self.dish_drivers.items():

            if dish_driver.get_weather_alarm() == False:
                continue

            dish_lock = self._get_dish_lock(dish_id)
            with dish_lock:
                try:
                    dish_driver.set_weather_alarm(False)
                    dish_driver.set_dish_mode(DishMode.STANDBY_FP)
                except XBase as e:
                    logger.error(f"DM failed to revert weather alarm state for Dish {dish_id}: {e}")

        self.alarm_triggered = False
        if prev_alarm_status and not self.alarm_triggered:
            self._log_alarm_event(event_type="transition", active=False)
            self._send_status_adv_to_tm(action=action, message="Dish Manager weather alarm cleared, conditions back to safe levels")

        return action

    def process_timer_event(self, event) -> Action:
        """ Processes timer events.
        """
        logger.debug(f"DM timer event: {event}")

        action = Action()

        # Handle a driver timer e.g. driver_timer_dsh001_MD01Driver
        if "driver_timer" in event.name:

            # Extract dish id from the timer event name
            dish_id = event.name.split("_")[2]
            dish_driver = self.dish_drivers.get(dish_id, None) if dish_id is not None else None
            dish_lock = self._get_dish_lock(dish_id) if dish_id is not None else None

            if dish_driver is None or dish_lock is None:
                raise XSoftwareFailure(f"DM driver timer event {event.name} for dish id {dish_id} without driver instance or lock\n{event}")

            # If weather station monitoring is enabled
            if self.dm_model.weather_store.is_ws_monitoring_enabled():

                # If a weather threshold has been breached, process the weather alarm
                if self.dm_model.weather_store.alarm():
                    self._process_weather_alarm(action) # Set dish to STOW mode if a weather threshold is breached
                else:
                    self._revert_weather_alarm(action)  # Revert to STANDBY_FP mode if weather thresholds are no longer breached

            with dish_lock:

                # Retrieve the target model and unique target identifier from the driver
                target_id, target = dish_driver.get_target_tuple()

                # Get latest AltAz from the dish driver called regardless of the current pointing state or dish mode
                try:
                    dish_driver.get_current_altaz()
                except XBase as e:
                    logger.error(f"DM failed to get current AltAz for Dish {dish_id}: {e}")

                # Review dish health state to determine if action is needed
                if dish_driver.get_health_state() == HealthState.FAILED:

                    self._send_status_adv_to_tm(
                        action=action,
                        target_id=target_id,
                        target=target,
                        status=tm_dm.STATUS_ERROR,
                        message=f"Dish {dish_id} health state is FAILED",
                    )

                    # Tone down the driver poll rate to once per minute to reduce log spam until the issue is resolved
                    action.set_timer_action(
                        Action.Timer(
                            name=f"driver_timer_{dish_id}_{type(dish_driver).__name__}",
                            timer_action=60000,
                        )
                    )
                    return action

                # If the dish pointing state transitioned to READY, it means we have reached the desired slew position.
                # Drift scans do not slew to a new position, so suppress READY status spam for active DRIFT_SCAN targets.
                # Pointing state would be SLEW if still slewing or TRACK if already tracking (if necessary).
                if (target is not None and dish_driver.get_pointing_state() == PointingState.READY and target.pointing != PointingType.DRIFT_SCAN):
                    logger.debug(f"DM reached slew target and is now in READY state for target {target} acquisition in observation {target.obs_id} with Dish {dish_id}.")

                    status = tm_dm.STATUS_SUCCESS
                    msg = f"Dish {dish_id} reached slew target and is now in READY state for target {target_id} acquisition in observation {target.obs_id}."

                    # If we need to track the target, tell the driver to track to it
                    if target.pointing in [PointingType.SIDEREAL_TRACK, PointingType.NON_SIDEREAL_TRACK]:                         
                        try:
                            dish_driver.track()
                        except XBase as e:
                            status = tm_dm.STATUS_ERROR
                            msg = f"DM failed to track for Dish {dish_id} to target {target_id} in observation {target.obs_id}: {e}"
                            logger.error(msg)

                    # Else if we are doing an offset or five point scan, tell the driver to scan it
                    elif target.pointing in [PointingType.OFFSET_SCAN, PointingType.FIVE_POINT_SCAN]:
                        target.start_scan()
                        try:
                            dish_driver.scan()
                        except XBase as e:
                            status = tm_dm.STATUS_ERROR
                            msg = f"DM failed to scan for Dish {dish_id} for target {target_id} in observation {target.obs_id}: {e}"
                            logger.error(msg)

                    self._send_status_adv_to_tm(action, target_id, target, status, msg)

                elif target is not None and dish_driver.get_pointing_state() == PointingState.TRACK:                     
                    try:
                        dish_driver.track()  # Continue tracking the target
                    except XBase as e:
                        msg = f"DM failed to track for Dish {dish_id} to target {target_id} in observation {target.obs_id}: {e}"
                        logger.error(msg)
                        self._send_status_adv_to_tm(action, target_id, target, tm_dm.STATUS_ERROR, msg)

                elif target is not None and dish_driver.get_pointing_state() == PointingState.SCAN:                     
                    try:
                        dish_driver.scan()  # Continue scanning the target
                    except XBase as e:
                        msg = f"DM failed to scan for Dish {dish_id} for target {target_id} in observation {target.obs_id}: {e}"
                        logger.error(msg)
                        self._send_status_adv_to_tm(action, target_id, target, tm_dm.STATUS_ERROR, msg)

        # Restart the driver timer for the dish    
        action.set_timer_action(Action.Timer(
            name=f"driver_timer_{dish_id}_{type(dish_driver).__name__}", 
            timer_action=dish_driver.get_poll_interval_ms())) 
       
        return action

    def process_status_event(self, event) -> Action:
        """ Processes status update events.
        """
        self.get_app_processor_state()
        self._refresh_weather_alarm_metrics()

        action = self._send_status_adv_to_tm()
        return action

    def get_health_state(self) -> HealthState:
        """ Returns the current health state of this application.
        """
        if self.dm_model.tm_connected != CommunicationStatus.ESTABLISHED:
            return HealthState.DEGRADED
        else:
            return HealthState.OK

    def _construct_status_adv_to_tm(self, status=None, message=None) -> APIMessage:
        """ Constructs a status advice message for the Telescope Manager.
        """
        tm_adv = APIMessage(api_version=self.tm_api.get_api_version())
        dm_status = self.dm_model.to_dict()

        # Keep retained weather samples local to DM and send only compact rolling summaries to TM.
        weather_store = dm_status.get("weather_store")
        if isinstance(weather_store, dict):
            weather_store["weather_data"] = []

        tm_adv.set_json_api_header(
            api_version=self.tm_api.get_api_version(), 
            dt=datetime.now(timezone.utc), 
            from_system=self.dm_model.app.app_name, 
            to_system="tm", 
            api_call={
                "msg_type": "adv", 
                "action_code": "set", 
                "property": tm_dm.PROPERTY_STATUS, 
                "value": dm_status, 
                "status": tm_dm.STATUS_SUCCESS if status is None else status,
                "message": "DM status update" if message is None else message
            })
        return tm_adv

    def _send_status_adv_to_tm(self, action=None, target_id=None, target=None, status=None, message=None) -> Action:
        """ Sends a status advice message to the Telescope Manager if connected.
        """
        action = Action() if action is None else action

        if self.dm_model.tm_connected == CommunicationStatus.ESTABLISHED:

            tm_adv = self._construct_status_adv_to_tm(status=status, message=message)

            # Setting the Obs ID will trigger the Observation Execution Tool to review the observation state
            if target is not None and target_id is not None:
                api_call = tm_adv.get_api_call()
                api_call['obs_data'] = {'obs_id': target.obs_id, 'target_id': target_id}

            action.set_msg_to_remote(tm_adv)
            
        return action

    def _construct_rsp_to_tm(self, status, message, api_msg: dict, api_call: dict, include_obs_data: bool = False) -> APIMessage:
        """ Constructs a response message to the Telescope Manager.
        """
        # Prepare rsp msg to tm containing result of an api call
        tm_rsp = APIMessage(api_msg=api_msg, api_version=self.tm_api.get_api_version())

        tm_rsp.switch_from_to()
        tm_rsp_api_call = {
            "msg_type": "rsp", 
            "action_code": api_call['action_code'], 
            "status": status, 
        }
        if api_call.get('property') is not None:
            tm_rsp_api_call["property"] = api_call['property']

        if api_call.get('value') is not None:
            tm_rsp_api_call["value"] = api_call['value']

        # Exclude obs_data on successful set-target unless the dish is already on target.
        # In the usual case set-target leads to a slew/track/scan acquisition and a later status update
        # is used to trigger the TM/OET workflow review. If no acquisition is needed, keep obs_data here.
        if status == tm_dm.STATUS_ERROR or api_call.get('property') != tm_dm.PROPERTY_TARGET or include_obs_data:
            tm_rsp_api_call["obs_data"] = api_call['obs_data']

        if message is not None:
            tm_rsp_api_call["message"] = message

        tm_rsp.set_api_call(tm_rsp_api_call)  
        return tm_rsp

    def _refresh_weather_alarm_metrics(self):
        """Refresh rolling weather alarm response metrics at most once per hour."""
        now = datetime.now(timezone.utc)
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        if self.last_alarm_metrics_refresh_hour == current_hour:
            return

        metrics = get_alarm_rsp_efficiency(
            log_dir=str(Path(App.logs_dir).expanduser() / "alarm"),
            log_name=self.app_model.app_name,
            start_period=now - timedelta(days=30),
            end_period=now,
        )

        self.dm_model.weather_store.last_mth_alarm_count = metrics["alarm_count"]
        self.dm_model.weather_store.last_mth_alarm_activated = metrics["downtime_sec"] / 60.0
        self.dm_model.weather_store.last_mth_alarm_deactivated = metrics["uptime_sec"] / 60.0
        self.dm_model.weather_store.last_mth_alarm_mtta = metrics["mean_time_to_alarm_sec"] / 60.0
        self.dm_model.weather_store.last_mth_alarm_mttr = metrics["mean_time_to_recovery_sec"] / 60.0

        self.last_alarm_metrics_refresh_hour = current_hour

    def get_alarm_logger(self) -> logging.Logger:
        """Get a dedicated rotating logger for weather alarm transitions."""
        log_dir = Path(App.logs_dir).expanduser() / "alarm"
        log_dir.mkdir(parents=True, exist_ok=True)

        logger_name = f"{self.app_model.app_name}.alarm"
        alarm_logger = logging.getLogger(logger_name)
        alarm_logger.setLevel(logging.INFO)
        alarm_logger.propagate = False
        alarm_logger.handlers.clear()

        handler = TimedRotatingFileHandler(
            filename=os.path.join(log_dir, f"{self.app_model.app_name}.log"),
            when="midnight",
            interval=1,
            backupCount=731,
            encoding="utf-8",
            utc=True,
        )
        handler.suffix = "%Y-%m-%d"
        formatter = logging.Formatter("%(asctime)s UTC | %(levelname)s | %(message)s")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)
        alarm_logger.addHandler(handler)
        return alarm_logger

    def _log_alarm_event(self, event_type: str, active: bool = None, reason: str = None):
        """Log a weather alarm event and current alarm metrics.

        Parameters:
            event_type: ``transition`` or ``snapshot``.
            active: Explicit alarm state for transition events.
            reason: Optional reason label for snapshot events, e.g. ``startup``.
        """
        if event_type == "transition":
            state = "ACTIVE" if active else "CLEAR"
            self.alarm_logger.info(f"WeatherAlarm transition state={state}")
        elif event_type == "snapshot":
            metrics = self.dm_model.weather_store.get_alarm_metrics()
            state = "ACTIVE" if metrics["alarm_triggered"] else "CLEAR"
            snapshot_reason = reason or "snapshot"
            self.alarm_logger.info(f"WeatherAlarm snapshot reason={snapshot_reason} state={state}")
        else:
            raise ValueError(f"Unsupported weather alarm event type: {event_type}")

        self.alarm_logger.info(
            "WeatherAlarm metrics " + self.dm_model.weather_store.format_alarm_metrics()
        )

# Runs tests: pytest dsh/dm.py -v -s 
# -v for verbose output (or -vv or -vvv for more verbosity)
# -s to show print output

def test_get_desired_altaz(dm, md01_driver):
    
    # Test sidereal target
    target_sidereal = TargetModel(
        id="sidereal001",
        pointing=PointingType.SIDEREAL_TRACK,
        sky_coord=SkyCoord(ra=180.0*u.deg, dec=45.0*u.deg, frame='icrs')
    )
    altaz_sidereal = dm._get_desired_altaz(target_sidereal, md01_driver)
    print(altaz_sidereal)
    assert hasattr(altaz_sidereal, 'alt') and hasattr(altaz_sidereal, 'az')
 
    # Test non-sidereal target
    target_nonsidereal = TargetModel(
        id="mars",
        pointing=PointingType.NON_SIDEREAL_TRACK
    )
    altaz_nonsidereal = dm._get_desired_altaz(target_nonsidereal, md01_driver)
    print(altaz_nonsidereal)
    assert hasattr(altaz_nonsidereal, 'alt') and hasattr(altaz_nonsidereal, 'az')
 
    # Test drift scan target
    target_drift = TargetModel(
        id="drift001",
        pointing=PointingType.DRIFT_SCAN,
        altaz=AltAz(alt=30.0*u.deg, az=150.0*u.deg)
    )
    altaz_drift = dm._get_desired_altaz(target_drift, md01_driver)
    print(altaz_drift)
    assert hasattr(altaz_drift, 'alt') and hasattr(altaz_drift, 'az')      

def main():
    dm = DM()
    dm.start()

    if dm.is_headless():
        logger.info("Dish Manager running in headless mode; skipping dish and weather displays.")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            dm.stop()
        return

    display_period_sec = 1.0

    try:
        while True:
            time_start = time.monotonic()

            for dish_id, dish_driver in dm.dish_drivers.items():

                # If there is no signal display for this digitiser, create a new active signal display
                if dish_id not in dm.dish_displays or dm.dish_displays[dish_id] is None:
                    logger.info(f"Dish Manager creating new DishDisplay for dish {dish_id}")
                    dm.dish_displays[dish_id] = DishDisplay(driver=dish_driver)

                if not (dm.dish_displays[dish_id].get_is_active()):
                    continue # Dish display for dish has been deactivated, continue to next dish

                dm.dish_displays[dish_id].display()

            for ws_id in dm.dm_model.weather_store.get_station_ids():

                if ws_id not in dm.weather_displays or dm.weather_displays[ws_id] is None:
                    logger.info(f"Dish Manager creating new WeatherDisplay for weather station {ws_id}")
                    dm.weather_displays[ws_id] = WeatherDisplay(weather_store=dm.dm_model.weather_store, ws_id=ws_id)

                if not dm.weather_displays[ws_id].get_is_active():
                    continue

                dm.weather_displays[ws_id].display()

            # Adjust display period up or down based on processing time
            time_elapsed = time.monotonic() - time_start

            if time_elapsed > display_period_sec:
                display_period_sec += 1.0 
                logger.warning(f"DM dish display loop took {time_elapsed:.3f} seconds to execute, extending display period to {display_period_sec} seconds")
            elif time_elapsed < display_period_sec - 2.0:
                display_period_sec = max(1.0, display_period_sec - 2.0)
                logger.info(f"DM dish display loop took {time_elapsed:.3f} seconds to execute, shortening display period to {display_period_sec} seconds")

            time.sleep(max(0.0, display_period_sec - time_elapsed)) # Update on an approximately 1 second cadence
                
    except KeyboardInterrupt:
        pass
    finally:
        dm.stop()

if __name__ == "__main__":
    main()
