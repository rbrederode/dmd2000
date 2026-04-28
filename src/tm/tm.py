import logging
import json
import map
import os
import re
from pathlib import Path
import socket
import time
import threading
from datetime import datetime, timezone, timedelta

# Import google api tools
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

# Import application modules
from api import tm_dig, tm_sdp, tm_dm, tm_ws
from env.app import App
from env.events import ConnectEvent, DisconnectEvent, DataEvent, ConfigEvent, ObsEvent
from ipc.message import AppMessage, APIMessage
from ipc.action import Action
from ipc.tcp_client import TCPClient
from ipc.tcp_server import TCPServer
from models.app import AppModel
from models.base import BaseModel
from models.comms import CommunicationStatus, InterfaceType
from models.dig import DigitiserModel, LoadState
from models.dsh import DishManagerModel, Feed, Capability, DishMode, PointingState
from models.obs import ObsModel, ObsTransition, ObsState
from models.oda import ODAModel, ObsList, ScanStore
from models.health import HealthState
from models.scan import ScanModel, ScanState
from models.sdp import ScienceDataProcessorModel
from models.target import TargetModel
from models.telescope import TelescopeModel
from models.tm import ResourceType, AllocationState, ResourceAllocations, Allocation
from models.ui import UIDriver, UIDriverType
from models.ws import WeatherStationModel, WeatherSummary
from obs.oet import ObservationExecutionTool
from util import log, util
from util.timer import Timer, TimerManager
from util.xbase import XBase, XStreamUnableToExtract, XUnknownEntity, XAPIValidationFailed, XSoftwareFailure
from webhook_handler import WebhookHandler

logger = logging.getLogger(__name__)

class TelescopeManager(App):

    AUTO_GAIN_TIMEOUT_MS = 30000

    telmodel = TelescopeModel()

    def __init__(self, app_name: str = "tm"):

        super().__init__(app_name=app_name, app_model=self.telmodel.tel_mgr.app)

        # Lock for thread-safe allocation of shared resources
        self._rlock = threading.RLock()  

        # When TM is started with -o/--observation_file we temporarily treat that
        # observation definition as the authoritative ODT source until it completes.
        self._startup_odt_protection_enabled = False
        self._startup_odt_pending_config = None
        self._startup_odt_protected_obs_ids = set()

        # Observation Execution Tool is an internal component of the TM used to manage observation workflows
        self.oet = ObservationExecutionTool(telmodel=self.telmodel, tm=self)

        # Dish Manager interface
        self.dm_system = "dm"
        self.dm_api = tm_dm.TM_DM()
        # Dish Manager TCP Client
        self.dm_endpoint = TCPClient(description=self.dm_system, queue=self.get_queue(), host=self.get_args().dm_host, port=self.get_args().dm_port)
        # Register Dish Manager interface with the App
        self.register_interface(self.dm_system, self.dm_api, self.dm_endpoint, InterfaceType.APP_APP)
        # Initialise Dish Manager comms status
        self.telmodel.dsh_mgr.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        self.telmodel.tel_mgr.dm_connected = CommunicationStatus.NOT_ESTABLISHED

        # Digitiser interface
        self.dig_system = "dig"
        self.dig_api = tm_dig.TM_DIG()
        # Digitiser TCP Server
        self.dig_endpoint = TCPServer(description=self.dig_system, queue=self.get_queue(), host=self.get_args().dig_host, port=self.get_args().dig_port)
        # Register Digitiser interface with the App
        self.register_interface(self.dig_system, self.dig_api, self.dig_endpoint, InterfaceType.ENTITY_DRIVER)
        # Entity drivers maintain comms status per entity, so no need to initialise comms status here
        
        # Science Data Processor interface 
        self.sdp_system = "sdp"
        self.sdp_api = tm_sdp.TM_SDP()
        # Science Data Processor TCP Client
        self.sdp_endpoint = TCPClient(description=self.sdp_system, queue=self.get_queue(), host=self.get_args().sdp_host, port=self.get_args().sdp_port)
        # Register Science Data Processor interface with the App
        self.register_interface(self.sdp_system, self.sdp_api, self.sdp_endpoint, InterfaceType.APP_APP)
        # Initialise Science Data Processor comms status
        self.telmodel.sdp.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        self.telmodel.tel_mgr.sdp_connected = CommunicationStatus.NOT_ESTABLISHED

        # Weather Station interface 
        self.ws_system = "ws"
        self.ws_api = tm_ws.TM_WS()
        # Weather Station TCP Client
        self.ws_endpoint = TCPClient(description=self.ws_system, queue=self.get_queue(), host=self.get_args().ws_host, port=self.get_args().ws_port)
        # Register Weather Station interface with the App
        self.register_interface(self.ws_system, self.ws_api, self.ws_endpoint, InterfaceType.APP_APP)
        # Initialise Weather Station comms status
        self.telmodel.wtr_stn.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        self.telmodel.tel_mgr.ws_connected = CommunicationStatus.NOT_ESTABLISHED

    def add_args(self, arg_parser): 
        """ Specifies the digitiser's command line arguments.
        """
        super().add_args(arg_parser)

        arg_parser.add_argument("--dig_host", type=str, required=False, help="TCP server host to listen for Digitiser connections", default="localhost")
        arg_parser.add_argument("--dig_port", type=int, required=False, help="TCP server port to listen for Digitiser connections", default=50000) 

        arg_parser.add_argument("--sdp_host", type=str, required=False, help="TCP server host to connect to the Science Data Processor",default="localhost")
        arg_parser.add_argument("--sdp_port", type=int, required=False, help="TCP server port to connect to the Science Data Processor", default=50001)

        arg_parser.add_argument("--dm_host", type=str, required=False, help="TCP server host to connect to the Dish Manager", default="localhost")
        arg_parser.add_argument("--dm_port", type=int, required=False, help="TCP server port to connect to the Dish Manager", default=50002) 

        arg_parser.add_argument("--ws_host", type=str, required=False, help="TCP server host to connect to the Weather Station", default="localhost")
        arg_parser.add_argument("--ws_port", type=int, required=False, help="TCP server port to connect to the Weather Station", default=50003) 
        arg_parser.add_argument("--observation_file", "-o", dest="observation_file", type=str, required=False,
            help="Path to an observation definition JSON file to inject as an ODT config event at startup",
        )

    def process_init(self) -> Action:
        """ Processes initialisation event on startup once all app processors are running.
            Runs in single threaded mode and switches to multi-threading mode after this method completes.
        """
        logger.debug(f"TM initialisation event")

        # Config files located in ./config/<profile>/<model>.json
        input_dir = f"./config/{self.get_args().profile}"

        # Load Telescope Manager configuration from disk
        filename = "TelescopeManagerModel.json"

        try:
            tm = self.telmodel.tel_mgr.load_from_disk(input_dir=input_dir, filename=filename)
        except FileNotFoundError:
            tm = None

        if tm is not None:
            self.telmodel.tel_mgr.ui_drivers = tm.ui_drivers if tm.ui_drivers is not None else []
            logger.info(f"Telescope Manager loaded TM configuration from directory {input_dir} file {filename}")
        else:
            logger.warning(f"Telescope Manager could not load TM configuration from directory {input_dir} file {filename}")

        # Load Digitiser configuration from disk
        # Config file defines initial list of digitisers to be processed by the TM
        filename = "DigitiserList.json"

        try:
            dig_store = self.telmodel.dig_store.load_from_disk(input_dir=input_dir, filename=filename)
        except FileNotFoundError:
            dig_store = None

        if dig_store is not None:
            self.telmodel.dig_store = dig_store
            logger.info(f"Telescope Manager loaded Digitiser configuration from directory {input_dir} file {filename}")
        else:
            logger.warning(f"Telescope Manager could not load Digitiser configuration from directory {input_dir} file {filename}")

        action = Action()

        # Start server endpoints and connect client endpoints to interfaces
        self.dm_endpoint.connect()
        self.dig_endpoint.start()
        self.sdp_endpoint.connect()
        self.ws_endpoint.connect()

        # If an observation file was specified on startup, load the obs definition file and inject a config event 
        observation_file = getattr(self.get_args(), "observation_file", None)
        if observation_file:
            odt_config = ObsList.from_disk(observation_file).to_dict()
            self._startup_odt_protection_enabled = True
            self._startup_odt_pending_config = odt_config
            self._startup_odt_protected_obs_ids.clear()
            config_event = ConfigEvent(
                category="ODT",
                old_config=None,
                new_config=odt_config,
                timestamp=datetime.now(timezone.utc),
            )
            self.get_queue().put(config_event)
            logger.info(
                f"Telescope Manager injected startup ODT config event from observation file "
                f"{Path(observation_file).expanduser()}"
            )

        return action

    def process_config(self, event: ConfigEvent) -> Action:
        """ Processes configuration update events.
        """
        logger.info(f"Telescope Manager received updated configuration: {event}")

        action = Action()

        if event.category.upper() == "DIG": # Digitiser Config Event

            # Identify the digitiser related to this configuration update
            dig_id = event.new_config.get("dig_id", None) if event.new_config is not None else None
            if dig_id is None:
                logger.error(f"Telescope Manager received digitiser configuration update with no digitiser ID specified in the new configuration: {event.new_config}")
                return action
            
            # Extract DIG specific properties (all properties except Scan Duration and Channels)
            old_dig_config = {k: v for k, v in (event.old_config or {}).items() 
                if k not in (tm_sdp.PROPERTY_SCAN_DURATION, tm_sdp.PROPERTY_CHANNELS)}
            new_dig_config = {k: v for k, v in (event.new_config or {}).items() 
                if k not in (tm_sdp.PROPERTY_SCAN_DURATION, tm_sdp.PROPERTY_CHANNELS)}

            # Update the digitiser configuration based on the received config event and trigger any necessary actions
            action = self.update_dig_configuration(old_dig_config, new_dig_config, action)

            # Extract SDP scan specific properties (all properties except Frequency Correction)
            # Scanning will be prepared seperately based on the scanning property in the DIG config
            old_scan_config = {k: v for k, v in (event.old_config or {}).items() 
                if k not in (tm_dig.PROPERTY_FREQ_CORRECTION, tm_dig.PROPERTY_SCANNING)}
            new_scan_config = {k: v for k, v in (event.new_config or {}).items() 
                if k not in (tm_dig.PROPERTY_FREQ_CORRECTION, tm_dig.PROPERTY_SCANNING)}
            
            dish = self.telmodel.dsh_mgr.get_dish_by_dig_id(dig_id)
            dsh_id = dish.dsh_id if dish is not None else None

            dig_scanning = event.new_config.get(tm_dig.PROPERTY_SCANNING, None) if event.new_config is not None else None    
            scanning = map.get_property_name_value(tm_dig.PROPERTY_SCANNING, dig_scanning)[1] if dig_scanning is not None else None

            # Generate an observation ID flagged as user-initiated (USR) for this scan based on the current datetime and dish/digitiser id
            obs_id = f"USR-{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%MZ')}-" + f"{dsh_id if dsh_id is not None else dig_id}"
            new_scan_config["obs_id"] = obs_id

            # If scanning is turned on we need to provide the observation id and tgt_idx and freq_scan
            new_scan_config["scanning"] = {'obs_id': obs_id, 'tgt_idx': 0, 'freq_scan': 0} if scanning else scanning

            # SDP scan_config expects a concrete numeric gain. When gain is AUTO, defer the
            # gain update until the digitiser returns a resolved gain value.
            if str(new_scan_config.get("gain", "")).upper() == "AUTO":
                logger.info(
                    f"Telescope Manager deferring SDP scan_config gain update for digitiser {dig_id} "
                    "until auto gain is resolved."
                )
                new_scan_config.pop("gain", None)
            
            old_sdp_config = {}
            new_sdp_config = {}
   
            new_sdp_config['scan_config'] = new_scan_config
            new_sdp_config['sdp_id'] = self.telmodel.sdp.sdp_id

            # Update the SDP configuration based on the received config event and trigger any necessary actions
            action = self.update_sdp_configuration(old_sdp_config, new_sdp_config, action) 

        elif event.category.upper() == "DSH": # Scheduler Config Event
            action = self.update_dsh_configuration(event.old_config, event.new_config, action)

        elif event.category.upper() == "ODT": # Observation Design Tool Config Event
            # Observation Design Tool (ODT) is the source of truth for new (ObsState = EMPTY) observations
            # Observation Data Archive (ODA) is the source of truth for in progress (ObsState != EMPTY) observations

            is_startup_odt_event = self._is_startup_odt_event(event)
            if self._startup_odt_protection_enabled and not is_startup_odt_event:
                logger.info(
                    "Ignoring external ODT configuration update because the startup "
                    "-o observation still has precedence."
                )
                return action

            # Extract a list of ObsState = EMPTY observations from the incoming ODT configuration event (JSON)
            odt = ObsList.from_dict(event.new_config)
            odt_empty_obs = [obs for obs in odt.obs_list if obs.obs_state == ObsState.EMPTY]
            startup_protected_obs_ids = set()
            
            # Create dictionary of EMPTY ODT observation ids for quick lookup
            odt_empty_obs_dict = {obs.obs_id: obs for obs in odt_empty_obs}
            odt_empty_obs_ids = set(odt_empty_obs_dict.keys())

            logger.info(f"Received {len(odt.obs_list)} ODT observations, with {len(odt_empty_obs)} in ObsState.EMPTY")
            
            # Iterate through existing ODA observations and update/remove EMPTY observations as needed
            for i, existing_obs in enumerate(self.telmodel.oda.obs_store.obs_list):

                if existing_obs.obs_state == ObsState.EMPTY:
                    
                    if existing_obs.obs_id in odt_empty_obs_dict:
                        # Update existing EMPTY observations in the ODA with new data from ODT
                        logger.info(f"Updating existing EMPTY observation {existing_obs.obs_id} with new data from ODT")
                        self.telmodel.oda.obs_store.obs_list[i] = odt_empty_obs_dict[existing_obs.obs_id]
                        if is_startup_odt_event:
                            startup_protected_obs_ids.add(odt_empty_obs_dict[existing_obs.obs_id].obs_id)
                    else: 
                        # Remove EMPTY observations from ODA that are no longer in ODT
                        logger.info(f"Removing existing EMPTY observation {existing_obs.obs_id} as it is no longer present in ODT")
                        obs = self.telmodel.oda.obs_store.obs_list.pop(i)
                                                   
            # Add new EMPTY observations from ODT to ODA
            for odt_obs in odt_empty_obs:
                if not any(existing_obs.obs_id == odt_obs.obs_id for existing_obs in self.telmodel.oda.obs_store.obs_list):
                    logger.info(f"Adding new observation {odt_obs.obs_id} from ODT to ODA")

                    # START DEBUG CODE, REMOVE LATER
                    current_dt_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")                
                    new_obs_id = re.sub(r"^.*?(-Dish\d{3})", current_dt_str + r"\1", odt_obs.obs_id)
    
                    odt_obs.obs_id = new_obs_id
                    odt_obs.scheduling_block_start = datetime.now(timezone.utc) + timedelta(seconds=10)
                    odt_obs.scheduling_block_end = odt_obs.scheduling_block_start + timedelta(seconds=610)

                    # Inform the Science Data Processor that we are resetting these observations (in case they have been run already)
                    self.oet.reset_sdp_scan(obs=odt_obs, action=action)
                    # END DEBUG CODE, REMOVE LATER

                    self.telmodel.oda.obs_store.obs_list.append(odt_obs)
                    if is_startup_odt_event:
                        startup_protected_obs_ids.add(odt_obs.obs_id)

            if is_startup_odt_event:
                self._startup_odt_protected_obs_ids = startup_protected_obs_ids
                self._startup_odt_pending_config = None

            # Start timer to initiate the next scheduled observation if applicable
            self.oet.start_next_obs_timer(action)

        elif event.category.upper() == "OET": # Observation Execution Tool Event
            _type = event.new_config.get("_type", None) if event.new_config is not None else None

            if _type == "ObservationReset":
                obs_id = event.new_config.get("obs_id", None) if event.new_config is not None else None
                obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None
                logger.info(f"Reset observation requested for observation ID: {obs_id}\n{event.new_config}")
                
                # If the related observation was identified, trigger the workflow to move to ABORT
                if obs is not None:
                    action.set_obs_transition(obs=obs, transition=ObsTransition.RESET)
        else:
            logger.info(f"Telescope Manager updated configuration received for {event.category}.")

        return action

    def process_obs_event(self, event: ObsEvent) -> Action:
        """ Defer workflow transitions on observations to the Observation Execution Tool (OET).
            Returns an Action object with actions to be performed.
        """
        action = self.oet.process_obs_event(event)

        if (
            event.transition == ObsTransition.RELEASE_RESOURCES
            and event.obs is not None
            and event.obs.obs_id in self._startup_odt_protected_obs_ids
        ):
            self._startup_odt_protected_obs_ids.discard(event.obs.obs_id)
            logger.info(
                f"Startup -o observation {event.obs.obs_id} released resources; "
                "removing its ODT protection."
            )
            if not self._startup_odt_protected_obs_ids:
                self._disable_startup_odt_protection()

        return action

    def process_dm_connected(self, event) -> Action:
        """ Processes Dish Manager connected events.
        """
        logger.info(f"Telescope Manager connected to Dish Manager: {event.remote_addr}")

        self.telmodel.dsh_mgr.tm_connected = CommunicationStatus.ESTABLISHED
        self.telmodel.tel_mgr.dm_connected = CommunicationStatus.ESTABLISHED

        action = Action()
        return action

    def process_dm_disconnected(self, event) -> Action:
        """ Processes Dish Manager disconnected events.
        """
        logger.info(f"Telescope Manager disconnected from Dish Manager: {event.remote_addr}")

        self.telmodel.dsh_mgr.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        self.telmodel.tel_mgr.dm_connected = CommunicationStatus.NOT_ESTABLISHED

        return self.abort_observations()

    def process_dm_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Dish Manager service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"Telescope Manager received Dish Manager {api_call['msg_type']} msg, action code: {api_call['action_code']}, property: {api_call.get('property','')}")
        
        action = Action()

        # Extract datetime, dish id from API message and lookup Dish Model
        dt = api_msg.get("timestamp")
        dsh_id = api_msg.get("entity", None) 
        dsh_model = self.telmodel.dsh_mgr.get_dish_by_id(dsh_id) if dsh_id is not None and dsh_id != "" else None

        # If the Dish ID is specified in the API message but not found in the Dish Manager model, raise an exception
        if dsh_id is not None and dsh_model is None:
            raise XUnknownEntity(f"Telescope Manager received Dish Manager API message for unknown dish {dsh_id}.\n{api_call}")
        
        # If the api call indicates that an error occured
        if api_call.get('status','') != tm_dm.STATUS_SUCCESS:
            
            logger.error(f"Telescope Manager received error response from Dish Manager for dish {dsh_id}.\n{api_call}")

            # Not that the dsh_model can be None for some error messages (e.g. a status update message)
            if dsh_model is not None:
                dsh_model.mode = DishMode.UNKNOWN # Set dish mode to UNKNOWN to force safe recovery
                dsh_model.last_err_msg = api_call['message'] if 'message' in api_call else dsh_model.last_err_msg
                dsh_model.last_err_dt = datetime.fromisoformat(dt) if dt is not None else datetime.now(timezone.utc)
            
            # If the message contains additional observation data, trigger the observation workflow
            obs_data = api_call.get('obs_data', None)
            obs_id = obs_data.get('obs_id', None) if obs_data is not None and isinstance(obs_data, dict) else None
            obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None
                
            # If the related observation was identified, trigger the workflow to move to ABORT
            if obs is not None:
                action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)

        # If the api call does not indicate that an error occured
        elif api_call.get('status','') == tm_dm.STATUS_SUCCESS:

            # If the api call is a dish mode set property rsp message, update the Dish Model
            if api_call.get('property','') == tm_dm.PROPERTY_MODE:
                dsh_model.mode = DishMode(api_call['value']) if api_call['value'] is not None else None

            # If the api call is a capability state set property rsp message, update the Dish Model
            elif api_call.get('property','') == tm_dm.PROPERTY_CAPABILITY:
                dsh_model.capability = Capability(api_call['value']) if api_call['value'] is not None else None

            # If the api call is a capability state set property rsp message, update the Dish Model
            elif api_call.get('property','') == tm_dm.PROPERTY_TARGET:
                dsh_model.target = TargetModel.from_dict(api_call['value']) if api_call['value'] is not None and isinstance(api_call['value'], dict) else None
                dsh_model.tgt_id = dsh_model.target.obs_id + f"-{dsh_model.target.tgt_idx}" if dsh_model.target is not None else None
                
            # If the api call is a status update message, update the Dish Manager model
            elif api_call.get('property','') == tm_dm.PROPERTY_STATUS:
                logger.debug(f"Telescope Manager received Dish Manager STATUS update: {api_call['value']}")
                self.telmodel.dsh_mgr = DishManagerModel.from_dict(api_call['value']) if api_call['value'] is not None else None

                msg = api_call.get('message')
                if msg is not None and "weather alarm" in msg.lower():

                    # Abort observations if a dish weather alarm is active to allow for safe recovery 
                    active_dsh_alarms = [dsh for dsh in self.telmodel.dsh_mgr.dish_store.dish_list if dsh.weather_alarm == True]

                    for dsh in active_dsh_alarms:
                        obs = self.telmodel.oda.obs_store.get_obs_by_dsh_id(dsh.dsh_id)
                        if obs is not None:
                            logger.warning(f"Telescope Manager detected active weather alarm on dish {dsh.dsh_id} for observation {obs.obs_id}. Aborting affected observation for safe recovery.")
                            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)

            # If the status update message contains additional observation data, trigger the observation workflow
            obs_data = api_call.get('obs_data', None)
            obs_id = obs_data.get('obs_id', None) if obs_data is not None and isinstance(obs_data, dict) else None
            obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None
                    
            # If the observation is still in CONFIGURING state, trigger the workflow to attempt to move to READY
            if obs is not None and obs.obs_state == ObsState.CONFIGURING:
                logger.info(f"Telescope Manager received Dish Manager observation update{f' for observation {obs_id}' if obs_id is not None else ''}.")
                action.set_obs_transition(obs=obs, transition=ObsTransition.CONFIGURE_RESOURCES)    

            # Update the last update timestamp on the Dish Manager model
            self.telmodel.dsh_mgr.last_update = datetime.fromisoformat(dt) if dt else datetime.now(timezone.utc)

        # If the api call is a rsp message, stop the corresponding retry timers
        if api_call['msg_type'] == tm_dm.MSG_TYPE_RSP:
            if dt is not None and dsh_id is not None: # Do not set this to None (it breaks things !)
                action.set_timer_action(Action.Timer(name=f"{dsh_id}_req_timer_retry:{dt}", timer_action=Action.Timer.TIMER_STOP))
                action.set_timer_action(Action.Timer(name=f"{dsh_id}_req_timer_final:{dt}", timer_action=Action.Timer.TIMER_STOP))

        return action

    def process_ws_connected(self, event) -> Action:
        """ Processes Weather Station connected events.
        """
        logger.info(f"Telescope Manager connected to Weather Station: {event.remote_addr}")

        self.telmodel.wtr_stn.tm_connected = CommunicationStatus.ESTABLISHED
        self.telmodel.tel_mgr.ws_connected = CommunicationStatus.ESTABLISHED

        action = Action()
        return action

    def process_ws_disconnected(self, event) -> Action:
        """ Processes Weather Station disconnected events.
        """
        logger.info(f"Telescope Manager disconnected from Weather Station: {event.remote_addr}")

        self.telmodel.wtr_stn.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        self.telmodel.tel_mgr.ws_connected = CommunicationStatus.NOT_ESTABLISHED

        action = Action()
        return action

    def process_ws_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Weather Station service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"Telescope Manager received Weather Station {api_call['msg_type']} msg, action code: {api_call['action_code']}, property: {api_call.get('property','')}")
        
        action = Action()
    
        # Extract datetime, weather station id from API message
        dt = api_msg.get("timestamp")
        ws_id = api_msg.get("entity", None) 

        # If the api call indicates that an error occured
        if api_call.get('status','') == tm_ws.STATUS_ERROR:
            
            logger.error(f"Telescope Manager received error response from Weather Station for station {ws_id}.\n{api_call}")
            self.telmodel.wtr_stn.last_err_msg = api_call['message'] if 'message' in api_call else self.telmodel.wtr_stn.last_err_msg
            self.telmodel.wtr_stn.last_err_dt = datetime.fromisoformat(dt) if dt is not None else datetime.now(timezone.utc)

        # If the api call does not indicate that an error occured
        elif api_call.get('status','') != tm_dm.STATUS_ERROR:

              # If the api call is a status update message, update the Dish Manager model
            if api_call.get('property','') == tm_dm.PROPERTY_STATUS:
                logger.debug(f"Telescope Manager received Weather Station STATUS update: {api_call['value']}")
                self.telmodel.wtr_stn = WeatherStationModel.from_dict(api_call['value']) if api_call['value'] is not None else None

        # Update the last update timestamp on the Weather Station model
        self.telmodel.wtr_stn.last_update = datetime.fromisoformat(dt) if dt else datetime.now(timezone.utc)

        # If the api call is a rsp message, stop the corresponding retry timers
        if api_call['msg_type'] == tm_dm.MSG_TYPE_RSP:
            if dt is not None and ws_id is not None: # Do not set this to None (it breaks things !)
                action.set_timer_action(Action.Timer(name=f"{ws_id}_req_timer_retry:{dt}", timer_action=Action.Timer.TIMER_STOP))
                action.set_timer_action(Action.Timer(name=f"{ws_id}_req_timer_final:{dt}", timer_action=Action.Timer.TIMER_STOP))

        return action
  
    def get_dig_entity(self, event) -> (str, BaseModel):
        """ Determines the digitiser entity ID based on the remote address of a ConnectEvent, DisconnectEvent, or DataEvent.
            Returns a tuple of the entity ID and entity if found, else None, None.
        """
        remote_addr = event.remote_addr[0] if event.remote_addr else 'None'
        logger.debug(f"Telescope Manager finding digitiser entity ID for remote address: {remote_addr}")

        for digitiser in self.telmodel.dig_store.dig_list:

            if isinstance(digitiser.app.arguments, dict) and "local_host" in digitiser.app.arguments:

                if digitiser.app.arguments["local_host"] == remote_addr:
                    logger.debug(f"Telescope Manager found digitiser entity ID: {digitiser.dig_id} for remote address: {remote_addr}")
                    return digitiser.dig_id, digitiser
            else:
                logger.warning(f"Telescope Manager digitiser {digitiser.dig_id} is not configured with a valid local_host argument to match against remote address: {remote_addr}")

        return None, None

    def process_dig_entity_connected(self, event, entity) -> Action:
        """ Processes Digitiser connected events.
        """
        logger.info(f"Telescope Manager connected to Digitiser entity on {event.remote_addr}")
        digitiser: DigitiserModel = entity if entity is not None and isinstance(entity, DigitiserModel) else None

        action = Action()

        if digitiser is not None:
            digitiser.tm_connected = CommunicationStatus.ESTABLISHED
            digitiser.last_update = datetime.now(timezone.utc)

            # DIG does not load its static hardware config from disk locally, so push
            # the combined load relay state once on connect.
            action = self.update_dig_configuration(
                old_config={"dig_id": digitiser.dig_id},
                new_config={"dig_id": digitiser.dig_id, "load_state": digitiser.load_state.to_dict()},
                action=action,
            )

        return action

    def process_dig_entity_disconnected(self, event, entity) -> Action:
        """ Processes Digitiser disconnected events.
        """
        logger.info(f"Telescope Manager disconnected from Digitiser entity on {event.remote_addr}\n{entity}")
        digitiser: DigitiserModel = entity if entity is not None and isinstance(entity, DigitiserModel) else None

        if digitiser is not None:
            digitiser.tm_connected = CommunicationStatus.NOT_ESTABLISHED
            digitiser.last_update = datetime.now(timezone.utc)

            # Abort all ongoing observations that are using this digitiser
            return self.abort_observations(dig_id=digitiser.dig_id)

    def process_dig_entity_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray, entity: BaseModel) -> Action:
        """ Processes api messages received on the Digitiser service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"Telescope Manager received digitiser {api_call['msg_type']} msg, action code: {api_call['action_code']}, " + \
            f"property {api_call.get('property','')} on entity: {api_msg['entity']}")

        digitiser: DigitiserModel = entity

        # If the Digitiser entity could not be identified, raise an exception
        if digitiser is None:
            raise XUnknownEntity(f"Telescope Manager received Digitiser API message for unknown (None) digitiser.\n{api_call}")

        action = Action()

        dt = api_msg.get("timestamp")
        if dt is not None:
            # Stop the corresponding retry timers
            action.set_timer_action(Action.Timer(name=f"{digitiser.dig_id}_req_timer_retry:{dt}", timer_action=Action.Timer.TIMER_STOP))
            action.set_timer_action(Action.Timer(name=f"{digitiser.dig_id}_req_timer_final:{dt}", timer_action=Action.Timer.TIMER_STOP))

        # If the api call indicates that an error occured
        if api_call.get('status','') == tm_dig.STATUS_ERROR:
            
            logger.error(f"Telescope Manager received error response from Digitiser {digitiser.dig_id}.\n{api_call}")
            digitiser.last_err_msg = api_call['message'] if 'message' in api_call else digitiser.last_err_msg
            digitiser.last_err_dt = datetime.fromisoformat(dt) if dt is not None else datetime.now(timezone.utc)

            # If the message contains additional observation data, trigger the observation workflow
            obs_data = api_call.get('obs_data', None)
            obs_id = obs_data.get('obs_id', None) if obs_data is not None and isinstance(obs_data, dict) else None
            obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None
                
            # If the related observation was identified, trigger the workflow to move to ABORT
            if obs is not None:
                action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)

        # If the api call does not indicate that an error occured
        elif api_call.get('status','') != tm_dig.STATUS_ERROR:

            obs_data = api_call.get('obs_data', None)
            if obs_data is not None and isinstance(obs_data, dict):
                obs_data = dict(obs_data)
            obs_id = obs_data.get('obs_id', None) if obs_data is not None and isinstance(obs_data, dict) else None
            sdp_config_update_pending = False

            # If the api call is a successful method response, handle it separately from property updates
            if api_call.get('action_code') == tm_dig.ACTION_CODE_METHOD:
                method = api_call.get('method')

                if method in [tm_dig.METHOD_GET_AUTO_GAIN, tm_dig.METHOD_SET_AUTO_GAIN]:
                    gain_value = api_call.get('value')
                    if gain_value is not None:
                        digitiser.gain = float(gain_value)
                        logger.info(f"Telescope Manager received Digitiser auto gain result: {digitiser.gain} dB")

                        if obs_data is not None and str(obs_data.get('gain', '')).upper() == "AUTO":
                            obs_data['gain'] = digitiser.gain
                            obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None
                            self._apply_auto_gain_to_current_scan(obs, digitiser.gain)
                            if self.telmodel.sdp is not None:
                                sdp_dig = self.telmodel.sdp.dig_store.get_dig_by_id(digitiser.dig_id)
                                old_sdp_config = {
                                    'scan_config': {
                                        'dig_id': digitiser.dig_id,
                                        'obs_id': obs_id,
                                        'gain': sdp_dig.gain if sdp_dig is not None else None,
                                    }
                                }
                                new_sdp_config = {
                                    'sdp_id': self.telmodel.sdp.sdp_id,
                                    'obs_id': obs_id,
                                    'scan_config': {
                                        'dig_id': digitiser.dig_id,
                                        'obs_id': obs_id,
                                        'gain': digitiser.gain,
                                    },
                                }
                                pending_msg_count = len(action.msgs_to_remote)
                                action = self.update_sdp_configuration(old_sdp_config, new_sdp_config, action)
                                sdp_config_update_pending = len(action.msgs_to_remote) > pending_msg_count
                    else:
                        logger.warning("Telescope Manager received Digitiser auto gain response without a gain value.")
                else:
                    logger.info(f"Telescope Manager received Digitiser method response: {method} = {api_call.get('value')}")

            # Else if the api call is a status update message, update the Digitiser model
            elif api_call.get('property','') == tm_dig.PROPERTY_STATUS:
                logger.debug(f"Telescope Manager received Digitiser STATUS update: {api_call['value']}")
                digitiser.update_from_model(DigitiserModel.from_dict(api_call['value']))

            elif api_call.get('property','') == tm_dig.PROPERTY_SDP_COMMS:
                digitiser.sdp_connected = CommunicationStatus(api_call['value'])

            elif api_call.get('property','') in digitiser.schema.schema:
                try:
                    logger.info(f"Telescope Manager received Digitiser property update: {api_call['property']} = {api_call['value']}")
                    value = api_call['value']
                    if api_call.get('property') == tm_dig.PROPERTY_LOAD_STATE and isinstance(value, dict):
                        value = LoadState.from_dict(value) if value.get("_type") == "LoadState" else LoadState(**value)
                    setattr(digitiser, api_call.get('property',''), value)
                except (XAPIValidationFailed, XSoftwareFailure) as e:
                    logger.error(f"Telescope Manager error setting attribute {api_call.get('property','')} on Digitiser: {e}")
                    return action
            else:
                logger.warning(f"Telescope Manager received unknown Digitiser property update: {api_call.get('property')}")
                return action

            # If the api call is a rsp message
            if api_call['msg_type'] == tm_dig.MSG_TYPE_RSP:

                # If the status update message contains additional observation data, extract the related observation 
                obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None

                # If the observation was identified and is in CONFIGURING state, trigger a review of the configuration updates
                if obs is not None:

                    config_mismatched = False

                    # Check for remaining mismatches between desired and current configuration properties
                    for config_key, new_value in obs_data.items():
                        
                        if config_key in digitiser.schema.schema:
                            current_value = getattr(digitiser, config_key, None)

                            if config_key == tm_dig.PROPERTY_LOAD_STATE:
                                current_load = current_value.load if isinstance(current_value, LoadState) else None
                                if isinstance(new_value, LoadState):
                                    desired_load = new_value.load
                                elif isinstance(new_value, dict):
                                    desired_load = new_value.get("load")
                                else:
                                    desired_load = None
                                if current_load != desired_load:
                                    config_mismatched = True
                                    logger.info(f"Telescope Manager identified mismatch between desired and current load state for observation {obs_id}" +
                                    f" on digitiser {digitiser.dig_id}.\nCurrent load: {current_load}\nDesired load: {desired_load}")
                                    break
                                continue

                            if current_value != new_value:
                                config_mismatched = True
                                logger.info(f"Telescope Manager identified mismatch between desired and current configuration for observation {obs_id}" + 
                                f" on digitiser {digitiser.dig_id} for property {config_key}.\nCurrent value: {current_value}\nDesired value: {new_value}")
                                break

                    # If no mismatches remain, the configuration update has been applied successfully
                    if not config_mismatched and obs.obs_state == ObsState.CONFIGURING:
                        if sdp_config_update_pending:
                            logger.info(
                                f"Telescope Manager waiting for Science Data Processor to acknowledge resolved gain "
                                f"before advancing observation {obs_id}."
                            )
                        else:
                            logger.info(f"Telescope Manager configuration update for observation {obs_id} has been applied successfully by Digitiser {digitiser.dig_id}.")
                            action.set_obs_transition(obs=obs, transition=ObsTransition.CONFIGURE_RESOURCES)

        # Update Telescope Model timestamps based on received Digitiser api_call
        self.telmodel.dig_store.last_update = datetime.fromisoformat(dt) if dt else datetime.now(timezone.utc)
        digitiser.last_update = datetime.fromisoformat(dt) if dt else datetime.now(timezone.utc)

        return action

    def _apply_auto_gain_to_current_scan(self, obs: ObsModel, gain: float):
        """Store the resolved AUTO gain on the current scan model."""
        if obs is None or gain is None:
            return

        scan = obs.get_current_tgt_scan()
        if scan is None:
            logger.warning(f"Telescope Manager could not find current scan for observation {obs.obs_id} to apply auto gain {gain}.")
            return

        scan.gain = float(gain)
        scan.last_update = datetime.now(timezone.utc)
        obs.last_update = datetime.now(timezone.utc)
        logger.info(f"Telescope Manager applied auto gain {scan.gain} dB to scan {scan.scan_id} for observation {obs.obs_id}.")

    def process_sdp_connected(self, event) -> Action:
        """ Processes Science Data Processor connected events.
        """
        logger.info(f"Telescope Manager connected to Science Data Processor: {event.remote_addr}")

        self.telmodel.sdp.tm_connected = CommunicationStatus.ESTABLISHED
        self.telmodel.tel_mgr.sdp_connected = CommunicationStatus.ESTABLISHED

    def process_sdp_disconnected(self, event) -> Action:
        """ Processes Science Data Processor disconnected events.
        """
        logger.info(f"Telescope Manager disconnected from Science Data Processor: {event.remote_addr}")

        self.telmodel.sdp.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        self.telmodel.tel_mgr.sdp_connected = CommunicationStatus.NOT_ESTABLISHED

        return self.abort_observations()

    def process_sdp_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Science Data Processor service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"Telescope Manager received Science Data Processor {api_call['msg_type']} msg, action code: {api_call['action_code']}, property: {api_call.get('property','')}")
        action = Action()

        dt = api_msg.get("timestamp")
        if dt is not None:
            # Stop the corresponding retry timers
            action.set_timer_action(Action.Timer(name=f"{self.telmodel.sdp.sdp_id}_req_timer_retry:{dt}", timer_action=Action.Timer.TIMER_STOP))
            action.set_timer_action(Action.Timer(name=f"{self.telmodel.sdp.sdp_id}_req_timer_final:{dt}", timer_action=Action.Timer.TIMER_STOP))

        # If the api call indicates that an error occured
        if api_call.get('status','') == tm_sdp.STATUS_ERROR: 
            logger.error(self.set_last_err(f"Telescope Manager received error response from Science Data Processor.\n{api_call}"))

            # If the status update message contains additional observation data, retrieve the related observation 
            obs_data = api_call.get('obs_data', None)
            obs_id = obs_data.get('obs_id', None) if obs_data is not None and isinstance(obs_data, dict) else None
            obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None

            # If the related observation was identified, trigger the workflow to move to ABORT
            if obs is not None:
                action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)

        elif api_call.get('status','') != tm_sdp.STATUS_ERROR:

            # If a status update is received, update the Science Data Processor Model 
            if api_call.get('property','') == tm_sdp.PROPERTY_STATUS:
                self.telmodel.sdp = ScienceDataProcessorModel.from_dict(api_call['value'])

            elif api_call.get('property','') == tm_sdp.PROPERTY_SCAN_CONFIG:
                logger.info(f"Telescope Manager received Science Data Processor SCAN_CONFIG update: {api_call['value']}")

                dig_config = api_call['value']

                # Copy key value pairs from scan config rsp msg into SDP model digitiser store configuration for the related digitiser
                dig_id = dig_config.get('dig_id', None) if dig_config is not None and isinstance(dig_config, dict) else None
                dig = self.telmodel.sdp.dig_store.get_dig_by_id(dig_id) if dig_id is not None else None

                if dig is not None:
                    for key in dig_config.keys():
                        if key in dig.schema.schema.keys():
                            setattr(dig, key, dig_config[key])
                        elif key == "load":
                            current_load_state = dig.load_state if isinstance(dig.load_state, LoadState) else LoadState()
                            load_value = dig_config[key].get("load") if isinstance(dig_config[key], dict) else dig_config[key]

                            if isinstance(load_value, bool):
                                dig.load_state = LoadState(
                                    load=load_value,
                                    gpio_pin=current_load_state.gpio_pin,
                                    last_update=datetime.now(timezone.utc),
                                )
                    dig.last_update = datetime.now(timezone.utc)
                else:
                    logger.warning(f"Telescope Manager received Science Data Processor SCAN_CONFIG rsp for unknown digitiser {dig_id}\n{api_call}")

            elif api_call.get('property','') == tm_sdp.PROPERTY_OBS_RESET:
                logger.info(f"Telescope Manager received Science Data Processor OBS_RESET response: {api_call['value']}")

            elif api_call.get('property','') == tm_sdp.PROPERTY_OBS_COMPLETE:
                logger.info(f"Telescope Manager received Science Data Processor OBS_COMPLETE response: {api_call['value']}")
                
                # Update SDP digitiser configuration for the relevant observation
                obs_id = api_call['value'].get('obs_id') if api_call['value'] is not None and isinstance(api_call['value'], dict) else None
                dig = self.telmodel.sdp.dig_store.get_dig_by_obs_id(obs_id) if obs_id is not None else None
                if dig is not None:
                    # Reset the scanning field of the digitiser
                    dig.scanning = False
                    dig.last_update = datetime.now(timezone.utc)

            # Else if a scan complete advice is received, process it 
            elif api_call.get('property','') == tm_sdp.PROPERTY_SCAN_COMPLETE:

                logger.info(f"Telescope Manager received Science Data Processor SCAN_COMPLETE update: {api_call['value']}")
                # Copy all key value pairs from the api_call scan complete msg into the Science Data Processor model
                completed_scan = ScanModel.from_dict(api_call['value'])

                obs_id = completed_scan.obs_id
                scan_id = completed_scan.scan_id

                obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None
                scan = obs.get_target_scan_by_id(scan_id) if obs is not None else None

                # If we identified the observation that the scan belongs to, transition its workflow accordingly
                if obs is not None:
                    action.set_obs_transition(obs=obs, transition=ObsTransition.SCAN_COMPLETED)
                    
                    # If we identified the scan within the observation, update its metadata on disk
                    if scan is not None:
                        scan.update_from_model(completed_scan)
                        self._apply_target_pec_to_scan(obs, scan)
                        self._apply_weather_summary_to_scan(obs, scan)

                        filename = util.gen_file_prefix(
                            dt=completed_scan.read_start,
                            entity_id=completed_scan.dig_id,
                            gain=completed_scan.gain,
                            duration=completed_scan.duration,
                            sample_rate=completed_scan.sample_rate,
                            center_freq=completed_scan.center_freq,
                            channels=completed_scan.channels,
                            instance_id=scan.scan_id, 
                            scan_type=scan.scan_type,
                            filetype="meta") + ".json"

                        scan.save_to_disk(output_dir=self.telmodel.get_scan_store_dir(), filename=filename)

                    status, message = tm_sdp.STATUS_SUCCESS, f"Telescope Manager processed SCAN_COMPLETE for observation {obs_id} scan {scan_id}"
                    logger.info(message)

                else:
                    # Respond with success even though the observation is not found, as a user may be performing a manual scan via the UI
                    status, message = tm_sdp.STATUS_SUCCESS, f"Telescope Manager received SCAN_COMPLETE for user-initiated observation {obs_id} scan {scan_id}"
                    logger.info(message)

                sdp_rsp = self._construct_rsp_to_sdp(status, message, api_msg, api_call)
                action.set_msg_to_remote(sdp_rsp)

            # Else update an individual property if it exists in the Science Data Processor model
            elif api_call.get('property','') in self.telmodel.sdp.schema.schema:
                try:
                    setattr(self.telmodel.sdp, api_call.get('property',''), api_call['value'])
                except XSoftwareFailure as e:
                    logger.error(self.set_last_err(f"Telescope Manager error setting attribute {api_call.get('property','')} on Science Data Processor: {e}"))
                    return action
            else:
                logger.warning(f"Telescope Manager received unknown Science Data Processor property update:\n{api_call}")
                return action

            # If the api call is a rsp message
            if api_call['msg_type'] == tm_sdp.MSG_TYPE_RSP:
              
                # If the status update message contains additional observation data, extract the related observation 
                obs_data = api_call.get('obs_data', None)
                obs_id = obs_data.get('obs_id', None) if obs_data is not None and isinstance(obs_data, dict) else None
                obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None

                # If the observation was identified and is in CONFIGURING state, trigger a review of the configuration updates
                if obs is not None:

                    config_mismatched = False

                    # Check for remaining mismatches between desired and current configuration properties
                    for config_key, new_value in obs_data.items():
                        if config_key in self.telmodel.sdp.schema.schema:
                            current_value = getattr(self.telmodel.sdp, config_key, None)
                            if current_value != new_value:
                                config_mismatched = True
                                break

                    # If no mismatches remain, the configuration update has been applied successfully
                    if not config_mismatched and obs.obs_state == ObsState.CONFIGURING:
                        logger.info(f"Telescope Manager configuration update for observation {obs_id} has been applied successfully by Science Data Processor.")
                        action.set_obs_transition(obs=obs, transition=ObsTransition.CONFIGURE_RESOURCES)

        # Update Telescope Model timestamp based on received SDP api_call
        self.telmodel.sdp.last_update = datetime.fromisoformat(dt) if dt else datetime.now(timezone.utc)
        return action

    def process_timer_event(self, event) -> Action:
        """ Processes timer events.
        """
        logger.debug(f"Telescope Manager timer event: {event}")

        action = Action()

        # Handle an initial request msg timer retry e.g. dig001_req_timer_retry:<timestamp> or sdp001_req_timer_retry:<timestamp>
        if "req_timer_retry" in event.name:
            
            logger.warning(f"Telescope Manager timed out waiting for response msg {event.name}, retrying request msg")

            # Resend the API request if the timer user_ref is set (containing the original request message)
            if event.user_ref is not None:

                req_msg: APIMessage = event.user_ref
                final_timer = re.sub(r':.*$', f':{req_msg.get_timestamp()}', event.name.replace("retry", "final"))

                action.set_msg_to_remote(req_msg)
                action.set_timer_action(Action.Timer(
                    name=final_timer, 
                    timer_action=self._get_request_timeout_ms(req_msg),
                    echo_data=req_msg))

        # Handle a final request msg timer e.g. dig002_req_timer_final:<timestamp> or sdp002_req_timer_final:<timestamp>
        elif "req_timer_final" in event.name:
            
            logger.warning(f"Telescope Manager timed out waiting for response msg after final retry, aborting retries.\n{event}")

            if event.user_ref is not None:

                req_msg: APIMessage = event.user_ref
                echo = req_msg.get_echo_data()
                api_call = req_msg.get_api_call()
                obs_id = None

                if echo is not None and isinstance(echo, dict):
                    new_config = echo["echo_data"] if "echo_data" in echo else echo
                    obs_id = new_config["obs_id"] if new_config is not None and "obs_id" in new_config else None

                if obs_id is None and api_call is not None and isinstance(api_call, dict):
                    obs_data = api_call.get("obs_data")
                    obs_id = obs_data.get("obs_id") if isinstance(obs_data, dict) else None

                obs = self.telmodel.oda.obs_store.get_obs_by_id(obs_id) if obs_id is not None else None

                # If the observation is still in CONFIGURING state, ABORT the observation
                if obs is not None and obs.obs_state == ObsState.CONFIGURING:
                    logger.warning(f"Telescope Manager aborting observation {obs_id} after request timeout for {event.name}.")
                    action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)

        # Handle observation start timer event
        elif event.name.startswith("obs_start_timer"):
            logger.info(f"Telescope Manager observation timer event: {event}")

            now = datetime.now(timezone.utc)

            # Transition observations that are scheduled for the current scheduling block from ObsState = EMPTY to ObsState = IDLE
            # It is possible that multiple observations are scheduled for the current scheduling block and that some cannot be resourced
            # Example: A dish has become UNAVAILABLE, so only some observations can be resourced
            for obs in self.telmodel.oda.obs_store.obs_list:

                # Calculate difference between now and the observation scheduling block start time in seconds
                start_offset = abs((obs.scheduling_block_start - now).total_seconds())
  
                # Transition observations scheduled to start within 60 seconds
                if obs.obs_state == ObsState.EMPTY and start_offset <= 60:
                    action.set_obs_transition(obs=obs, transition=ObsTransition.START)
                    logger.info(f"Telescope Manager starting observation {obs.obs_id} scheduled to start at {obs.scheduling_block_start}")

            # Start timer to initiate the next scheduled observation if applicable
            self.oet.start_next_obs_timer(action)

        # Handle observation configuring timeout timer event
        elif event.name.startswith("obs_configuring_timer"):
            logger.info(f"Telescope Manager observation configuring timer event: {event}")

            obs: ObsModel = event.user_ref if isinstance(event.user_ref, ObsModel) else None

            if obs is not None and obs.obs_state == ObsState.CONFIGURING:
                logger.warning(f"Telescope Manager observation {obs.obs_id} configuration timeout occurred, aborting observation")
                action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)

        # Handle observation scanning timeout timer event
        elif event.name.startswith("obs_scanning_timer"):
            logger.info(f"Telescope Manager observation scanning timer event: {event}")

            obs: ObsModel = event.user_ref if isinstance(event.user_ref, ObsModel) else None

            if obs is not None and obs.obs_state == ObsState.SCANNING:
                logger.warning(f"Telescope Manager observation {obs.obs_id} scanning timeout occurred, ending scan")
                action.set_obs_transition(obs=obs, transition=ObsTransition.SCAN_ENDED)

        # Handle observation aborting timeout timer event
        elif event.name.startswith("obs_abort_timer"):
            logger.info(f"Telescope Manager observation abort timer fired: {event.name}")

            obs: ObsModel = event.user_ref if isinstance(event.user_ref, ObsModel) else None

            if obs is not None and obs.obs_state == ObsState.ABORTED:
                logger.warning(f"Telescope Manager observation {obs.obs_id} abort timeout occurred, releasing resources")
                action.set_obs_transition(obs=obs, transition=ObsTransition.RELEASE_RESOURCES)

        return action

    def update_sdp_configuration(self, old_config, new_config, action):
        """ Constructs and sends property set requests to the Science Data Processor.
            Only properties that changed values are sent.
            Parameters:
                old_config: dict of previous configuration values
                new_config: dict of desired configuration values
                action: Action object to append messages and timers to
            Returns updated Action object.
        """

        if self.telmodel.tel_mgr.sdp_connected != CommunicationStatus.ESTABLISHED:
            logger.warning(f"Telescope Manager cannot send Science Data Processor configuration update, not connected\n{new_config}")
            return action

        # Extract sdp_id from the incoming SDP configuration event (JSON)
        sdp_id = new_config.get("sdp_id", None)

        for config_key in new_config.keys():
            config_value = new_config[config_key]

            # If key value is unchanged, skip it
            if old_config and config_key in old_config and old_config[config_key] == config_value:
                continue

            logger.info(f"Science Data Processor configuration update for key: {config_key}, value: {config_value}")

            property = value = None
            (property, value) = map.get_property_name_value(config_key, config_value)

            if property is None:
                if config_key not in ["obs_id", "sdp_id"]: # obs_id and sdp_id are used for internal tracking
                    logger.warning(f"Telescope Manager ignoring science data processor configuration item: {config_key}")
                continue
        
            sdp_req = self._construct_req_to_sdp(property=property, value=value, message="")
            
            # Attach all new configuration to the request for tracking
            api_call = sdp_req.get_api_call()
            api_call['obs_data'] = new_config.copy() # Shallow copy

            action.set_msg_to_remote(sdp_req)
            action.set_timer_action(Action.Timer(
                name=f"{sdp_id}_req_timer_retry:{sdp_req.get_timestamp()}", 
                timer_action=self.telmodel.tel_mgr.app.msg_timeout_ms, 
                echo_data=sdp_req))
                
        return action

    def update_dig_configuration(self, old_config, new_config, action):
        """ Constructs and sends property set requests to the Digitiser.
            Only properties that changed values are sent.
            Parameters:
                old_config: dict of previous configuration values
                new_config: dict of desired configuration values
                action: Action object to append messages and timers to
            Returns updated Action object.
        """

        # Extract dig_id from the incoming DIG configuration event (JSON)
        dig_id = new_config.get("dig_id", None)
        digitiser = self.telmodel.dig_store.get_dig_by_id(dig_id) if dig_id is not None else None

        if digitiser is not None and digitiser.tm_connected != CommunicationStatus.ESTABLISHED:
            logger.warning(f"Telescope Manager cannot send Digitiser configuration update, not connected\n{new_config}")
            return action

        for config_key in new_config.keys():
            config_value = new_config[config_key]

            # If key value is unchanged, skip it
            if old_config and config_key in old_config and old_config[config_key] == config_value:
                continue

            logger.info(f"Digitiser configuration update for key: {config_key}, value: {config_value}")

            property = method = value = None

            method, method_value = map.get_method_name_value(config_key, config_value)

            if method is None:
                property, value = map.get_property_name_value(config_key, config_value)
            else:
                property, value = None, method_value

            if method is None and property is None:
                if config_key not in ["obs_id", "dig_id"]: # obs_id and dig_id are used for internal tracking
                    logger.warning(f"Telescope Manager ignoring digitiser configuration item: {config_key}")
                continue
        
            dig_req = self._construct_req_to_dig(entity=dig_id, property=property, method=method, value=value, message="")

            # Attach all new configuration to the request for tracking
            api_call = dig_req.get_api_call()
            api_call['obs_data'] = new_config.copy() # Shallow copy

            action.set_msg_to_remote(dig_req)
            action.set_timer_action(Action.Timer(
                name=f"{dig_id}_req_timer_retry:{dig_req.get_timestamp()}", 
                timer_action=self._get_request_timeout_ms(dig_req), 
                echo_data=dig_req))
                
        return action

    def update_dsh_configuration(self, old_config, new_config, action):
        """ Constructs and sends property set requests to the Dish Manager.
            Only properties that changed values are sent.
            Parameters:
                old_config: dict of previous configuration values
                new_config: dict of desired configuration values
                action: Action object to append messages and timers to
            Returns updated Action object.
        """

        if self.telmodel.tel_mgr.dm_connected != CommunicationStatus.ESTABLISHED:
            logger.warning(f"Telescope Manager cannot send Dish Manager configuration update, not connected\n{new_config}")
            return action

        # Extract dsh_id from the incoming DM configuration event (JSON)
        dsh_id = new_config.get("dsh_id", None)

        for config_key in new_config.keys():
            config_value = new_config[config_key]

            # If key value is unchanged, skip it
            if old_config and config_key in old_config and old_config[config_key] == config_value:
                logger.info(f"Telescope Manager skipping unchanged Dish Manager configuration item: {config_key}, value: {config_value}")
                continue

            logger.info(f"Dish Manager configuration update for dish {dsh_id} key: {config_key}, value: {config_value}")

            property = value = None
            (property, value) = map.get_property_name_value(config_key, config_value)

            if property is None:
                if config_key not in ["obs_id", "dsh_id"]: # obs_id and dsh_id are used for internal tracking
                    logger.warning(f"Telescope Manager ignoring dish configuration item: {config_key}")
                continue
        
            dm_req = self._construct_req_to_dm(entity=dsh_id, property=property, value=value, message="")

            # Attach all new configuration to the request for tracking
            api_call = dm_req.get_api_call()
            api_call['obs_data'] = new_config.copy() # Shallow copy

            action.set_msg_to_remote(dm_req)
            action.set_timer_action(Action.Timer(
                name=f"{dsh_id}_req_timer_retry:{dm_req.get_timestamp()}", 
                timer_action=self.telmodel.tel_mgr.app.msg_timeout_ms, 
                echo_data=dm_req))
                
        return action

    def get_health_state(self) -> HealthState:
        """ Returns the current health state of this application.
        """
        if self.telmodel.tel_mgr.sdp_connected != CommunicationStatus.ESTABLISHED:
            return HealthState.DEGRADED
        elif self.telmodel.tel_mgr.dm_connected != CommunicationStatus.ESTABLISHED:
            return HealthState.DEGRADED
        elif any(dig.tm_connected != CommunicationStatus.ESTABLISHED for dig in self.telmodel.dig_store.dig_list):
            return HealthState.DEGRADED
        else:
            return HealthState.OK

    def process_status_event(self, event) -> Action:
        """ Processes status update events. 
            Calls get_app_processor_state() to update the Telescope Model status.
            Reads the scan store directory to update the scan store file lists.
        """
        status = self.get_app_processor_state()

        scan_store_dir = self.telmodel.sdp.app.arguments.get('scan_store_dir','~/') if self.telmodel.sdp.app.arguments is not None else '~/'
        scan_store_dir = os.path.expanduser(scan_store_dir)

        if Path(scan_store_dir).exists():

            logger.info(f"Telescope Manager reading scan store directory: {scan_store_dir}")    

            # Read scan store directory listing
            spr_files = list(Path(scan_store_dir).glob("*spr.csv"))
            load_files = list(Path(scan_store_dir).glob("*load.csv"))
            tsys_files = list(Path(scan_store_dir).glob("*tsys.csv"))
            gain_files = list(Path(scan_store_dir).glob("*gain.csv"))
            meta_files = list(Path(scan_store_dir).glob("*meta.json"))

            # Sort by creation date in reverse order (newest first)
            spr_files.sort(key=lambda x: x.stat().st_ctime, reverse=True)
            load_files.sort(key=lambda x: x.stat().st_ctime, reverse=True)
            tsys_files.sort(key=lambda x: x.stat().st_ctime, reverse=True)
            gain_files.sort(key=lambda x: x.stat().st_ctime, reverse=True)
            meta_files.sort(key=lambda x: x.stat().st_ctime, reverse=True)

            # Limit to the latest 10 files of each type
            spr_files = spr_files[:10]
            load_files = load_files[:10]
            tsys_files = tsys_files[:10]
            gain_files = gain_files[:10]
            meta_files = meta_files[:10]

            # Combine into a single list of scan files
            scan_files = spr_files + load_files + tsys_files + gain_files + meta_files

            self.telmodel.oda.scan_store.spr_files = []
            self.telmodel.oda.scan_store.load_files = []
            self.telmodel.oda.scan_store.tsys_files = []
            self.telmodel.oda.scan_store.gain_files = []
            self.telmodel.oda.scan_store.meta_files = []

            for scan_file in scan_files:
                if scan_file.name.endswith("spr.csv"):
                    self.telmodel.oda.scan_store.spr_files.append(scan_file.name)
                elif scan_file.name.endswith("load.csv"):
                    self.telmodel.oda.scan_store.load_files.append(scan_file.name)
                elif scan_file.name.endswith("tsys.csv"):
                    self.telmodel.oda.scan_store.tsys_files.append(scan_file.name)
                elif scan_file.name.endswith("gain.csv"):
                    self.telmodel.oda.scan_store.gain_files.append(scan_file.name)
                elif scan_file.name.endswith("meta.json"):
                    self.telmodel.oda.scan_store.meta_files.append(scan_file.name)

            self.telmodel.oda.scan_store.last_update = datetime.now(timezone.utc)
            self.telmodel.oda.last_update = datetime.now(timezone.utc)

        self.telmodel.tel_mgr.last_update = datetime.now(timezone.utc)

    def _is_startup_odt_event(self, event: ConfigEvent) -> bool:
        """Return True when the provided ODT config event is the startup -o injection."""
        return (
            event is not None
            and event.category.upper() == "ODT"
            and self._startup_odt_pending_config is not None
            and event.new_config == self._startup_odt_pending_config
        )

    def _disable_startup_odt_protection(self):
        """Release precedence of the startup -o observation over other ODT sources."""
        self._startup_odt_protection_enabled = False
        self._startup_odt_pending_config = None
        self._startup_odt_protected_obs_ids.clear()

    def _apply_target_pec_to_scan(self, obs, scan: ScanModel):
        """Copy the latest target-level PEC from the DM snapshot onto a completed scan."""
        if obs is None or scan is None:
            return

        dsh_mgr = self.telmodel.dsh_mgr
        dsh_model = dsh_mgr.get_dish_by_id(obs.dsh_id) if dsh_mgr is not None and obs.dsh_id is not None else None

        if dsh_model is None:
            logger.debug(f"Telescope Manager could not find dish {obs.dsh_id} to attach PEC to scan {scan.scan_id}.")
            return

        tgt_idx = scan.tgt_idx
        tgt_id = f"{obs.obs_id}-{tgt_idx}" if obs.obs_id is not None and tgt_idx is not None else None
        tgt_pec = dsh_model.get_pec_by_tgt_id(tgt_id) if tgt_id is not None else None

        if tgt_pec is None:
            logger.debug(f"Telescope Manager could not find target PEC {tgt_id} for scan {scan.scan_id}.")
            return

        scan.target_alt_pec_rms = float(tgt_pec.alt_rms)
        scan.target_az_pec_rms = float(tgt_pec.az_rms)
        scan.target_pec_last_update = tgt_pec.last_update

    def _select_weather_summary_for_dish(self, dsh_model) -> WeatherSummary:
        """Select the most appropriate weather summary for a dish."""
        weather_store = self.telmodel.dsh_mgr.weather_store if self.telmodel.dsh_mgr is not None else None
        summaries = weather_store.weather_summaries if weather_store is not None else []

        if not summaries:
            return None

        if dsh_model.ws_id is not None:
            summary = weather_store.get_summary_by_ws_id(dsh_model.ws_id)
            if summary is not None:
                return summary

        if len(summaries) == 1:
            return summaries[0]

        nearest_summary = None
        nearest_distance = float("inf")

        for summary in summaries:
            station = weather_store.get_station(summary.ws_id)
            if station is None or station.latitude is None or station.longitude is None:
                continue

            distance = (station.latitude - dsh_model.latitude) ** 2 + (station.longitude - dsh_model.longitude) ** 2
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_summary = summary

        return nearest_summary if nearest_summary is not None else summaries[0]

    def _apply_weather_summary_to_scan(self, obs, scan: ScanModel):
        """Copy the latest rolling weather summary from the DM snapshot onto a completed scan."""
        if obs is None or scan is None:
            return

        dsh_mgr = self.telmodel.dsh_mgr
        dsh_model = dsh_mgr.get_dish_by_id(obs.dsh_id) if dsh_mgr is not None and obs.dsh_id is not None else None

        if dsh_model is None:
            logger.debug(f"Telescope Manager could not find dish {obs.dsh_id} to attach weather metadata to scan {scan.scan_id}.")
            return

        summary = self._select_weather_summary_for_dish(dsh_model)
        if summary is None:
            logger.debug(f"Telescope Manager could not find a weather summary for dish {dsh_model.dsh_id} and scan {scan.scan_id}.")
            return
        if summary.sample_count == 0 or summary.last_sample_time is None:
            logger.debug(f"Telescope Manager found no fresh weather samples for dish {dsh_model.dsh_id} and scan {scan.scan_id}.")
            return

        scan.ws_id = summary.ws_id
        scan.ws_sec = summary.sample_secs
        scan.wind_avg = float(summary.wind_avg)
        scan.wind_rms = float(summary.wind_rms)
        scan.wind_max = float(summary.wind_max)
        scan.wind_sample_count = summary.sample_count
        scan.wind_sample_time = summary.last_sample_time

    def _construct_req_to_dig(self, entity=None, property=None, method=None, value=None, message=None) -> APIMessage:
        """ Constructs a request message to the Digitiser.
        """

        dig_req = APIMessage(api_version=self.dig_api.get_api_version())

        # If property is auto gain or read_samples
        if method is not None:
            dig_req.set_json_api_header(
                api_version=self.dig_api.get_api_version(), 
                dt=datetime.now(timezone.utc), 
                from_system=self.app_model.app_name, 
                to_system="dig", 
                entity=entity if entity else "<undefined>",
                api_call={
                    "msg_type": "req", 
                    "action_code": "method", 
                    "method": method, 
                    "params": value if value is not None else {}
            })
        elif property is not None:
            dig_req.set_json_api_header(
                api_version=self.dig_api.get_api_version(), 
                dt=datetime.now(timezone.utc), 
                from_system=self.app_model.app_name, 
                to_system="dig", 
                entity=entity if entity else "<undefined>",
                api_call={
                    "msg_type": "req", 
                    "action_code": "set", 
                    "property": property, 
                    "value": value if value is not None else 0, 
                    "message": message if message else ""
            })

        return dig_req

    def _get_request_timeout_ms(self, req_msg: APIMessage) -> int:
        """Return a request timeout tailored to the API call being sent."""
        timeout_ms = self.telmodel.tel_mgr.app.msg_timeout_ms
        api_call = req_msg.get_api_call() if req_msg is not None else {}

        if (
            isinstance(api_call, dict)
            and api_call.get("action_code") == tm_dig.ACTION_CODE_METHOD
            and api_call.get("method") in [tm_dig.METHOD_GET_AUTO_GAIN, tm_dig.METHOD_SET_AUTO_GAIN]
        ):
            return max(timeout_ms, self.AUTO_GAIN_TIMEOUT_MS)

        return timeout_ms

    def _construct_req_to_sdp(self, property=None, value=None, message=None) -> APIMessage:
        """ Constructs a request message to the Science Data Processor.
        """

        sdp_req = APIMessage(api_version=self.sdp_api.get_api_version())

        if property is not None:
            sdp_req.set_json_api_header(
                api_version=self.sdp_api.get_api_version(), 
                dt=datetime.now(timezone.utc), 
                from_system=self.app_model.app_name, 
                to_system="sdp", 
                api_call={
                    "msg_type": "req", 
                    "action_code": "set", 
                    "property": property, 
                    "value": value if value is not None else 0, 
                    "message": message if message else ""
            })

        return sdp_req

    def _construct_req_to_dm(self, entity=None, property=None, value=None, message=None) -> APIMessage:
        """ Constructs a request message to the Dish Manager.
        """

        dm_req = APIMessage(api_version=self.dm_api.get_api_version())
        if property is not None:
            dm_req.set_json_api_header(
                api_version=self.dm_api.get_api_version(), 
                dt=datetime.now(timezone.utc), 
                from_system=self.app_model.app_name, 
                to_system="dm",
                entity=entity if entity else "<undefined>",
                api_call={
                    "msg_type": "req", 
                    "action_code": "set", 
                    "property": property, 
                    "value": value if value is not None else 0, 
                    "message": message if message else ""
            })

        return dm_req

    def _construct_rsp_to_sdp(self, status, message, api_msg: dict, api_call: dict) -> APIMessage:
        """ Constructs a response message to the Science Data Processor.
        """
        # Prepare rsp msg to sdp containing result of an api call
        sdp_rsp = APIMessage(api_msg=api_msg, api_version=self.sdp_api.get_api_version())
        sdp_rsp.switch_from_to()
        sdp_rsp_api_call = {
            "msg_type": "rsp", 
            "action_code": api_call['action_code'], 
            "status": status, 
        }
        if api_call.get('property') is not None:
            sdp_rsp_api_call["property"] = api_call['property']

        if api_call.get('value') is not None:
            sdp_rsp_api_call["value"] = api_call['value']

        if message is not None:
            sdp_rsp_api_call["message"] = message

        sdp_rsp.set_api_call(sdp_rsp_api_call)  
        return sdp_rsp

    def abort_observations(self, dig_id: str = None, dsh_id: str=None, action=None) -> Action:
        """ Aborts ongoing observations matching the given digitiser ID or dish manager ID.
            If both IDs are None, aborts all ongoing observations.
            Returns an Action object containing the observation abort transition
        """
        action = Action() if action is None else action

        dish1 = self.telmodel.dsh_mgr.get_dish_by_id(dsh_id) if dsh_id is not None else None
        dish2 = self.telmodel.dsh_mgr.get_dish_by_dig_id(dig_id) if dig_id is not None else None

        ids = []
        if dish1 is not None:
            ids.append(dish1.dsh_id)
        if dish2 is not None and dish2.dsh_id not in ids:
            ids.append(dish2.dsh_id)

        for obs in self.telmodel.oda.obs_store.obs_list:

            logger.debug(f"Checking whether to abort observation {obs.obs_id} in state {obs.obs_state.name} for dish manager ID {obs.dsh_id} and digitiser ID {dig_id} with filter dish manager IDs {ids} and filter digitiser ID {dig_id}")

            if obs.obs_state in [ObsState.CONFIGURING, ObsState.READY, ObsState.SCANNING]:

                if obs.dsh_id in ids or (dig_id is None and dsh_id is None):
                    
                    dish = self.telmodel.dsh_mgr.get_dish_by_id(obs.dsh_id)
                    dig = dish.dig_id if dish is not None else None

                    logger.info(f"Telescope Manager aborting observation {obs.obs_id}.\nConnection status:\n" + \
                        f"- Dish Manager {obs.dsh_id}: {self.telmodel.dsh_mgr.tm_connected.name}\n" + \
                        f"- Digitiser {dig}: {self.telmodel.dig_store.get_dig_by_id(dig).tm_connected.name if dig is not None else 'N/A'}\n" + \
                        f"- Science Data Processor: {self.telmodel.sdp.tm_connected.name}")
                    action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)

        return action

def main():
  
    tm = TelescopeManager()
    tm.start()

    # Start webhook handler before optional UI handling so webhook inputs still work in headless mode.
    webhook_handler = WebhookHandler(event_queue=tm.get_queue(), host='127.0.0.1', port=5001)
    webhook_handler.start()
    logger.info("Webhook handler initialized and running on port 5001")

    if tm.is_headless():
        logger.info("Telescope Manager running in headless mode; skipping ui initialization and updates.")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            pass
        finally:
            tm.stop()
        return

    last_odt_config_snapshot = None

    try:

        # Iterate through all UI drivers every second (while True) 
        # Instantiate uninitialised UI drivers based on type and config
        # Push Telescope Model updates to initialized UI drivers based on their defined poll period
        # Pull configuration updates from UI drivers and trigger corresponding configuration update events in the Telescope Model

        while True:
            now = datetime.now(timezone.utc)

            for driver in tm.telmodel.tel_mgr.ui_drivers:

                # If the driver instance is not yet initialized, initialize it based on its type and config
                if driver.instance is None:

                    if driver.type == UIDriverType.GSHEETS:
                        from ui.drivers.gsheets.gsheets_driver import GoogleSheetsDriver
                        from ui.drivers.gsheets.gsheets_model import GSheetConfig
                        config = GSheetConfig(**driver.config) if isinstance(driver.config, dict) else driver.config
                        driver.instance = GoogleSheetsDriver(config)
                        logger.info(f"Telescope Manager initialised Google Sheets driver for UI integration with config:\n" + \
                            f"{json.dumps(driver.config, indent=2)}")
                    else:
                        logger.warning(f"Telescope Manager UI driver {driver.type} not supported, skipping UI integration for this driver")

                # Else check if a model push is due based on the poll period defined for the driver
                else:
                    # Check if the driver's poll period is due
                    if (now - driver.last_update).total_seconds() >= driver.poll_period:

                        logger.info(f"Telescope Manager pushing Telescope Model update to UI driver {driver.type.name} {driver.short_desc}")

                        try:
                            driver.last_update = now

                            dig_dict = tm.telmodel.dig_store.to_dict()
                            driver.instance.publish(dig_dict)
                        
                            dm_dict = tm.telmodel.dsh_mgr.to_dict() 
                            driver.instance.publish(dm_dict)

                            sdp_dict = tm.telmodel.sdp.to_dict()
                            driver.instance.publish(sdp_dict)

                            oda_dict = tm.telmodel.oda.to_dict()
                            driver.instance.publish(oda_dict)

                            ws_dict = tm.telmodel.wtr_stn.to_dict()
                            driver.instance.publish(ws_dict)

                            tm_dict = tm.telmodel.tel_mgr.to_dict()
                            driver.instance.publish(tm_dict)

                            odt = driver.instance.read_config("ObsList") if hasattr(driver.instance, "read_config") else None
                            if odt:
                                logger.debug(f"Telescope Manager read ODT configuration from UI driver {driver.type.name}:\n{json.dumps(odt, indent=2)}")
                                if odt != last_odt_config_snapshot:
                                    config = ConfigEvent(
                                        category="ODT",
                                        old_config=last_odt_config_snapshot,
                                        new_config=odt,
                                        timestamp=now
                                    )
                                    tm.get_queue().put(config)

                                    last_odt_config_snapshot = odt
 
                        except Exception as e:
                            logger.error(f"Error publishing to UI driver {driver.type.name}: {e}")
         
            # Sleep before checking whether drivers are due for an update
            time.sleep(1) 

    except KeyboardInterrupt:
        pass
    finally:
        tm.stop()

if __name__ == "__main__":
    main()
