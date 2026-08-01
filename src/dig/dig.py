import logging
import json
import numpy as np
import threading
import time
import _thread
from datetime import datetime, timezone
from gpiozero import LED

from api import tm_dig, sdp_dig
from dig.temp import Temperature
from env.app import App
from ipc.message import APIMessage
from ipc.message import AppMessage
from ipc.action import Action
from ipc.tcp_client import TCPClient
from ipc.tcp_server import TCPServer
from models.app import AppModel
from models.base import BaseModel
from models.comms import CommunicationStatus, InterfaceType
from models.dig import BandpassFilterType, DigitiserList, DigitiserModel
from models.health import HealthState
from sdr.facade import SDR
from util import log, util
from util.format import fmt_bool
from util.timer import Timer, TimerManager
from util.xbase import XStreamUnableToExtract, XSoftwareFailure, XHardwareFailure, XAPIValidationFailed

logger = logging.getLogger("dig.dig")

TEMP_WARNING_DELTA = 5.0  # Degrees Celsius below maximum temperature at which to log a warning for Digitiser Assembly temperature sensor readings

class Digitiser(App):

    def __init__(self, app_name: str = "dig"):

        self.dig_model = DigitiserModel()

        super().__init__(app_name=app_name, app_model = self.dig_model.app)

        self.dig_model.dig_id = self.get_args().entity_id

        # Telescope Manager interface
        self.tm_system = "tm"
        self.tm_api = tm_dig.TM_DIG()
        # Telescope Manager TCP Client
        self.tm_endpoint = TCPClient(description=self.tm_system, queue=self.get_queue(), host=self.get_args().tm_host, port=self.get_args().tm_port)
        # Register Telescope Manager interface with the App
        self.register_interface(self.tm_system, self.tm_api, self.tm_endpoint, InterfaceType.ENTITY)
        # Set initial Telescope Manager connection status
        self.dig_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED

        # Science Data Processor Interface
        self.sdp_system = "sdp"
        self.sdp_api = sdp_dig.SDP_DIG()
        # Science Data Processor TCP Client
        self.sdp_endpoint = TCPClient(description=self.sdp_system, queue=self.get_queue(), host=self.get_args().sdp_host, port=self.get_args().sdp_port)
        # Register Science Data Processor interface with the App
        self.register_interface(self.sdp_system, self.sdp_api, self.sdp_endpoint, InterfaceType.ENTITY)
        # Set initial Science Data Processor connection status
        self.dig_model.sdp_connected = CommunicationStatus.NOT_ESTABLISHED

        self.dig_model.scanning = False # Flag indicating if we are currently scanning for samples (from the SDR)

        self.load_relay = None          # Optional GPIO output to drive a relay switch to apply a load resistor in the signal path
        self.power_relay = None         # Optional GPIO output to drive a relay switch to power on/off an optional bandpass filter in the signal path
        self._bpf_control_state = {"load": None, "power": None}
        self._bpf_control_pin = {"load": None, "power": None}
        self._bpf_control_lock = threading.Lock()

        self._scan_samples_generation = 0
        self._scan_samples_generation_lock = threading.Lock()
        self._idle_poweroff_seconds = 300
        self._last_active_dt = datetime.now(timezone.utc)
        self._shutdown_requested = False

    def add_args(self, arg_parser):
        """ Specifies the digitiser's command line arguments.
        """
        super().add_args(arg_parser)

        arg_parser.add_argument("--tm_host", type=str, required=False, help="TCP host to listen on for Telescope Manager commands", default="localhost")
        arg_parser.add_argument("--tm_port", type=int, required=False, help="TCP port to listen on for Telescope Manager commands", default=50000)
        
        arg_parser.add_argument("--sdp_host", type=str, required=False, help="TCP server host to connect to for downstream Science Data Processor transport",default="localhost")
        arg_parser.add_argument("--sdp_port", type=int, required=False, help="TCP server port to connect to for downstream Science Data Processor transport", default=60000)

        arg_parser.add_argument("--local_host", type=str, required=True, help="Localhost (ip4 address) on which the digitiser is running e.g. 192.168.0.1", default="0.0.0.0")
    
    def process_init(self) -> Action:
        """ Processes initialisation event on startup once all app processors are running.
            Runs in single threaded mode and switches to multi-threading mode after this method completes.
        """
        logger.debug(f"Digitiser initialisation event")

        action = Action()

        # Config files located in ./config/<profile>/<model>.json
        input_dir = f"./config/{self.get_args().profile}"
        filename = "DigitiserList.json"

        try:
            dig_store = DigitiserList.load_from_disk(input_dir=input_dir, filename=filename)
        except FileNotFoundError:
            dig_store = None

        if dig_store is not None:
            dig_config = dig_store.get_dig_by_id(self.dig_model.dig_id)
            if dig_config is not None:
                for key in self.dig_model.schema.schema.keys():
                    if key == "app":
                        continue
                    setattr(self.dig_model, key, getattr(dig_config, key))
                self.dig_model.dig_id = self.get_args().entity_id
                logger.info(f"Digitiser loaded configuration for {self.dig_model.dig_id} from directory {input_dir} file {filename}")
            else:
                msg = f"Digitiser configuration for {self.dig_model.dig_id} not found in directory {input_dir} file {filename}"
                logger.warning(self.set_last_err(msg))
        else:
            msg = f"Digitiser could not load Digitiser configuration from directory {input_dir} file {filename}"
            logger.warning(self.set_last_err(msg))

        # Initialise the Software Defined Radio (internal) interface
        self.sdr = SDR(sdr_type=self.dig_model.sdr_type, sdr_config=self.dig_model.sdr_config)
        self.dig_model.sdr_eeprom = self.sdr.get_eeprom_info() or {}
        self.dig_model.sdr_connected = self.sdr.get_comms_status()

        # Initialise the Digitiser Assembly temperature sensor interface
        self.temp_sensor = None
        if self.dig_model.temp_type is not None and self.dig_model.temp_type.lower() != "none":

            logger.info(f"Digitiser configuring temperature sensor type={self.dig_model.temp_type} config={self.dig_model.temp_config}")
            
            self.temp_sensor = Temperature(device=self.dig_model.temp_type, sensor_config=self.dig_model.temp_config)
            self.dig_model.temp_connected = self.temp_sensor.get_comms_status()
            
            if self.temp_sensor.get_comms_status() == CommunicationStatus.ESTABLISHED:
                logger.info("Digitiser successfully connected to temperature sensor.")
            else:
                logger.info("Digitiser temperature sensor configured; waiting for background connection.")
            
            # Poll the temperature sensor every 5 seconds. The Temperature class returns
            # None until its background reader has a fresh cached value.
            action.set_timer_action(Action.Timer(name=f"temp_sensor_poll", timer_action=5000, echo_data=None))
        else:
            self.dig_model.temp_connected = CommunicationStatus.DISABLED
        
        # Start timer to periodically checks comms e.g. SDR, Bandpass filter relays, temp sensors etc
        action.set_timer_action(Action.Timer(name=f"comms_retry", timer_action=5000))

        # Connect client endpoints to interfaces
        self.tm_endpoint.connect()
        self.sdp_endpoint.connect()

        return action

    def process_tm_connected(self, event) -> Action:
        """ Processes Telescope Manager connected events.
        """
        logger.debug(f"Digitiser connected to Telescope Manager: {event.remote_addr}")

        self.dig_model.tm_connected = CommunicationStatus.ESTABLISHED
        
        # Send status advice message to Telescope Manager
        tm_adv = self._construct_status_adv_to_tm()
        action = Action()
        action.set_msg_to_remote(tm_adv)

        return action

    def process_tm_disconnected(self, event) -> Action:
        """ Processes Telescope Manager disconnected events.
        """
        logger.debug(f"Digitiser disconnected from Telescope Manager: {event.remote_addr}")

        self.dig_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        self.set_last_err("Telescope Manager disconnected")

        # If currently scanning for an observation, stop scanning due to TM disconnect
        if isinstance(self.dig_model.scanning, dict) and self.dig_model.scanning.get('obs_id', None) is not None:
            message = f"Digitiser stopping scanning for observation {self.dig_model.scanning.get('obs_id', 'None')} due to Telescope Manager disconnect."
            logger.warning(self.set_last_err(message))

            self.set_scanning(False)
            self._advance_scan_samples_generation()
            self.set_bpf_power_state(False)  # Switch bandpass filter powered off when stopping scanning:

    def process_tm_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Telescope Manager service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.debug(f"Digitiser received Telescope Manager message:\n{event}")

        action = Action()

        # If api call is a rsp msg from the TM
        if api_call['msg_type'] == 'rsp':

            # Stop the corresponding req/adv timer if applicable
            dt = api_msg.get('timestamp')
            if dt:
                action.set_timer_action(Action.Timer(name=f"tm_req_timer:{dt}", timer_action=Action.Timer.TIMER_STOP))
                action.set_timer_action(Action.Timer(name=f"tm_adv_timer:{dt}", timer_action=Action.Timer.TIMER_STOP))
            
            if api_call.get('status') == tm_dig.STATUS_ERROR:
                msg = f"Digitiser received negative acknowledgement from TM for api call\n{json.dumps(api_call, indent=2)}"
                logger.error(self.set_last_err(msg))

            return action

        # Else if api call is a req or adv msg from the TM
        elif api_call['msg_type'] in ['req', 'adv']:

            # Touch the last active timestamp to prevent idle power off of the bandpass filter
            self._last_active_dt = datetime.now(timezone.utc)

            scanning = self.dig_model.scanning
            obs_id = scanning.get('obs_id', None) if isinstance(scanning, dict) else None

            # If we are busy scanning samples for an observation and receive a new unrelated obs set / method api call, reject it
            if obs_id and obs_id !=api_call.get('obs_data', {}).get('obs_id'):
                if api_call['action_code'] in ["set", "method"]:
                    msg = f"Digitiser busy scanning for observation {obs_id} and cannot process unrelated API call until observation is complete"               
                    logger.error(self.set_last_err(msg) + f"\n{json.dumps(api_call, indent=2)}")
                    
                    action.set_msg_to_remote(self._construct_rsp_to_tm(tm_dig.STATUS_ERROR, msg, None, api_msg, api_call))
                    return action
            
            self.set_bpf_power_state(True)  # Ensure bandpass filter is powered on before handing calls to handlers

            # Dispatch the API Call to a handler method
            dispatch = {
                "set": self.handle_field_set,
                "get": self.handle_field_get,
                "method": self.handle_method_call
                }

            # Invoke set, get or method handler to process the api call
            result = dispatch.get(api_call['action_code'], lambda x: None)(api_call)
            status, message, value, payload = util.unpack_result(result)

            # If api call was successfully processed by the handler method
            if status == tm_dig.STATUS_SUCCESS:

                # If the API call is a "set" action for the "scanning" property
                if api_call['action_code'] == tm_dig.ACTION_CODE_SET and api_call.get('property') == tm_dig.PROPERTY_SCANNING:

                    logger.info(f"Digitiser scanning state changed to: {value}")
                    scan_generation = self._advance_scan_samples_generation()

                    # If scanning is active, ensure sample reads are running immediately.
                    if self.dig_model.scanning:

                        # Two timers run in parallel: one can send the previous read
                        # while the SDR worker services the other read.
                        for i in range(1, 3):
                            action.set_timer_action(Action.Timer(name=f"scan_samples_{i}", timer_action=0, echo_data=scan_generation))
                                
                    else:    
                        # Stop active scan_samples timers. Already queued events are
                        # invalidated by the generation change above.
                        for timer in Timer.manager.get_timers_by_keyword(f"scan_samples"):
                            action.set_timer_action(Action.Timer(name=timer.name, timer_action=Action.Timer.TIMER_STOP))

                # Else if the API call is a "method" action for reading samples
                elif api_call['action_code'] == tm_dig.ACTION_CODE_METHOD and api_call['method'] in ("read_samples", "read_bytes"):

                    if self.dig_model.sdp_connected == CommunicationStatus.ESTABLISHED and payload is not None:
                        # Prepare adv msg to send samples to sdp
                        sdp_adv = self._construct_adv_to_sdp(status, message, value, payload.tobytes())
                        action.set_msg_to_remote(sdp_adv)
                        action.set_timer_action(Action.Timer(name=f"sdp_adv_timer:{sdp_adv.get_timestamp()}", timer_action=self.dig_model.app.msg_timeout_ms, echo_data=sdp_adv))

                    elif not self.dig_model.sdp_connected == CommunicationStatus.ESTABLISHED:
                        self.set_last_err("Digitiser cannot send samples to Science Data Processor, not connected.")
                        # Send status advice message to Telescope Manager
                        tm_adv = self._construct_status_adv_to_tm()
                        action.set_msg_to_remote(tm_adv)
                        action.set_timer_action(Action.Timer(name=f"tm_adv_timer:{tm_adv.get_timestamp()}", timer_action=self.dig_model.app.msg_timeout_ms, echo_data=tm_adv))

                    elif payload is None:
                        # Wait for scan_samples timer to trigger again
                        logger.warning(self.set_last_err("Digitiser cannot send samples to Science Data Processor, no payload (samples) from the SDR."))

            tm_rsp = self._construct_rsp_to_tm(status, message, value, api_msg, api_call)
            action.set_msg_to_remote(tm_rsp)

        return action

    def process_sdp_connected(self, event) -> Action:
        """ Processes Science Data Processor connected events.
        """
        logger.info(f"Digitiser connected to Science Data Processor: {event.remote_addr}")

        self.dig_model.sdp_connected = CommunicationStatus.ESTABLISHED

        action = Action()

        if self.dig_model.tm_connected == CommunicationStatus.ESTABLISHED:
            # Send status advice message to Telescope Manager
            tm_adv = self._construct_status_adv_to_tm()
            action.set_msg_to_remote(tm_adv)
            action.set_timer_action(Action.Timer(name=f"tm_adv_timer:{tm_adv.get_timestamp()}", timer_action=self.dig_model.app.msg_timeout_ms, echo_data=tm_adv))

        return action

    def process_sdp_disconnected(self, event) -> Action:
        """ Processes Science Data Processor disconnected events.
        """
        logger.info(f"Digitiser disconnected from Science Data Processor: {event.remote_addr}")

        self.dig_model.sdp_connected = CommunicationStatus.NOT_ESTABLISHED

        # If currently scanning for an observation, stop scanning due to SDP disconnect (TM will abort the observation anyway)
        if isinstance(self.dig_model.scanning, dict) and self.dig_model.scanning.get('obs_id', None) is not None:
            message = f"Digitiser stopping scanning for observation {self.dig_model.scanning.get('obs_id', 'None')} due to Science Data Processor disconnect."
            logger.warning(self.set_last_err(message))

            self.set_scanning(False)
            self._advance_scan_samples_generation()
            self.set_bpf_power_state(False)  # Switch bandpass filter powered off when stopping scanning:

        action = Action()

        if self.dig_model.tm_connected == CommunicationStatus.ESTABLISHED:
            # Send status advice message to Telescope Manager
            tm_adv = self._construct_status_adv_to_tm()
            action.set_msg_to_remote(tm_adv)
            action.set_timer_action(Action.Timer(name=f"tm_adv_timer:{tm_adv.get_timestamp()}", timer_action=self.dig_model.app.msg_timeout_ms, echo_data=tm_adv))

        return action

    def process_sdp_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Science Data Processor service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.debug(f"Digitiser received sdp message:\n{event}")

        action = Action()

         # If api call is a rsp msg from the SDP
        if api_call['msg_type'] == 'rsp':

            # Stop the corresponding req/adv timer if applicable
            dt = api_msg.get('timestamp')
            if dt:
                action.set_timer_action(Action.Timer(name=f"sdp_req_timer:{dt}", timer_action=Action.Timer.TIMER_STOP))
                action.set_timer_action(Action.Timer(name=f"sdp_adv_timer:{dt}", timer_action=Action.Timer.TIMER_STOP))
            
            if api_call.get('status') == tm_dig.STATUS_ERROR:
                message = f"Digitiser received negative acknowledgement from SDP for api call\n{json.dumps(api_call, indent=2)}"
                logger.error(self.set_last_err(message))

        return action

    def process_timer_event(self, event) -> Action:
        """ Processes timer events.
        """
        logger.debug(f"Digitiser timer event: {event}")

        action = Action()

        # If the timer is for scanning samples from the SDR
        if event.name.startswith("scan_samples"):

            scan_generation = event.user_ref
            if not self._is_current_scan_samples_generation(scan_generation):
                logger.debug(f"Digitiser ignoring stale {event.name} timer event from generation {scan_generation}; current generation is {self._current_scan_samples_generation()}.")
                return action
            
            # Invoke the read_samples method to read samples from the SDR
            result = self.handle_method_call({"method": "read_samples", "params": {}})
            status, message, value, payload = util.unpack_result(result)

            if not self._is_current_scan_samples_generation(scan_generation):
                logger.debug(f"Digitiser dropping stale samples from {event.name} generation {scan_generation}; current generation is {self._current_scan_samples_generation()}.")
                return action

            # If the digitiser is set to scan samples
            if self.dig_model.scanning:

                # Start the same scan_samples timer immediately if it was successful, else wait 1000 milliseconds before retrying
                wait = 0 if status == tm_dig.STATUS_SUCCESS else 1000 
                action.set_timer_action(Action.Timer(name=event.name, timer_action=wait, echo_data=scan_generation)) 

            if self.dig_model.sdp_connected == CommunicationStatus.ESTABLISHED and payload is not None:
                # Prepare adv msg to send samples to sdp
                sdp_adv = self._construct_adv_to_sdp(status, message, value, payload.tobytes())
                action.set_msg_to_remote(sdp_adv)
                action.set_timer_action(Action.Timer(name=f"sdp_adv_timer:{sdp_adv.get_timestamp()}", timer_action=self.dig_model.app.msg_timeout_ms, echo_data=sdp_adv))

            elif payload is None:
                # Wait for scan_samples timer to trigger again
                self.set_last_err(f"Digitiser cannot send samples to Science Data Processor on {event.name}, no payload (samples) after reading SDR.")
        
        # Else if the timer is for handling sdp adv timeouts
        elif event.name.startswith("sdp_adv_timer"):

            # Simply log a warning that the SDP did not acknowledge the samples advice
            self.set_last_err(f"Digitiser timed out waiting for acknowledgement from SDP for samples advice {event}")

        # Else if the timer is for handling temperature sensor polling
        elif event.name.startswith("temp_sensor_poll"):

            # Restart the timer to keep polling periodically
            action.set_timer_action(Action.Timer(name=f"temp_sensor_poll", timer_action=5000))

            if self.temp_sensor is not None and self.temp_sensor.get_comms_status() == CommunicationStatus.ESTABLISHED:
                
                temp_reading = self.temp_sensor.get_reading()
                if temp_reading is not None:

                    logger.debug(f"Digitiser (Assembly) temperature sensor reading: {temp_reading.temperature:.2f} C, humidity: {temp_reading.humidity:.2f} %, pressure: {temp_reading.pressure:.2f} hPa")
                    self.dig_model.temp_reading = temp_reading

                    if self.dig_model.temp_max is not None:

                        # If the temperature reading exceeds the maximum configured temperature, initiate shutdown of the digitiser process
                        if temp_reading.temperature > self.dig_model.temp_max:

                            # Stop scanning and advance the scan samples generation to invalidate any queued/in-flight scan sample timers
                            self.set_scanning(False)
                            self._advance_scan_samples_generation()

                            shutdown_reason = (f"Digitiser Assembly temperature sensor reading {temp_reading.temperature:.2f} C "
                                               f"exceeds maximum configured temperature {self.dig_model.temp_max:.2f} C. "
                                               "Initiating automatic shutdown.")
                    
                            logger.error(self.set_last_err(shutdown_reason))
                            self.dig_model.app.health = HealthState.FAILED
                    
                            # Power off the bandpass filter to prevent further heating
                            self.set_bpf_power_state(False)  # Switch bandpass filter powered off when stopping scanning
                            
                            # Send status advice message to Telescope Manager
                            if self.dig_model.tm_connected == CommunicationStatus.ESTABLISHED:
                                tm_adv = self._construct_status_adv_to_tm()
                                action.set_msg_to_remote(tm_adv)

                            # Request shutdown of the digitiser process due to over-temperature
                            self.request_shutdown(shutdown_reason)

                        # Else if temperature reading is approaching maximum configured temperature, log a warning. 
                        elif temp_reading.temperature > self.dig_model.temp_max - TEMP_WARNING_DELTA:

                            message = f"Digitiser Assembly temperature sensor reading {temp_reading.temperature:.2f} C is approaching maximum configured " \
                                f"temperature {self.dig_model.temp_max:.2f} C."
                            logger.warning(self.set_last_err(message))

                else:
                    message = "Digitiser Assembly temperature sensor reading is stale or unavailable."
                    logger.warning(self.set_last_err(message))
                    
            else:
                message = f"Digitiser Assembly temperature sensor is not connected or communication is not established. Last error: {self._get_temp_sensor_last_error()}"
                logger.warning(self.set_last_err(message))
                
        # Else if the timer is for handling comms retries such as SDR connection retries
        elif event.name.startswith("comms_retry"):

            # Restart the timer to keep retrying periodically
            action.set_timer_action(Action.Timer(name=f"comms_retry", timer_action=5000))

            if self.sdr is None or self.sdr.get_comms_status() != CommunicationStatus.ESTABLISHED:
                self.sdr = SDR(sdr_type=self.dig_model.sdr_type, sdr_config=self.dig_model.sdr_config)  # Retry connecting to the SDR
                self.dig_model.sdr_connected = self.sdr.get_comms_status()

                if self.dig_model.sdr_connected == CommunicationStatus.ESTABLISHED:
                    logger.info("Digitiser successfully connected to SDR device.")
            else:
                self.dig_model.sdr_connected = self.sdr.get_comms_status()

            if not self.dig_model.scanning:
                idle_seconds = (datetime.now(timezone.utc) - self._last_active_dt).total_seconds()
                if idle_seconds >= self._idle_poweroff_seconds:
                    self.set_bpf_power_state(False)  # Switch bandpass filter power off when idle

            if self.temp_sensor is not None and self.temp_sensor.get_comms_status() != CommunicationStatus.ESTABLISHED and self.dig_model.temp_type is not None and self.dig_model.temp_type.lower() != "none":
                self.set_last_err(f"Digitiser retrying temperature sensor connection. Last error: {self._get_temp_sensor_last_error()}")
                self.temp_sensor = Temperature(device=self.dig_model.temp_type, sensor_config=self.dig_model.temp_config)  # Retry connecting to the temperature sensor
                self.dig_model.temp_connected = self.temp_sensor.get_comms_status()

                if self.temp_sensor.get_comms_status() == CommunicationStatus.ESTABLISHED:
                    logger.info("Digitiser successfully connected to temperature sensor.")
                    # Poll the temperature sensor every 5 seconds
                    action.set_timer_action(Action.Timer(name=f"temp_sensor_poll", timer_action=5000, echo_data=None))  
            elif self.temp_sensor is not None:
                self.dig_model.temp_connected = self.temp_sensor.get_comms_status()

        return action

    def _advance_scan_samples_generation(self) -> int:
        """Invalidate queued/in-flight scan sample timers and return the new generation."""
        with self._scan_samples_generation_lock:
            self._scan_samples_generation += 1
            return self._scan_samples_generation

    def _current_scan_samples_generation(self) -> int:
        """ Returns the current scan samples generation. This is used to invalidate queued/in-flight scan 
            sample timers when scanning is stopped or restarted."""

        with self._scan_samples_generation_lock:
            return self._scan_samples_generation

    def _is_current_scan_samples_generation(self, generation) -> bool:
        """ Returns True if the given generation is the current scan samples generation. This is used to invalidate
            queued/in-flight scan sample timers when scanning is stopped or restarted. """
        
        with self._scan_samples_generation_lock:
            return generation == self._scan_samples_generation

    def _get_temp_sensor_last_error(self) -> str:
        if self.temp_sensor is None:
            return "temperature sensor is not configured"
        err = self.temp_sensor.get_last_error()
        return "none" if err is None else repr(err)

    def request_shutdown(self, reason: str) -> None:
        """ Requests shutdown of the digitiser process. Initiated when the digitiser is in an unrecoverable state, 
            such as over-temperature or hardware failure. This method is idempotent and will only request shutdown once.
        """
        if self._shutdown_requested:
            return

        self._shutdown_requested = True
        logger.critical(f"Digitiser requesting process shutdown: {reason}")
        _thread.interrupt_main()

    def stop(self):
        """ Stops the digitiser application and cleans up resources. """
        
        if getattr(self, "temp_sensor", None) is not None:
            self.temp_sensor.stop()
            self.temp_sensor = None
        super().stop()

    def process_status_event(self, event) -> Action:
        """ Processes status update events from the application. This method is called periodically by the App 
            framework to allow the application to send status updates to the Telescope Manager.
        """
        # Refresh the app and processor state (in the digitiser model)
        self.get_app_processor_state()
  
        action = Action()

        # If connected to Telescope Manager, send status advice message
        if self.dig_model.tm_connected == CommunicationStatus.ESTABLISHED:
            tm_adv = self._construct_status_adv_to_tm()
            action.set_msg_to_remote(tm_adv)

        return action

    def get_health_state(self) -> HealthState:
        """ Returns the current health state of this application. This method is called periodically by the App 
            framework to allow the application to report its health state to the Telescope Manager.
        """
        if self.dig_model.sdr_connected != CommunicationStatus.ESTABLISHED:
            message = "Digitiser health status set to FAILED: Software Defined Radio not connected"
            self.set_last_err(message)
            return HealthState.FAILED
        elif self.dig_model.tm_connected != CommunicationStatus.ESTABLISHED:
            message = "Digitiser health status set to DEGRADED: Telescope Manager not connected"
            self.set_last_err(message)
            return HealthState.DEGRADED
        elif self.dig_model.sdp_connected != CommunicationStatus.ESTABLISHED:
            message = "Digitiser health status set to DEGRADED: Science Data Processor not connected"
            self.set_last_err(message)
            return HealthState.DEGRADED
        elif self.temp_sensor is not None:
            if self.temp_sensor.get_comms_status() != CommunicationStatus.ESTABLISHED or self.dig_model.temp_reading is None:
                message = "Digitiser health status set to DEGRADED: Temperature sensor not connected or reading unavailable"
                self.set_last_err(message)
                return HealthState.DEGRADED
            elif self.dig_model.temp_max is not None and self.dig_model.temp_reading.temperature > self.dig_model.temp_max - TEMP_WARNING_DELTA:
                message = f"Digitiser health status set to DEGRADED: Temperature sensor reading {self.dig_model.temp_reading.temperature:.2f} C is approaching maximum configured temperature {self.dig_model.temp_max:.2f} C"
                self.set_last_err(message)
                return HealthState.DEGRADED

        return HealthState.OK
    
    def handle_field_set(self, api_call):
        """ Handles field set api calls.
                : returns: (status, message, value, payload)
        """
        prop_name = 'set_' + api_call['property']
        prop_value = api_call['value']

        # If the property setter exists on the SDR, but comms to the SDR is not established
        if hasattr(self.sdr, prop_name) and not self.dig_model.sdr_connected == CommunicationStatus.ESTABLISHED:
            message = f"Digitiser SDR not connected, cannot set property {prop_name} to {prop_value}"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        try:
            # If the property setter exists on the SDR
            if hasattr(self.sdr, prop_name) and callable(getattr(self.sdr, prop_name)):
                setter = getattr(self.sdr, prop_name)
                result = setter(prop_value)
                # Update the property in the digitiser model for sdr properties
                setattr(self.dig_model, prop_name[4:], result if result is not None else prop_value)

            # Else if the property setter exists on the Digitiser
            elif hasattr(self, prop_name) and callable(getattr(self, prop_name)):
                setter = getattr(self, prop_name)
                setter(prop_value)

            # Else if the property exists on the Digitiser model schema e.g. scanning
            elif prop_name[4:] in self.dig_model.schema.schema:
                setattr(self.dig_model, prop_name[4:], prop_value)

            # Else if the property does not exist on either the SDR, Digitiser or Digitiser model
            elif not hasattr(self.sdr, prop_name) and not hasattr(self, prop_name) and not prop_name[4:] in self.dig_model.schema.schema:
                message = f"Digitiser unknown property {prop_name} with value {prop_value}"
                logger.error(self.set_last_err(message))
                return tm_dig.STATUS_ERROR, message, None, None

            # Else the property exists but is not callable
            else:
                message = f"Digitiser property setter for {prop_name} with value {prop_value} is not callable"
                logger.error(self.set_last_err(message))
                return tm_dig.STATUS_ERROR, message, None, None
        
        except Exception as e:
            if isinstance(e, XHardwareFailure):
                self.dig_model.sdr_connected = CommunicationStatus.NOT_ESTABLISHED
            message = f"Digitiser failed to set property {prop_name} to {prop_value}: {str(e)}"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        message = f"Digitiser set property {prop_name[4:]} to {prop_value}"
        logger.info(message)
        return tm_dig.STATUS_SUCCESS, message, prop_value, None

    def set_scanning(self, value):
        """Update scanning state and reset the stream when acquisition stops."""
        self.dig_model.scanning = value
        if not value:
            discarded = self.sdr.stream_reset()
            logger.info(
                "Digitiser reset SDR stream after scanning stopped; discarded %d buffered samples.",
                discarded,
            )

    def handle_field_get(self, api_call):
        """ Handles field get api calls.
                : returns: (status, message, value, payload)
        """
        prop_name = 'get_' + api_call['property']

        # If the property getter exists on the SDR, but comms to the SDR is not established
        if hasattr(self.sdr, prop_name) and not self.dig_model.sdr_connected == CommunicationStatus.ESTABLISHED:
            message = f"Digitiser SDR not connected, cannot get value for property {prop_name}"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        # Else if the property getter exists on the SDR and is callable
        elif hasattr(self.sdr, prop_name) and callable(getattr(self.sdr, prop_name)):
            getter = getattr(self.sdr, prop_name)

        # Else if the property getter exists on the Digitiser and is callable
        elif hasattr(self, prop_name) and callable(getattr(self, prop_name)):
            getter = getattr(self, prop_name)

        # Else if the property exists on the Digitiser model schema
        elif prop_name[4:] in self.dig_model.schema.schema:
            getter = getattr(self.dig_model, prop_name[4:])

        # Else if the property does not exist on either the SDR, Digitiser or Digitiser model
        elif not hasattr(self.sdr, prop_name) and not hasattr(self, prop_name) and not prop_name[4:] in self.dig_model.schema.schema:
            message = f"Digitiser unknown property {prop_name}"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        # Else the property exists but is not callable
        else:
            message = f"Digitiser property getter for {prop_name} is not callable"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        try:  # Call the getter method
            value = getter() if callable(getter) else getter
        except Exception as e:
            if isinstance(e, XHardwareFailure):
                self.dig_model.sdr_connected = CommunicationStatus.NOT_ESTABLISHED
            message = f"Digitiser failed to get property {prop_name}: {str(e)}"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        return tm_dig.STATUS_SUCCESS, f"Digitiser get {prop_name} value {value}", value, None
  
    def handle_method_call(self, api_call):
        """ Handles method api calls.
                : returns: (status, message, value, payload)
        """
        method = api_call.get('method', None)

        # If the method call exists on the SDR, but comms to the SDR is not established
        if hasattr(self.sdr, method) and not self.dig_model.sdr_connected == CommunicationStatus.ESTABLISHED:
            message = f"Digitiser SDR not connected, cannot call method {method}"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        allowed_keys = {"sample_rate", "time_in_secs"}
        args = {k: v for k, v in api_call.get('params', {}).items() if k in allowed_keys}

        logger.debug(f"Digitiser method call: {method} with params {args}")

        # If the method exists on the SDR
        if hasattr(self.sdr, method):
            call = getattr(self.sdr, method)

        # Else if the method exists on the Digitiser
        elif hasattr(self, method):
            call = getattr(self, method)

        # Else if the method does not exist on either the SDR or Digitiser
        else:
            message = f"Digitiser method {method} not found"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        try:  # Call the method
            if method in (tm_dig.METHOD_GET_AUTO_GAIN, tm_dig.METHOD_SET_AUTO_GAIN):
                result = self._call_auto_gain_with_load_disabled(call, args)
            else:
                result = call(**args) if args is not None else call() if callable(call) else call
        except (XSoftwareFailure, XHardwareFailure) as e:
            if isinstance(e, XHardwareFailure):
                self.dig_model.sdr_connected = CommunicationStatus.NOT_ESTABLISHED
            message = f"Digitiser method {method} failed with exception: {str(e)}"
            logger.error(self.set_last_err(message))
            return tm_dig.STATUS_ERROR, message, None, None

        if method == tm_dig.METHOD_SET_AUTO_GAIN and result is not None:
            self.dig_model.gain = float(result[0] if isinstance(result, tuple) else result)

        # Check whether result is a tuple of (value, payload) or just a value
        if isinstance(result, tuple):
            return tm_dig.STATUS_SUCCESS, f"Digitiser method {method} invoked on SDR", result[0], result[1]
        else:
            return tm_dig.STATUS_SUCCESS, f"Digitiser method {method} invoked on SDR", result, None

    def _call_auto_gain_with_load_disabled(self, call, args):
        """Run auto-gain against the sky/input path, restoring the prior load state."""
        args = args if args is not None else {}
        restore_load = bool(self.dig_model.load_active)

        if restore_load:
            logger.info("Digitiser temporarily disabling LOAD relay for auto gain measurement.")
            self.set_load_active(False)

        try:
            return call(**args)
        finally:
            if restore_load:
                logger.info("Digitiser restoring LOAD relay state after auto gain measurement.")
                self.set_load_active(True)

    def _construct_status_adv_to_tm(self) -> APIMessage:
        """ Constructs a status advice message for the Telescope Manager. """

        tm_adv = APIMessage(api_version=self.tm_api.get_api_version())
        tm_adv.set_json_api_header(
            api_version=self.tm_api.get_api_version(), 
            dt=datetime.now(timezone.utc), 
            from_system=self.dig_model.app.app_name, 
            to_system="tm", 
            entity=self.dig_model.dig_id,
            api_call={
                "msg_type": "adv", 
                "action_code": "set", 
                "property": tm_dig.PROPERTY_STATUS, 
                "value": self.dig_model.to_dict(), 
                "message": "DIG status update"
            })
        return tm_adv

    def _construct_adv_to_sdp(self, status, message, value, payload: bytes) -> APIMessage:
        """ Constructs an advice message to the Science Data Processor with the given sample payload. """

        # Extract sample metadata from the value dictionary
        read_counter = value.get('read_counter', 0)
        num_samples = value.get('num_samples', 0)
        read_start = value.get('read_start', 0)
        read_end = value.get('read_end', 0)

        sdp_adv = APIMessage(api_version=self.sdp_api.get_api_version(), payload=payload)

        sdp_adv.set_json_api_header(
            api_version=self.sdp_api.get_api_version(), 
            dt=datetime.now(timezone.utc), 
            from_system=self.dig_model.app.app_name, 
            to_system="sdp", 
            entity=self.dig_model.dig_id,
            api_call={}
        )
        
        # Construct metadata using the digitiser model and sample read info
        metadata = [   
            {"property": "dig_id", "value": self.dig_model.dig_id},               # Digitiser Id
            {"property": "load", "value": self.dig_model.load_active},            # Bool
            {"property": "center_freq", "value": self.dig_model.center_freq},     # Hz    
            {"property": "sample_rate", "value": self.dig_model.sample_rate},     # Hz
            {"property": "bandwidth", "value": self.dig_model.bandwidth},         # MHz
            {"property": "gain", "value": self.dig_model.gain},                   # dB
            {"property": "read_counter", "value": read_counter},
            {"property": "read_start", "value": datetime.fromtimestamp(read_start, timezone.utc).isoformat()},
            {"property": "read_end", "value": datetime.fromtimestamp(read_end, timezone.utc).isoformat()},
            {"property": "scanning", "value": self.dig_model.scanning}
           ]   
  
        sdp_adv.set_api_call({
            "msg_type": "adv", 
            "action_code": "samples", 
            "status": status if status else "", 
            "message": message if message else "", 
            "metadata": metadata
        })

        return sdp_adv

    def _construct_rsp_to_tm(self, status: int, message: str, value: any, api_msg: dict, api_call: dict) -> APIMessage:
        """ Constructs a Telescope Manager response APIMessage. """

        tm_rsp = APIMessage(api_msg=api_msg, api_version=self.tm_api.get_api_version())
        tm_rsp.switch_from_to()

        tm_rsp_api_call = {
            "msg_type": "rsp", 
            "action_code": api_call['action_code'], 
            "status": status, 
        }
        
        if api_call.get('property') is not None:
            tm_rsp_api_call["property"] = api_call['property']

        if api_call.get('method') is not None:
            tm_rsp_api_call["method"] = api_call['method']

        if api_call.get('params') is not None:
            tm_rsp_api_call["params"] = api_call['params']

        if value is not None:
            tm_rsp_api_call["value"] = value
        
        if api_call.get('obs_data') is not None:
            tm_rsp_api_call["obs_data"] = api_call['obs_data']

        if message is not None:
            tm_rsp_api_call["message"] = message

        tm_rsp.set_api_call(tm_rsp_api_call)       
        return tm_rsp

    def _configure_bpf_control_relay(self, control_type: str):
        """Configures the optional relay GPIO based on the current BPF config and type."""

        if control_type == "load":
            relay_attr = "load_relay"
        elif control_type == "power":
            relay_attr = "power_relay"
        else:
            raise ValueError(f"Unsupported control_type: {control_type}")

        gpio_pin = self.dig_model.get_bpf_control_pin(control_type)
        relay = getattr(self, relay_attr)

        if gpio_pin is None:
            if relay is not None:
                relay.close()
                setattr(self, relay_attr, None)
            self._bpf_control_state[control_type] = None
            self._bpf_control_pin[control_type] = None
            return

        configured_pin = self._bpf_control_pin.get(control_type)
        pin_changed = relay is None or configured_pin != gpio_pin

        if pin_changed:
            if relay is not None:
                relay.close()
            setattr(self, relay_attr, LED(gpio_pin))
            self._bpf_control_state[control_type] = None
            self._bpf_control_pin[control_type] = gpio_pin

    def _switch_bpf_control_relay(self, control_type: str, control_state: bool):
        """Drive optional GPIO control relays to match the current control state."""

        if control_type == "load":
            relay = self.load_relay
        elif control_type == "power":
            relay = self.power_relay
        else:
            raise ValueError(f"Unsupported control_type: {control_type}")

        if relay is None:
            return

        current_state = self._bpf_control_state.get(control_type)
        if current_state is not None and current_state == control_state:
            return

        logger.info(f"Digitiser switching BPF control relay for {control_type} to {'ON' if control_state else 'OFF'}")
        relay.on() if control_state else relay.off()
        self._bpf_control_state[control_type] = control_state

    def set_load_active(self, value):
        """Set the load active state, driving controllable relays if necessary."""
        load_active = fmt_bool(value)

        if self.dig_model.is_bpf_controllable("load") and load_active != self.dig_model.load_active:
            with self._bpf_control_lock:
                self._configure_bpf_control_relay("load")
                self._switch_bpf_control_relay("load", load_active)

        self.dig_model.load_active = load_active

    def set_bpf_power_state(self, value):
        """Set the bandpass filter power to on/off by driving controllable relays if necessary."""
        power_active = fmt_bool(value)

        if self.dig_model.is_bpf_controllable("power"):
            with self._bpf_control_lock:
                self._configure_bpf_control_relay("power")
                self._switch_bpf_control_relay("power", power_active)

def main():
    digitiser = Digitiser()
    digitiser.start() 

    try:
        while True:
             time.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        digitiser.stop()
        digitiser.sdr.close()

if __name__ == "__main__":
    main()
