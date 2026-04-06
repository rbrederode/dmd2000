import logging
import json
import numpy as np
import time
from datetime import datetime, timezone
from rtlsdr import RtlSdr
from gpiozero import LED

from api import tm_dig, sdp_dig
from env.app import App
from ipc.message import APIMessage
from ipc.message import AppMessage
from ipc.action import Action
from ipc.tcp_client import TCPClient
from ipc.tcp_server import TCPServer
from models.app import AppModel
from models.comms import CommunicationStatus, InterfaceType
from models.dig import DigitiserModel
from models.health import HealthState
from sdr.sdr import SDR
from util import log, util
from util.timer import Timer, TimerManager
from util.xbase import XBase, XStreamUnableToExtract, XSoftwareFailure, XHardwareFailure, XAPIValidationFailed

logger = logging.getLogger(__name__)

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
        self.tm_endpoint.connect()
        # Register Telescope Manager interface with the App
        self.register_interface(self.tm_system, self.tm_api, self.tm_endpoint, InterfaceType.ENTITY)
        # Set initial Telescope Manager connection status
        self.dig_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED

        # Science Data Processor Interface
        self.sdp_system = "sdp"
        self.sdp_api = sdp_dig.SDP_DIG()
        # Science Data Processor TCP Client
        self.sdp_endpoint = TCPClient(description=self.sdp_system, queue=self.get_queue(), host=self.get_args().sdp_host, port=self.get_args().sdp_port)
        self.sdp_endpoint.connect()
        # Register Science Data Processor interface with the App
        self.register_interface(self.sdp_system, self.sdp_api, self.sdp_endpoint, InterfaceType.ENTITY)
        # Set initial Science Data Processor connection status
        self.dig_model.sdp_connected = CommunicationStatus.NOT_ESTABLISHED

        self.dig_model.scanning = False # Flag indicating if we are currently scanning for samples (from the SDR)
 
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

        # Initialise the Software Defined Radio (internal) interface
        self.sdr = SDR()
        self.dig_model.sdr_eeprom = self.sdr.get_eeprom_info()
        self.dig_model.sdr_connected = self.sdr.get_comms_status()
        
        # Start timer to periodically retry SDR connection to ensure it connects
        action.set_timer_action(Action.Timer(name=f"sdr_retry", timer_action=5000))

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

        # If currently scanning for an observation, stop scanning due to TM disconnect
        if isinstance(self.dig_model.scanning, dict) and self.dig_model.scanning.get('obs_id', None) is not None:
            logger.warning(f"Digitiser stopping scanning for observation {self.dig_model.scanning.get('obs_id', 'None')} due to Telescope Manager disconnect.")
            self.dig_model.scanning = False

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
                logger.error(f"Digitiser received negative acknowledgement from TM for api call\n{json.dumps(api_call, indent=2)}")

            return action

        # Else if api call is a req or adv msg from the TM
        elif api_call['msg_type'] in ['req', 'adv']:

            scanning = self.dig_model.scanning
            obs_id = scanning.get('obs_id', None) if isinstance(scanning, dict) else None

            # If we are busy scanning samples for an observation and receive a new unrelated set / method api call, reject it
            if obs_id and obs_id !=api_call.get('obs_data', {}).get('obs_id'):
                if api_call['action_code'] in ["set", "method"]:
                    msg = f"Digitiser busy scanning for observation {obs_id} and cannot process unrelated API call until observation is complete"               
                    logger.error(msg + f"\n{json.dumps(api_call, indent=2)}")
                    action.set_msg_to_remote(self._construct_rsp_to_tm(tm_dig.STATUS_ERROR, msg, None, api_msg, api_call))
                    return action
            
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

                    # If scanning was turned on, start reading samples immediately (timer_action=0) 
                    if not scanning and self.dig_model.scanning:
                        # Two timers (1,2) run in parallel, reading samples one after the other, blocking only on the SDR
                        for i in range(1, 3):
                            action.set_timer_action(Action.Timer(name=f"scan_samples_{i}", timer_action=0))
                                
                    else:    
                        # Stop all scan_samples timers (not really necesary since they have a zero timeout)
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
                        logger.warning("Digitiser cannot send samples to Science Data Processor, not connected.")

                        # Send status advice message to Telescope Manager
                        tm_adv = self._construct_status_adv_to_tm()
                        action.set_msg_to_remote(tm_adv)
                        action.set_timer_action(Action.Timer(name=f"tm_adv_timer:{tm_adv.get_timestamp()}", timer_action=self.dig_model.app.msg_timeout_ms, echo_data=tm_adv))

                    elif payload is None:
                        # Wait for scan_samples timer to trigger again
                        logger.warning("Digitiser cannot send samples to Science Data Processor, no payload.")

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
            logger.warning(f"Digitiser stopping scanning for observation {self.dig_model.scanning.get('obs_id', 'None')} due to Science Data Processor disconnect.")
            self.dig_model.scanning = False

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
                logger.error(f"Digitiser received negative acknowledgement from SDP for api call\n{json.dumps(api_call, indent=2)}")

        return action

    def process_timer_event(self, event) -> Action:
        """ Processes timer events.
        """
        logger.debug(f"Digitiser timer event: {event}")

        action = Action()

        # If the timer is for scanning samples from the SDR
        if event.name.startswith("scan_samples"):
            
            # Invoke the read_samples method to read samples from the SDR
            result = self.handle_method_call({"method": "read_samples", "params": {}})
            status, message, value, payload = util.unpack_result(result)

            # If the digitiser is set to scan samples
            if self.dig_model.scanning:

                # Start the same scan_samples timer immediately if it was successful, else wait 1000 milliseconds before retrying
                wait = 0 if status == tm_dig.STATUS_SUCCESS else 1000 
                action.set_timer_action(Action.Timer(name=event.name, timer_action=wait)) 

            if self.dig_model.sdp_connected == CommunicationStatus.ESTABLISHED and payload is not None:
                # Prepare adv msg to send samples to sdp
                sdp_adv = self._construct_adv_to_sdp(status, message, value, payload.tobytes())
                action.set_msg_to_remote(sdp_adv)
                action.set_timer_action(Action.Timer(name=f"sdp_adv_timer:{sdp_adv.get_timestamp()}", timer_action=self.dig_model.app.msg_timeout_ms, echo_data=sdp_adv))

            elif payload is None:
                # Wait for scan_samples timer to trigger again
                logger.warning(f"Digitiser cannot send samples to Science Data Processor on {event.name}, no payload after reading samples.")
        
        # Else if the timer is for handling sdp adv timeouts
        elif event.name.startswith("sdp_adv_timer"):

            # Simply log a warning that the SDP did not acknowledge the samples advice
            logger.warning(f"Digitiser timed out waiting for acknowledgement from SDP for samples advice {event}")

        # Else if the timer is for handling comms to the SDR
        elif event.name.startswith("sdr_retry"):

            # Restart the timer to keep retrying periodically
            action.set_timer_action(Action.Timer(name=f"sdr_retry", timer_action=5000))

            if self.dig_model.sdr_connected == CommunicationStatus.NOT_ESTABLISHED:
                self.sdr = SDR()  # Retry connecting to the SDR
                self.dig_model.sdr_connected = self.sdr.get_comms_status()

                if self.dig_model.sdr_connected == CommunicationStatus.ESTABLISHED:
                    logger.info("Digitiser successfully connected to SDR device.")

        return action

    def process_status_event(self, event) -> Action:
        """ Processes status update events.
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
        """ Returns the current health state of this application.
        """
        if self.dig_model.tm_connected != CommunicationStatus.ESTABLISHED:
            return HealthState.DEGRADED
        elif self.dig_model.sdp_connected != CommunicationStatus.ESTABLISHED:
            return HealthState.DEGRADED
        elif self.sdr is None or self.sdr.get_comms_status() != CommunicationStatus.ESTABLISHED:
            return HealthState.FAILED
        else:
            return HealthState.OK
    
    def handle_field_set(self, api_call):
        """ Handles field set api calls.
                : returns: (status, message, value, payload)
        """
        prop_name = 'set_' + api_call['property']
        prop_value = api_call['value']

        # If the property setter exists on the SDR, but comms to the SDR is not established
        if hasattr(self.sdr, prop_name) and not self.dig_model.sdr_connected == CommunicationStatus.ESTABLISHED:
            logger.error(f"Digitiser SDR not connected, cannot set property {prop_name} to {prop_value}")
            return tm_dig.STATUS_ERROR, f"Digitiser SDR not connected, cannot set property {prop_name}", None, None

        try:
            # If the property setter exists on the SDR
            if hasattr(self.sdr, prop_name) and callable(getattr(self.sdr, prop_name)):
                setter = getattr(self.sdr, prop_name)
                setter(prop_value)
                # Update the property in the digitiser model for sdr properties
                setattr(self.dig_model, prop_name[4:], prop_value)

            # Else if the property setter exists on the Digitiser
            elif hasattr(self, prop_name) and callable(getattr(self, prop_name)):
                setter = getattr(self, prop_name)
                setter(prop_value)

            # Else if the property exists on the Digitiser model schema e.g. scanning
            elif prop_name[4:] in self.dig_model.schema.schema:
                setattr(self.dig_model, prop_name[4:], prop_value)

            # Else if the property does not exist on either the SDR, Digitiser or Digitiser model
            elif not hasattr(self.sdr, prop_name) and not hasattr(self, prop_name) and not prop_name[4:] in self.dig_model.schema.schema:
                logger.error(f"Digitiser unknown property {prop_name} with value {prop_value}")
                return tm_dig.STATUS_ERROR, f"Digitiser unknown property {prop_name}", None, None

            # Else the property exists but is not callable
            else:
                logger.error(f"Digitiser property setter for {prop_name} with value {prop_value} is not callable")
                return tm_dig.STATUS_ERROR, f"Digitiser property {prop_name} is not callable", None, None
        
        except Exception as e:
            logger.exception(f"Digitiser failed to set property {prop_name} to {prop_value}: {e}")
            return tm_dig.STATUS_ERROR, f"Digitiser failed to set property {prop_name} to {prop_value}: {e}", None, None

        logger.info(f"Digitiser set property {prop_name[4:]} to {prop_value}")
        return tm_dig.STATUS_SUCCESS, f"Digitiser set property {prop_name} to {prop_value}", prop_value, None
    
    def handle_field_get(self, api_call):
        """ Handles field get api calls.
                : returns: (status, message, value, payload)
        """
        prop_name = 'get_' + api_call['property']

        # If the property getter exists on the SDR, but comms to the SDR is not established
        if hasattr(self.sdr, prop_name) and not self.dig_model.sdr_connected == CommunicationStatus.ESTABLISHED:
            logger.error(f"Digitiser SDR not connected, cannot get value for property {prop_name}")
            return tm_dig.STATUS_ERROR, f"Digitiser SDR not connected, cannot get value for property {prop_name}", None, None

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
            logger.error(f"Digitiser unknown property {prop_name}")
            return tm_dig.STATUS_ERROR, f"Digitiser unknown property {prop_name}", None, None

        # Else the property exists but is not callable
        else:
            logger.error(f"Digitiser property getter for {prop_name} is not callable")
            return tm_dig.STATUS_ERROR, f"Digitiser property {prop_name} is not callable", None, None

        try:  # Call the getter method
            value = getter() if callable(getter) else getter
        except Exception as e:
            logger.error(f"Digitiser failed to get property {prop_name}: {e}")
            return tm_dig.STATUS_ERROR, f"Digitiser failed to get property {prop_name}: {e}", None, None

        return tm_dig.STATUS_SUCCESS, f"Digitiser get {prop_name} value {value}", value, None
  
    def handle_method_call(self, api_call):
        """ Handles method api calls.
                : returns: (status, message, value, payload)
        """
        method = api_call.get('method', None)

        # If the method call exists on the SDR, but comms to the SDR is not established
        if hasattr(self.sdr, method) and not self.dig_model.sdr_connected == CommunicationStatus.ESTABLISHED:
            logger.error(f"Digitiser SDR not connected, cannot call method {method}")
            return tm_dig.STATUS_ERROR, f"Digitiser SDR not connected, cannot call method {method}", None, None

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
            logger.error(f"Digitiser method {method} not found")
            return tm_dig.STATUS_ERROR, f"Digitiser method {method} not found", None, None

        try:  # Call the method
            result = call(**args) if args is not None else call() if callable(call) else call
        except (XSoftwareFailure, XHardwareFailure) as e:
            logger.error(f"Digitiser method {method} failed with exception: {e}")
            return tm_dig.STATUS_ERROR, f"Digitiser method {method} failed with exception: {e}", None, None

        # Check whether result is a tuple of (value, payload) or just a value
        if isinstance(result, tuple):
            return tm_dig.STATUS_SUCCESS, f"Digitiser method {call.__name__} invoked on SDR", result[0], result[1]
        else:
            return tm_dig.STATUS_SUCCESS, f"Digitiser method {call.__name__} invoked on SDR", result, None

    def _construct_status_adv_to_tm(self) -> APIMessage:
        """ Constructs a status advice message for the Telescope Manager.
        """

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
        """ Constructs an advice message to the Science Data Processor with the given sample payload.
        """

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
            {"property": "load", "value": self.dig_model.load},                   # Bool
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
        """ Constructs a Telescope Manager response APIMessage.
        """
        tm_rsp = APIMessage(api_msg=api_msg, api_version=self.tm_api.get_api_version())
        tm_rsp.switch_from_to()

        tm_rsp_api_call = {
            "msg_type": "rsp", 
            "action_code": api_call['action_code'], 
            "status": status, 
        }
        
        if api_call.get('property') is not None:
            tm_rsp_api_call["property"] = api_call['property']

        if value is not None:
            tm_rsp_api_call["value"] = value
        
        if api_call.get('obs_data') is not None:
            tm_rsp_api_call["obs_data"] = api_call['obs_data']

        if message is not None:
            tm_rsp_api_call["message"] = message

        tm_rsp.set_api_call(tm_rsp_api_call)       
        return tm_rsp

def main():
    digitiser = Digitiser()
    digitiser.start() 

    led = LED(17)   # define LED pin according to BCM Numbering

    try:
        while True:
            led.on()    # turn on LED
            time.sleep(0.5)
            led.off()   # turn off LED
            time.sleep(0.5)

    except KeyboardInterrupt:
        pass
    finally:
        digitiser.stop()
        digitiser.sdr.close()

if __name__ == "__main__":
    main()