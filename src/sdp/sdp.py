from datetime import datetime, timezone
import json
import logging
import numpy as np
import os
from pathlib import Path
from queue import Queue
import re
import time
import threading

from api import sdp_dig, tm_sdp
from env.app import App
from ipc.message import APIMessage
from ipc.action import Action
from ipc.message import AppMessage
from ipc.tcp_client import TCPClient
from ipc.tcp_server import TCPServer
from models.base import BaseModel
from models.comms import CommunicationStatus, InterfaceType
from models.dig import DigitiserModel, DigitiserList
from models.health import HealthState
from models.pipeline import StepConfig, StepType, PipelineConfig
from models.scan import ScanModel, ScanState, ScanType
from models.sdp import ScienceDataProcessorModel
from obs.scan import Scan
from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory
from sdp.signal_display import SignalDisplay
from util import log, util
from util.xbase import XBase, XStreamUnableToExtract, XSoftwareFailure

#SAMPLES_DIR = '/Volumes/DATA SDD/Alston Radio Telescope/Samples/Home'  # Directory to store samples
SAMPLES_DIR = '/Users/r.brederode/samples'  # Directory to store samples

logger = logging.getLogger(__name__)

class SDP(App):

    sdp_model = ScienceDataProcessorModel(sdp_id="sdp001")
    pipeline_factory = None  

    def __init__(self, app_name: str = "sdp"):

        super().__init__(app_name=app_name, app_model = self.sdp_model.app)

        # Telescope Manager interface (TBD)
        self.tm_system = "tm"
        self.tm_api = tm_sdp.TM_SDP()
        # Telescope Manager TCP Server
        self.tm_endpoint = TCPServer(description=self.tm_system, queue=self.get_queue(), host=self.get_args().tm_host, port=self.get_args().tm_port)
        # Register Telescope Manager interface with the App
        self.register_interface(self.tm_system, self.tm_api, self.tm_endpoint, InterfaceType.APP_APP)
        # Set initial Telescope Manager connection status
        self.sdp_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        
        # Digitiser interface
        self.dig_system = "dig"
        self.dig_api = sdp_dig.SDP_DIG()
        # Digitiser TCP Server
        self.dig_endpoint = TCPServer(description=self.dig_system, queue=self.get_queue(), host=self.get_args().dig_host, port=self.get_args().dig_port)
        # Register Digitiser interface with the App
        self.register_interface(self.dig_system, self.dig_api, self.dig_endpoint, InterfaceType.ENTITY_DRIVER)
        # Entity drivers maintain comms status per entity, so no need to initialise comms status here

        self.scan_q = Queue()            # Queue of sky scans being processed and displayed
        self.cal_q = Queue()             # Queue of calibration scans to apply to sky scans
        self.signal_displays = {}        # Dictionary to hold SignalDisplay objects for each digitiser

        self._rlock = threading.RLock()  # Lock for thread-safe access to shared resources
        self._dig_locks = {}             # Dictionary of threading locks, one per digitiser ID

    def add_args(self, arg_parser): 
        """ Specifies the science data processors command line arguments.
        """
        super().add_args(arg_parser)

        arg_parser.add_argument("--tm_host", type=str, required=False, help="TCP host to listen on for Telescope Manager commands", default="localhost")
        arg_parser.add_argument("--tm_port", type=int, required=False, help="TCP port for Telescope Manager commands", default=50001)

        arg_parser.add_argument("--dig_host", type=str, required=False, help="TCP host to listen on for Digitiser commands", default="localhost")
        arg_parser.add_argument("--dig_port", type=int, required=False, help="TCP server port for upstream Digitiser transport", default=60000)

        arg_parser.add_argument("--scan_store_dir", type=str, required=False, help="Directory to store captured scan samples", default=SAMPLES_DIR)

    def process_init(self) -> Action:
        """ Processes initialisation event on startup once all app processors are running.
            Runs in single threaded mode and switches to multi-threading mode after this method completes.
        """
        logger.debug(f"SDP initialisation event")

        action = Action()

        # Load Digitiser configuration from disk
        # Config file is located in ./config/<profile>/<model>.json
        # Config file defines initial list of digitisers to be processed by the SDP
        input_dir = f"./config/{self.get_args().profile}"
        filename = "DigitiserList.json"

        try:
            dig_store = self.sdp_model.dig_store.load_from_disk(input_dir=input_dir, filename=filename)
        except FileNotFoundError:
            dig_store = None
            logger.warning(f"Science Data Processor could not load Digitiser configuration from directory {input_dir} file {filename}. File not found.")

        if dig_store is not None:
            self.sdp_model.dig_store = dig_store
            logger.info(f"Science Data Processor loaded {len(self.sdp_model.dig_store.dig_list)} digitiser configurations from directory {input_dir} file {filename}")
        else:
            self.sdp_model.dig_store = DigitiserList()
            logger.info(f"Science Data Processor using default Digitiser configuration as file not found in directory {input_dir} file {filename}")

        # Load processing pipeline factory configuration from disk
        filename = "PipelineConfig.json"

        try:
            pipeline_config = self.sdp_model.pipeline_config.load_from_disk(input_dir=input_dir, filename=filename)
        except FileNotFoundError:
            pipeline_config = None
            logger.warning(f"Science Data Processor could not load processing pipeline factory configuration from directory {input_dir} file {filename}. File not found.")

        if pipeline_config is not None:
            self.sdp_model.pipeline_config = pipeline_config
            logger.info(f"Science Data Processor loaded processing pipeline factory configuration from directory {input_dir} file {filename}:\n{self.sdp_model.pipeline_config}")
        else:
            self.sdp_model.pipeline_config = PipelineConfig()
            logger.info(f"Science Data Processor using default processing pipeline factory configuration as file not found in directory {input_dir} file {filename}:\n{self.sdp_model.pipeline_config}")

        SDP.pipeline_factory = ProcessingPipelineFactory(pipeline_config=self.sdp_model.pipeline_config)

        # Start server endpoints and connect client endpoints to interfaces
        self.tm_endpoint.start()
        self.dig_endpoint.start()

        return action

    def _get_dig_lock(self, dig_id: str) -> threading.RLock:
        """Get or create a threading lock for a specific digitiser ID."""
        if dig_id not in self._dig_locks:
            self._dig_locks[dig_id] = threading.RLock()
        return self._dig_locks[dig_id]

    def get_dig_entity(self, event) -> (str, BaseModel):
        """ Determines the digitiser entity ID based on the remote address of a ConnectEvent, DisconnectEvent, or DataEvent.
            Returns a tuple of the entity ID and entity if found, else None, None.
        """
        remote_addr = event.remote_addr[0] if event.remote_addr else 'None'
        logger.debug(f"Science Data Processor finding digitiser entity ID for remote address: {remote_addr}")

        for digitiser in self.sdp_model.dig_store.dig_list:

            if isinstance(digitiser.app.arguments, dict) and "local_host" in digitiser.app.arguments:

                if digitiser.app.arguments["local_host"] == remote_addr:
                    logger.debug(f"Science Data Processor found digitiser entity ID: {digitiser.dig_id} for remote address: {remote_addr}")
                    return digitiser.dig_id, digitiser
            else:
                logger.warning(f"SDP found {digitiser.dig_id} not configured with valid local_host argument matching against remote address: {remote_addr}")

        return None, None

    def process_dig_entity_connected(self, event, entity:BaseModel) -> Action:
        """ Processes Digitiser connected events.
        """
        logger.info(f"Science Data Processor connected to Digitiser entity on {event.remote_addr}\n{entity}")
        
        digitiser: DigitiserModel = entity
        digitiser.sdp_connected = CommunicationStatus.ESTABLISHED
        digitiser.last_update = datetime.now(timezone.utc)

        action = Action()
        return action

    def process_dig_entity_disconnected(self, event, entity: BaseModel) -> Action:
        """ Processes Digitiser disconnected events.
        """
        logger.info(f"Science Data Processor disconnected from Digitiser entity on {event.remote_addr}\n{entity}")

        digitiser: DigitiserModel = entity

        digitiser.sdp_connected = CommunicationStatus.NOT_ESTABLISHED
        digitiser.scanning = False
        digitiser.last_update = datetime.now(timezone.utc)

        action = Action()
        return action
        
    def process_dig_entity_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray, entity: BaseModel) -> Action:
        """ Processes api messages received on the Digitiser service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"Science Data Processor received digitiser {api_call['msg_type']} msg, action code: {api_call['action_code']}, " + \
            f"property: {api_call.get('property','')} on entity: {api_msg['entity']}")

        digitiser: DigitiserModel = entity
        dig_id = digitiser.dig_id
        
        status, message = sdp_dig.STATUS_SUCCESS, "Acknowledged"
        action = Action()

        # If we are receiving samples from the digitiser, load them into the current scan
        if api_call['action_code'] == sdp_dig.ACTION_CODE_SAMPLES:

            # Extract metadata from the api call and convert to dictionary for direct access
            metadata = api_call.get('metadata', [])
            meta_dict = {item.get("property"): item.get("value") for item in metadata or []}

            center_freq   = meta_dict.get(sdp_dig.PROPERTY_CENTER_FREQ, 0.0)
            sample_rate   = meta_dict.get(sdp_dig.PROPERTY_SAMPLE_RATE, 0.0)
            gain          = meta_dict.get(sdp_dig.PROPERTY_SDR_GAIN, 0.0)
            load          = meta_dict.get(sdp_dig.PROPERTY_LOAD, False)
            scanning      = meta_dict.get(sdp_dig.PROPERTY_SCANNING)
            read_counter  = meta_dict.get(sdp_dig.PROPERTY_READ_COUNTER, 0)
            read_start    = datetime.fromisoformat(meta_dict.get(sdp_dig.PROPERTY_READ_START))
            read_end      = datetime.fromisoformat(meta_dict.get(sdp_dig.PROPERTY_READ_END))
 
            # Get the threading lock specific to this digitiser
            dig_lock = self._get_dig_lock(dig_id)

            with dig_lock:

                obs_id = digitiser.scanning.get('obs_id') if isinstance(digitiser.scanning, dict) else None
                tgt_idx = digitiser.scanning.get('tgt_idx') if isinstance(digitiser.scanning, dict) else None
                freq_scan = digitiser.scanning.get('freq_scan') if isinstance(digitiser.scanning, dict) else None

                odt_initiated = obs_id[:3].upper() == "ODT" if isinstance(obs_id, str) and len(obs_id) >= 3 else False

                # Discard digitiser samples if the SDP scan configuration does not match the sample metadata
                # Respond with success to avoid triggering retries from the digitiser, but log a warning and discard the samples
                if not digitiser.scanning or (odt_initiated and digitiser.scanning != scanning) or center_freq != digitiser.center_freq or \
                    sample_rate != digitiser.sample_rate or gain != digitiser.gain or load != digitiser.load:
                    
                    diff = self._diff_dig_metadata(digitiser, meta_dict)
                    msg = f"Science Data Processor received samples from {digitiser.dig_id} that do not match the SDP scan configuration."
                    logger.warning(msg + f"\n{diff}")
                    
                    status, message = sdp_dig.STATUS_SUCCESS, msg
                    dig_rsp = self._construct_rsp_to_dig(status, message, api_msg, api_call)
                    action.set_msg_to_remote(dig_rsp)
                    return action
                
                logger.debug(f"Science Data Processor received digitiser samples message with metadata:\n{metadata}")
                       
                match = None

                # Find pending scans for this digitiser
                pending_scans = [s for s in list(self.scan_q.queue) if s.get_status() in [ScanState.EMPTY, ScanState.WIP] and s.get_dig_id() == dig_id]
                abort_scans = []

                self.sdp_model.scans_wip = len(pending_scans)

                for scan in pending_scans:
                    start_idx, end_idx = scan.get_start_end_idx()
                    if read_counter >= start_idx and read_counter <= end_idx:

                        # Verify that the digitiser metadata still matches the scan parameters
                        if scan.scan_model.center_freq == center_freq and scan.scan_model.sample_rate == sample_rate and scan.scan_model.load == load and \
                           scan.scan_model.channels == digitiser.channels and scan.scan_model.duration == digitiser.scan_duration:
                            match = scan
                            break
                        else:
                            logger.warning(f"Science Data Processor aborting scan id {scan.scan_model.scan_id}: scan parameters no longer match digitiser metadata {metadata} vs scan:\n" + \
                                f"{scan}")
                            abort_scans.append(scan)

                    # If the Digitiser read_counter is greater than the scan range by the scan duration or the digitiser has reset itself, abort the scan
                    elif read_counter > end_idx + scan.scan_model.duration or read_counter < start_idx:
                        abort_scans.append(scan)  # add it to the abort list
                        logger.warning(f"Science Data Processor aborting scan id: {scan.scan_model.scan_id}, dig read_counter {read_counter} not consistent with scan indexes {start_idx}-{end_idx}")
                        
                        # If the digitiser read_counter is 0 or 1 (the digitiser was restarted), reset the scan_iter counter to match the digitiser
                        if read_counter in [0,1]:
                            Scan.reset_scan_iter_counter(obs_id)

                # If we found scans to abort, do so
                for scan in abort_scans:
                    self._abort_scan(scan)

                # If no matching scan was found
                if match is None:

                    # Create a new scan model based on the digitiser metadata
                    # Metadata excludes scan_iter which is auto incremented as needed by the Scan class
                    scan_model = ScanModel(
                        dig_id=dig_id,
                        obs_id=obs_id,
                        tgt_idx=tgt_idx,
                        freq_scan=freq_scan,
                        scan_type=ScanType.LOAD if load else ScanType.SKY,
                        start_idx=read_counter,
                        duration=digitiser.scan_duration,
                        channels=digitiser.channels,
                        sample_rate=sample_rate,
                        center_freq=center_freq,
                        gain=gain,
                        load=load,
                        status=ScanState.EMPTY)

                    scan = Scan(scan_model=scan_model)

                    # Create a signal processing pipeline using the pipeline factory and associate it with the scan
                    pipeline = SDP.pipeline_factory.create_pipeline(scan, self.scan_q, self.cal_q) if SDP.pipeline_factory else None
                    scan.set_pipeline(pipeline)

                    self.scan_q.put(scan)
                    self.sdp_model.scans_created += 1
                    match = scan

                    logger.debug(f"Science Data Processor created new scan: {scan}")

                if match is not None:              
                    logger.debug(f"Science Data Processor loading samples into scan: {match}")

                    # Ensure the scan is added to the SDP model processing scans list (maintains a single scan per digitiser)
                    self.sdp_model.add_scan(match.scan_model)

                    # Convert payload to complex64 numpy array
                    iq_samples = np.frombuffer(payload, dtype=np.complex64)
                
                    if match.load_samples(sec=(read_counter - match.get_start_end_idx()[0] + 1), 
                            iq=iq_samples,
                            read_start=read_start,
                            read_end=read_end):
                        status = sdp_dig.STATUS_SUCCESS
                        message = f"Science Data Processor loaded samples into scan id: {match.scan_model.scan_id}"
                    else:
                        status = sdp_dig.STATUS_ERROR
                        message = f"Science Data Processor failed to load samples into scan id: {match.scan_model.scan_id}"
                        if match.scan_model.load_failures >= 3:
                            logger.error(f"Science Data Processor aborting scan id: {match.scan_model.scan_id} has exceeded maximum load failures: {match.scan_model.load_failures}")
                            self._abort_scan(match)

                    if match.get_status() == ScanState.COMPLETE:
                        logger.info(f"Science Data Processor scan is complete: {match}")
                        self._complete_scan(match)

                        if self.sdp_model.tm_connected == CommunicationStatus.ESTABLISHED:

                            # Send scan complete advice to Telescope Manager
                            tm_adv = self._construct_scan_complete_adv_to_tm(match)
                            action.set_msg_to_remote(tm_adv)
                            action.set_timer_action(Action.Timer(name=f"tm_adv_timer_retry:{tm_adv.get_timestamp()}", timer_action=self.sdp_model.app.msg_timeout_ms, echo_data=tm_adv))
                    
        dig_rsp = self._construct_rsp_to_dig(status, message, api_msg, api_call)
        action.set_msg_to_remote(dig_rsp)

        return action

    def process_tm_connected(self, event) -> Action:
        """ Processes Telescope Manager connected events.
        """
        logger.info(f"Science Data Processor connected to Telescope Manager: {event.remote_addr}")

        self.sdp_model.tm_connected = CommunicationStatus.ESTABLISHED
        self.sdp_model.last_update = datetime.now(timezone.utc)
        
        action = Action()
        
        # Send initial status advice message to Telescope Manager
        tm_adv = self._construct_status_adv_to_tm()
        action.set_msg_to_remote(tm_adv)
        return action

    def process_tm_disconnected(self, event) -> Action:
        """ Processes Telescope Manager disconnected events.
        """
        logger.info(f"Science Data Processor disconnected from Telescope Manager: {event.remote_addr}")

        self.sdp_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED
        self.sdp_model.last_update = datetime.now(timezone.utc)

    def process_tm_msg(self, event, api_msg: dict, api_call: dict, payload: bytearray) -> Action:
        """ Processes api messages received on the Telescope Manager service access point (SAP)
            API messages are already translated and validated before being passed to this method.
        """
        logger.info(f"Science Data Processor received Telescope Manager {api_call['msg_type']} msg, action code: {api_call['action_code']}, property {api_call.get('property','')}")

        action = Action()

         # If api call is a rsp msg from the TM
        if api_call['msg_type'] == 'rsp':

            # Stop the corresponding adv timer if applicable
            dt = api_msg.get("timestamp")
            if dt:
                action.set_timer_action(Action.Timer(name=f"tm_adv_timer_retry:{dt}", timer_action=Action.Timer.TIMER_STOP))
                action.set_timer_action(Action.Timer(name=f"tm_adv_timer_final:{dt}", timer_action=Action.Timer.TIMER_STOP))
            
            if api_call.get('status') == tm_sdp.STATUS_ERROR:
                logger.error(self.set_last_err(f"Science Data Processor received negative acknowledgement from TM for api call\n{json.dumps(api_call, indent=2)}"))

            return action

        # Else if api call is a req or adv msg from the TM
        elif api_call['msg_type'] in ['req', 'adv']:

            # Dispatch the API Call to a handler method
            dispatch = {
                "set": self.handle_field_set,
                "get": self.handle_field_get,
            }

            # Invoke set or get handler to process the api call
            result = dispatch.get(api_call['action_code'], lambda x: None)(api_call)
            status, message, value, payload = util.unpack_result(result)
              
        tm_rsp = self._construct_rsp_to_tm(status, message, value, api_msg, api_call)
        action.set_msg_to_remote(tm_rsp)

        return action

    def process_timer_event(self, event) -> Action:
        """ Processes timer events.
        """
        logger.debug(f"SDP timer event: {event}")

        action = Action()

        # Handle an initial request msg timer retry e.g. sdp_adv_timer_retry:<timestamp> 
        if "adv_timer_retry" in event.name:
            
            logger.warning(f"Science Data Processor timed out waiting for response msg {event.name}, retrying advice msg")

            # Resend the API advice if the timer user_ref is set (containing the original advice message)
            if event.user_ref is not None:

                adv_msg: APIMessage = event.user_ref
                final_timer = re.sub(r':.*$', f':{adv_msg.get_timestamp()}', event.name.replace("retry", "final"))

                action.set_msg_to_remote(adv_msg)
                action.set_timer_action(Action.Timer(
                    name=final_timer, 
                    timer_action=self.sdp_model.app.msg_timeout_ms,
                    echo_data=adv_msg))

        # Handle a final request msg timer e.g. dig002_req_timer_final:<timestamp> or sdp002_req_timer_final:<timestamp>
        elif "adv_timer_final" in event.name:
            
            logger.warning(f"Science Data Processor timed out waiting for response msg after final retry, aborting retries for {event.name}")

        return action

    def process_status_event(self, event) -> Action:
        """ Processes status update events.
        """
        self.get_app_processor_state()

        action = Action()

        # If connected to Telescope Manager, send status advice message
        if self.sdp_model.tm_connected == CommunicationStatus.ESTABLISHED:
            tm_adv = self._construct_status_adv_to_tm()
            action.set_msg_to_remote(tm_adv)

        return action

    def get_health_state(self) -> HealthState:
        """ Returns the current health state of this application.
        """
        if self.sdp_model.tm_connected != CommunicationStatus.ESTABLISHED:
            return HealthState.DEGRADED
        #elif any(dig.tm_connected != CommunicationStatus.ESTABLISHED for dig in self.sdp_model.dig_store.dig_list):
        #    return HealthState.DEGRADED
        else:
            return HealthState.OK

    def set_signal_display(self, value:dict) -> bool:
        """ Sets the signal display state for a digitiser.
            value is a dictionary with keys 'dig_id' and 'active' (boolean)
        """
        logger.info(f"Science Data Processor setting signal display with value: {value}")

        dig_id = value.get('dig_id')
        active = value.get('active', False)

        if dig_id is None:
            logger.error(f"Science Data Processor signal display request missing dig_id in value: {value}")
            return False

        if dig_id not in self.signal_displays:
            self.signal_displays[dig_id] = SignalDisplay(dig_id=dig_id)

        signal_display = self.signal_displays[dig_id]

        logger.info(f"Science Data Processor signal display {signal_display} set active={active}")

        signal_display.set_is_active(active)
        logger.info(f"Science Data Processor set signal display for digitiser {dig_id} to active={active}")

        return True

    def set_scan_config(self, value: dict) -> bool:
        """ Sets the scan configuration for a digitiser.
            This is needed to prepare the correct load (baseline) scan before samples for a sky scan start arriving.      
            params:     value is a dictionary with keys dig_id, center_freq, sample_rate, bandwidth, gain, duration, 
                        load, channels, obs_id, tgt_idx, freq_scan, scan_iter.
            returns:    True if the scan config was successfully applied, False otherwise
        """
        logger.info(f"Science Data Processor setting scan config with value: {value}")

        obs_id = value.get('obs_id') if value is not None and isinstance(value, dict) else None
        dig_id = value.get('dig_id') if value is not None and isinstance(value, dict) else None
        dig = self.sdp_model.dig_store.get_dig_by_id(dig_id) if dig_id is not None else None
  
        if dig is None or obs_id is None:
            logger.error(f"Science Data Processor could not set scan config for digitiser {'None' if dig_id is None else dig_id}" + \
                f" and observation {'None' if obs_id is None else obs_id}\n{value}")
            return False

        user_initiated = obs_id[:3].upper() == "USR" if isinstance(obs_id, str) and len(obs_id) >= 3 else False

        # If this is a user-initiated scan config and digitiser already scanning, then decline if the current scanning is an observation-initiated (ODT) scan
        if user_initiated and dig.scanning:
            if isinstance(dig.scanning, dict) and dig.scanning.get('obs_id') and str(obs_id)[:3] != str(dig.scanning.get('obs_id'))[:3]:
                logger.warning(f"Science Data Processor declining scan config for digitiser {dig_id} because the digitiser is already scanning samples for obs_id {dig.scanning.get('obs_id')}.\n{value}")
                return False
            
        # Loop through the key value pairs in value dict
        for key, val in value.items():
            if key in ['dig_id', 'obs_id']:
                continue  # skip these keys as they are identifiers and not attributes

            if hasattr(dig, key):
                setattr(dig, key, val)
                logger.info(f"Science Data Processor set digitiser {dig_id} attribute {key} to {val}")
            else:
                logger.warning(f"Science Data Processor received unknown scan config key {key} for digitiser {dig_id} in value: {value}")

        load_found = False

        # Check if we already have an equivalent completed load scan in the load queue for this digitiser
        cal_scans = [s for s in list(self.cal_q.queue) if s.get_dig_id() == dig_id and s.get_scan_type() == ScanType.LOAD]
        purge = True if len(cal_scans) > 5 else False  # Purge load scans from the load queue if there are more than 5 in the queue

        for cal in cal_scans:

            if cal.scan_model.center_freq == dig.center_freq and cal.scan_model.sample_rate == dig.sample_rate and cal.scan_model.gain == dig.gain and \
               cal.scan_model.channels == dig.channels and cal.scan_model.duration == dig.scan_duration and cal.scan_model.status == ScanState.COMPLETE:
                load_found = True
                logger.info(f"Science Data Processor found equivalent load scan in load queue for digitiser {dig_id} and observation {obs_id}:\n{cal}")
                break  # we already have an equivalent load scan in the load queue, so we can keep it and skip preparing a new one
            else:
                if purge:
                    self.cal_q.queue.remove(cal)  # remove non-equivalent load scans from the load queue to prevent build up of stale load scans
                    self.cal_q.task_done()
                    logger.info(f"Science Data Processor purged non-equivalent load scan from load queue for digitiser {dig_id} and observation {obs_id}:\n{cal}")

        if not load_found:
            # Check whether a previous load scan is available in the sample store that matches the digitiser's scan parameters
            scan_store_dir = os.path.expanduser(self.get_args().scan_store_dir)

            file_prefix = util.gen_file_prefix(
                dt=None,
                entity_id=dig_id,
                gain=dig.gain,
                duration=dig.scan_duration,
                sample_rate=dig.sample_rate,
                center_freq=dig.center_freq,
                channels=dig.channels, 
                scan_type=ScanType.LOAD)

            load_files = [f for f in os.listdir(scan_store_dir) if file_prefix in f and f.endswith('spr.csv')]
            logger.info(f"Science Data Processor found {len(load_files)} load scan files in sample store matching prefix {file_prefix} for digitiser {dig_id} and observation {obs_id}")
            load_files = sorted(load_files, key=lambda f: os.path.getctime(os.path.join(scan_store_dir, f)), reverse=True) if len(load_files) > 0 else []
            load_file = load_files[0].removesuffix('spr.csv') if len(load_files) > 0 else None

            if load_file is not None:
                load_scan = Scan.from_disk(file_prefix=load_file, input_dir=scan_store_dir, include_iq=False)
                
                if load_scan is not None and load_scan.is_load_scan():
                    load_found = True
                    self.cal_q.put(load_scan)
                    logger.info(f"Science Data Processor found equivalent load scan in {self.get_args().scan_store_dir} for digitiser {dig_id} and observation {obs_id}:\n{load_scan}")

        if not load_found:
            # Extract target index and frequency scan index from the digitiser scanning metadata if available, else default to -1 for both
            tgt_idx = dig.scanning.get("tgt_idx", -1) if dig.scanning is not None and isinstance(dig.scanning, dict) else -1
            freq_scan = dig.scanning.get("freq_scan", -1) if dig.scanning is not None and isinstance(dig.scanning, dict) else -1

            # Create a default load scan model based on the digitiser metadata and the observation parameters 
            # This load scan will default to a baseline of ones so will not affect the sky scan when applied
            load_model = ScanModel(
                dig_id=dig.dig_id,
                obs_id=obs_id,             
                tgt_idx=tgt_idx,           
                freq_scan=freq_scan,
                scan_type=ScanType.LOAD,
                start_idx=0,                 # default load scans start at index 0
                duration=dig.scan_duration,
                sample_rate=dig.sample_rate,
                channels=dig.channels,
                center_freq=dig.center_freq,
                gain=dig.gain,
                load=True,
                status=ScanState.COMPLETE)

            load_scan = Scan(scan_model=load_model)
            Scan.reset_scan_iter_counter(obs_id, tgt_idx, freq_scan)  # reset the scan iteration counter for this observation, target, and frequency scan so that the load scan is applied to the correct sky scan iteration
            self.cal_q.put(load_scan)
            load_found = True
            logger.info(f"Science Data Processor created default load scan for digitiser {dig_id} and observation {obs_id}:\n{load_scan}")

        return load_found

    def set_obs_reset(self, value: str) -> bool:
        """ Handles observation reset notification from Telescope Manager.
            params:     value is the observation ID (obs_id) of the observation that is being reset.
            returns:    True if the observation reset was successfully processed, False otherwise
        """
        logger.info(f"Science Data Processor received observation reset notification with value: {value}")

        # Reset the scan iteration counter for this observation, so that we can start from scan_iter=0 again
        obs_id = value if value is not None and isinstance(value, str) else None
        # Find pending scans for this observation and abort them
        pending_scans = [s for s in list(self.scan_q.queue) if s.get_status() in [ScanState.EMPTY, ScanState.WIP] and s.get_obs_id() == obs_id]
        for scan in pending_scans:
            self._abort_scan(scan)
        
        Scan.reset_scan_iter_counter(obs_id)

        digitiser = self.sdp_model.dig_store.get_dig_by_obs_id(obs_id)
        if digitiser is not None:
            # Reset the scanning field of the digitiser
            digitiser.scanning = False
            digitiser.last_update = datetime.now(timezone.utc)
            logger.info(f"Science Data Processor reset scanning to False for digitiser {digitiser.dig_id} for reset observation {obs_id}")

        return True

    def set_obs_complete(self, value: str) -> bool:
        """ Handles observation complete notification from Telescope Manager.
            params:     value is the observation ID (obs_id) of the observation that is complete.
            returns:    True if the observation complete notification was successfully processed, False otherwise
        """
        logger.info(f"Science Data Processor received observation complete notification with value: {value}")

        # Flush the load queue for the relevant digitiser and observation
        obs_id = value if value is not None and isinstance(value, str) else None
        load_scans = [s for s in list(self.cal_q.queue) if s.get_obs_id() == obs_id]
        for load in load_scans:
            self.cal_q.queue.remove(load)
            self.cal_q.task_done()
            logger.info(f"Science Data Processor removed load scan from load queue for completed observation {obs_id}:\n{load}")

        # Reset digitiser configuration for the relevant observation
        dig = self.sdp_model.dig_store.get_dig_by_obs_id(obs_id)
        if dig is not None:
            # Reset the scanning field of the digitiser
            dig.scanning = False
            dig.last_update = datetime.now(timezone.utc)
            logger.info(f"Science Data Processor reset scanning to False for digitiser {dig.dig_id} for completed observation {obs_id}")

        return True

    def handle_field_get(self, api_call):
        """ Handles field get api calls.
              returns: (status, message, value, payload)
        """
        prop_name = api_call['property']

        try:
            if prop_name in self.sdp_model.schema.schema:
                value = getattr(self.sdp_model, prop_name)
                logger.info(f"Science Data Processor {prop_name} get to {value}")
            else:
                logger.error(f"Science Data Processor unknown property {prop_name} get request")
                return tm_sdp.STATUS_ERROR, f"Science Data Processor unknown property {prop_name}", None, None
        
        except XSoftwareFailure as e:
            logger.exception(f"Science Data Processor failed to get property {prop_name}: {e}")
            return tm_sdp.STATUS_ERROR, f"Science Data Processor failed to get property {prop_name}: {e}", None, None

        return tm_sdp.STATUS_SUCCESS, f"Science Data Processor get property {prop_name} value {value}", value, None

    def handle_field_set(self, api_call):
        """ Handles field set api calls.
              returns: (status, message, value, payload)
        """
        prop_name = api_call['property']
        prop_value = api_call['value']

        try:
            if prop_name in self.sdp_model.schema.schema:
                setattr(self.sdp_model, prop_name, prop_value)
                logger.info(f"Science Data Processor {prop_name} set to {prop_value}")
            # Else if the SDP has a callable attribute matching the property name
            elif hasattr(self, "set_" + prop_name) and callable(getattr(self, "set_" + prop_name)):
                method = getattr(self, "set_" + prop_name)
                result = method(prop_value) if prop_value is not None else method()
                if result is True:
                    return tm_sdp.STATUS_SUCCESS, f"Science Data Processor set property {prop_name} to {prop_value}", prop_value, None
                else:
                    return tm_sdp.STATUS_ERROR, f"Science Data Processor failed to set property {prop_name} to {prop_value}", None, None
            else:
                logger.error(f"Science Data Processor unknown property {prop_name} with value {prop_value}")
                return tm_sdp.STATUS_ERROR, f"Science Data Processor unknown property {prop_name}", None, None
        
        except XSoftwareFailure as e:
            logger.exception(f"Science Data Processor failed to set property {prop_name} to {prop_value}: {e}")
            return tm_sdp.STATUS_ERROR, f"Science Data Processor failed to set property {prop_name} to {prop_value}: {e}", None, None

        return tm_sdp.STATUS_SUCCESS, f"Science Data Processor set property {prop_name} to {prop_value}", prop_value, None

    def _diff_dig_metadata(self, digitiser: DigitiserModel, meta_dict: dict) -> str:
        """ Compares digitiser model attributes against incoming sample metadata values.
            Returns a formatted string listing only the fields that differ, one per line.
            Each line shows: field_name: model=<model_value> metadata=<metadata_value>
        """
        diffs = []
        if not digitiser.scanning:
            diffs.append(f" - scanning: model={digitiser.scanning} (not scanning)")
        for field in [sdp_dig.PROPERTY_CENTER_FREQ, sdp_dig.PROPERTY_SAMPLE_RATE, sdp_dig.PROPERTY_SDR_GAIN, sdp_dig.PROPERTY_LOAD, sdp_dig.PROPERTY_SCANNING]:
            model_val = getattr(digitiser, field, None)
            meta_val = meta_dict.get(field)
            if model_val != meta_val:
                diffs.append(f" - {field}: model={model_val} metadata={meta_val}")
        return "\n".join(diffs) if diffs else "no differences found"

    def _construct_status_adv_to_tm(self) -> APIMessage:
        """ Constructs a status advice message for the Telescope Manager.
        """
        tm_adv = APIMessage(api_version=self.tm_api.get_api_version())
        tm_adv.set_json_api_header(
            api_version=self.tm_api.get_api_version(), 
            dt=datetime.now(timezone.utc), 
            from_system=self.sdp_model.app.app_name, 
            to_system="tm", 
            api_call={
                "msg_type": "adv", 
                "action_code": "set", 
                "property": tm_sdp.PROPERTY_STATUS, 
                "value": self.sdp_model.to_dict(), 
                "message": "SDP status update"
            })
        return tm_adv

    def _construct_scan_complete_adv_to_tm(self, match) -> APIMessage:
        """ Constructs a scan complete advice message for the Telescope Manager.
        """
        tm_adv = APIMessage(api_version=self.tm_api.get_api_version())
        tm_adv.set_json_api_header(
            api_version=self.tm_api.get_api_version(), 
            dt=datetime.now(timezone.utc), 
            from_system=self.sdp_model.app.app_name, 
            to_system="tm", 
            api_call={
                "msg_type": "adv", 
                "action_code": "set", 
                "property": tm_sdp.PROPERTY_SCAN_COMPLETE, 
                "value": match.scan_model.to_dict(), 
                "message": "SDP scan complete"
            })
        return tm_adv

    def _construct_rsp_to_dig(self, status: int, message: str, api_msg: dict, api_call: dict) -> APIMessage:
        """ Constructs a Digitiser response APIMessage.
        """
        # Prepare rsp msg to dig acknowledging receipt of the incoming api message
        dig_rsp = APIMessage(api_msg=api_msg, api_version=self.dig_api.get_api_version())
        dig_rsp.switch_from_to()
        dig_rsp.set_api_call({
            "msg_type": "rsp", 
            "action_code": api_call['action_code'], 
            "status": status, 
            "message": message,
        })       
        return dig_rsp

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

    def _abort_scan(self, scan: Scan):
        """ Aborts a scan and performs necessary cleanup.
        """
        scan.set_status(ScanState.ABORTED)
        scan.del_iq()

        self.sdp_model.scans_aborted += 1
        self._remove_from_queue(scan=scan, queue=self.scan_q)  # Remove the aborted scan from the scan processing queue

    def _complete_scan(self, scan: Scan):
        """ Completes a scan and performs necessary cleanup.
        """
        scan.save_to_disk(output_dir=self.get_args().scan_store_dir, include_iq=False)

        self.sdp_model.scans_completed += 1
        self.sdp_model.scans_wip -= 1

        self._remove_from_queue(scan=scan, queue=self.scan_q) # Remove older completed scans from the scan processing queue
        
        # Add load scan to the load queue and replace equivalent items (not needed anymore)
        if scan.is_load_scan():
            self._merge_into_queue(scan, self.cal_q) 

        return

    def _merge_into_queue(self, scan: Scan, queue: Queue = None):
        """ Adds a scan t o the specified queue, replacing any equivalent scans that are already in the queue.
            :params scan: the scan to add to the queue
            :params queue: the queue to add the scan to, defaults to the load (baseline) queue if not specified
        """
        queue = queue if queue is not None else self.cal_q
        queue.put(scan)  # Add this scan to the queue
        
        equivalent_items = [s for s in list(queue.queue) if s != scan and s.equivalent(scan)]

        for item in equivalent_items:  
            self._remove_from_queue(item, queue=queue)
            logger.info(f"Science Data Processor removed equivalent {'load' if item.is_load_scan() else 'sky'} scan {item} from queue " + \
                f"for digitiser {item.get_dig_id()} with same parameters as {scan}")

    def _remove_from_queue(self, scan: Scan, queue: Queue = None):
        """ Removes a scan from the queue without marking it as aborted or completed.
            Used for cleanup of pending scans on startup or observation reset.
        """
        queue = queue if queue is not None else self.scan_q

        try:
            queue.queue.remove(scan) # Remove this scan from the underlying queue
            queue.task_done() # Balance the unfinished task count for the removed item
        except ValueError:
            logger.warning(f"Science Data Processor could not find scan while attempting to remove scan {scan} from queue. It may have already been removed.")

def main():
    sdp = SDP()
    sdp.start()

    display_period_sec = 1.0  # Initial display period in seconds

    try:
        while True:
            time_start = time.monotonic()
          
            # For each processing scan in the scan queue, allocate it to a signal display
            # We are expecting one processing scan per digitiser to be in the scan queue
            for i in range(sdp.scan_q.qsize()):

                try:
                    scan = sdp.scan_q.queue[i]
                except IndexError:
                    continue  # Scan index not valid, continue to next scan

                dig_id = scan.get_dig_id() # Identify the digitiser associated with the scan

                # If there is no signal display for this digitiser, create a new active signal display
                if dig_id not in sdp.signal_displays or sdp.signal_displays[dig_id] is None:
                    logger.info(f"Science Data Processor creating new SignalDisplay for digitiser {dig_id}")
                    sdp.signal_displays[dig_id] = SignalDisplay(dig_id=dig_id)

                if not (sdp.signal_displays[dig_id].get_is_active()):
                    continue # Signal display for digitiser has been deactivated, continue to next scan 

                sig_display_scan = sdp.signal_displays[dig_id].get_scan() 
                
                # If the scan allocated to the signal display differs from the current scan
                if sig_display_scan != scan:

                    # If the previous displayed scan completed, update display and save its figure
                    if sig_display_scan and sig_display_scan.get_status() == ScanState.COMPLETE:    
                        sdp.signal_displays[dig_id].display()
                        sdp.signal_displays[dig_id].save_scan_figure(output_dir=sdp.get_args().scan_store_dir)
                        
                    #sig_display_scan.del_iq()

                    # Find the equivalent load scan for this scan if it exists in the load queue
                    load_scans = [s for s in list(sdp.cal_q.queue) if s.equivalent(scan) and s.is_load_scan() == True]
                    logger.debug(f"Science Data Processor found {len(load_scans)} equivalent load scans in load queue for digitiser {dig_id} and observation {scan.get_obs_id()} to apply to signal display")

                    # Set the signal display to the current scan
                    sdp.signal_displays[dig_id].set_scan(scan=scan, load=load_scans[0] if len(load_scans) > 0 else None)

            for sig_display in sdp.signal_displays.values():
                # If the signal display has a scan and is active, display the scan
                if sig_display.get_scan():
                    
                    if sig_display.get_is_active():
                        sig_display.display()
                    
                    if sig_display.get_scan().get_status() == ScanState.COMPLETE:
                        sig_display.save_scan_figure(output_dir=sdp.get_args().scan_store_dir)

            time_elapsed = time.monotonic() - time_start

            # Adjust display period up or down based on processing time
            time_elapsed = time.monotonic() - time_start

            if time_elapsed > display_period_sec:
                display_period_sec += 1.0 
                logger.warning(f"Science Data Processor - Signal display loop took {time_elapsed:.3f} seconds to execute, extending display period to {display_period_sec} seconds")
            elif time_elapsed < display_period_sec - 2.0:
                display_period_sec = max(1.0, display_period_sec - 2.0)
                logger.info(f"Science Data Processor - Signal display loop took {time_elapsed:.3f} seconds to execute, shortening display period to {display_period_sec} seconds")

            time.sleep(max(0.0, display_period_sec - time_elapsed))  # Update displays on an approximately 1 second cadence
                
    except KeyboardInterrupt:
        pass
    finally:
        sdp.stop()

if __name__ == "__main__":
    main()
