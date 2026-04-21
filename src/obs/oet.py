from typing import TYPE_CHECKING
from datetime import datetime, timezone, timedelta

import json
import logging
import threading

from env.events import ObsEvent
from ipc.action import Action
from models.comms import CommunicationStatus
from models.dsh import DishManagerModel, Feed, Capability, DishMode, PointingState
from models.health import HealthState
from models.obs import ObsModel, ObsTransition, ObsState
from models.oda import ODAModel, ObsList, ScanStore
from models.scan import ScanModel, ScanState
from models.target import TargetModel, PointingType
from models.telescope import TelescopeModel
from models.tm import ResourceType, AllocationState
from util import log, util
from util.timer import Timer, TimerManager
from util.xbase import XBase, XStreamUnableToExtract, XSoftwareFailure

if TYPE_CHECKING:
    from tm.tm import TelescopeManager

logger = logging.getLogger(__name__)

class ObservationExecutionTool:

    def __init__(self, telmodel:TelescopeModel, tm:"TelescopeManager"):
        
        # Lock for thread-safe allocation of shared resources
        self._rlock = threading.RLock()
        self._obs_locks = {}  # Dictionary of locks for individual observations, keyed by obs_id

        self.telmodel = telmodel        # Telescope Model
        self.tm = tm                    # Telescope Manager  

    def _get_obs_lock(self, obs_id: str) -> threading.RLock:
        """Get or create a threading lock for a specific observation ID."""
        if obs_id not in self._obs_locks:
            self._obs_locks[obs_id] = threading.RLock()
        return self._obs_locks[obs_id]

    def process_obs_event(self, event):
        """ Processes a workflow transition on an observation.
            Returns an Action object with actions to be performed.
        """

        logger.info(f"Observation Execution Tool processing an Observation event: {event}")

        action = Action()

        # Get the threading lock specific to this observation
        obs_lock = self._get_obs_lock(event.obs.obs_id)
        with obs_lock:

            # Handle observation event transitions
            if event.transition == ObsTransition.START:

                # Transition to IDLE where resources can be assigned or released
                event.obs.obs_state = ObsState.IDLE

                # Determine the required scans for each target in the observation
                event.obs.determine_scans()
                action.set_obs_transition(obs=event.obs, transition=ObsTransition.ASSIGN_RESOURCES)

            elif event.transition == ObsTransition.ASSIGN_RESOURCES:

                event.obs.obs_state = ObsState.IDLE
                
                # Grant resources for this observation if possible, otherwise request resources i.e. get in the queue
                # Resource availability will be checked each time this method is called, resources will only be requested once 
                # Returns true if all resources were granted, false if any resource had to be requested
                if self.assign_resources(event.obs, action):
                    action.set_obs_transition(obs=event.obs, transition=ObsTransition.CONFIGURE_RESOURCES)
                else:
                    # Resources not available, observation remains in IDLE state waiting for resources to be released by other observations
                    logger.info(f"Observation {event.obs.obs_id} blocked waiting for resources.")

            elif event.transition == ObsTransition.RELEASE_RESOURCES:

                event.obs.obs_state = ObsState.IDLE

                # Release resources for this observation
                # Returns true if at least one active resource was released, false otherwise
                if self.release_resources(event.obs, action):

                    now = datetime.now(timezone.utc)
                    # Find observations with ObsState = IDLE that should be observing now
                    waiting_obs = [obs for obs in self.telmodel.oda.obs_store.obs_list if obs.obs_state == ObsState.IDLE and obs.scheduling_block_start <= now and obs.scheduling_block_end > now]

                    # Check if there are other observations waiting for the same resources just released so that they can be assigned
                    for obs in waiting_obs:
                        if obs.obs_id != event.obs.obs_id and obs.dsh_id == event.obs.dsh_id:
                            action.set_obs_transition(obs=obs, transition=ObsTransition.ASSIGN_RESOURCES)

                # Save current observation state to disk
                event.obs.save_to_disk(self.telmodel.get_scan_store_dir())
                event.obs.save_fits_to_disk(self.telmodel.get_scan_store_dir())

            elif event.transition == ObsTransition.CONFIGURE_RESOURCES:

                event.obs.obs_state = ObsState.CONFIGURING
                timer_name = f"obs_configuring_timer:{event.obs.obs_id}"

                # Determine outstanding configuration actions for this observation
                # Returns true if all resources are already configured, false if any resource still requires configuration
                if self.configure_resources(event.obs, action):
                    action.set_obs_transition(obs=event.obs, transition=ObsTransition.READY)
                    action.set_timer_action(Action.Timer(name=timer_name, timer_action=Action.Timer.TIMER_STOP))
                else:

                    # Start configuration timer for this observation if not already active
                    if not any(timer.active for timer in Timer.manager.get_timers_by_name(timer_name)):
                    
                        action.set_timer_action(Action.Timer(
                            name=timer_name, 
                            timer_action=event.obs.timeout_ms_config, 
                            echo_data=event.obs))
            
            elif event.transition == ObsTransition.READY:
                if event.obs.obs_state == ObsState.SCANNING:
                    logger.info(
                        f"Observation Execution Tool ignoring duplicate READY transition for "
                        f"observation {event.obs.obs_id} because it is already SCANNING."
                    )
                    return action

                if event.obs.obs_state not in (ObsState.CONFIGURING, ObsState.READY):
                    logger.warning(
                        f"Observation Execution Tool ignoring READY transition for "
                        f"observation {event.obs.obs_id} in unexpected state "
                        f"{event.obs.obs_state.name}."
                    )
                    return action

                event.obs.obs_state = ObsState.READY

                # Attempt to start scanning, returns true if scanning successfully requested, false otherwise
                if self.start_scanning(event.obs, action):
                    action.set_obs_transition(obs=event.obs, transition=ObsTransition.SCAN_STARTED)

            elif event.transition == ObsTransition.SCAN_STARTED:
                if event.obs.obs_state == ObsState.CONFIGURING:
                    logger.warning(
                        f"Observation Execution Tool received SCAN_STARTED for observation "
                        f"{event.obs.obs_id} while it was still CONFIGURING. "
                        "Promoting to READY to absorb a stale configuration event."
                    )
                    event.obs.obs_state = ObsState.READY

                elif event.obs.obs_state == ObsState.SCANNING:
                    logger.info(
                        f"Observation Execution Tool ignoring duplicate SCAN_STARTED transition "
                        f"for observation {event.obs.obs_id} because it is already SCANNING."
                    )
                    return action

                elif event.obs.obs_state != ObsState.READY:
                    logger.warning(
                        f"Observation Execution Tool ignoring SCAN_STARTED transition for "
                        f"observation {event.obs.obs_id} in unexpected state "
                        f"{event.obs.obs_state.name}."
                    )
                    return action

                event.obs.obs_state = ObsState.SCANNING
                timer_name = f"obs_scanning_timer:{event.obs.obs_id}"
            
                # Start a scan timer in case the scan exceeds its expected duration
                action.set_timer_action(Action.Timer(
                    name=timer_name, 
                    timer_action=event.obs.timeout_ms_scan, 
                    echo_data=event.obs))

            elif event.transition == ObsTransition.SCAN_COMPLETED:

                event.obs.obs_state = ObsState.READY

                # Save current observation state to disk
                event.obs.save_to_disk(self.telmodel.get_scan_store_dir())

                # If the observation is complete, stop scanning and release resources
                if self.complete_scan(event.obs, action):
                    self.stop_scanning(event.obs, action)
                    action.set_obs_transition(obs=event.obs, transition=ObsTransition.RELEASE_RESOURCES)
                
                # If the observation is not complete, prepare for the next scan
                # Workflow will transition to SCAN_STARTED or CONFIGURE_RESOURCES as needed within complete_scan()  

            elif event.transition == ObsTransition.SCAN_ENDED:
                event.obs.obs_state = ObsState.READY

                # If the observation is complete, stop scanning and release resources
                if self.complete_scan(event.obs, action):
                    self.stop_scanning(event.obs, action)
                    action.set_obs_transition(obs=event.obs, transition=ObsTransition.RELEASE_RESOURCES)

                # If the observation is not complete, prepare for the next scan
                # Workflow will transition to SCAN_STARTED or CONFIGURE_RESOURCES as needed within complete_scan()

            elif event.transition == ObsTransition.ABORT:

                # If resources were assigned and are either configuring, ready or scanning
                if event.obs.obs_state in [ObsState.CONFIGURING, ObsState.READY, ObsState.SCANNING]:
                    # Stop scanning for this observation
                    self.stop_scanning(event.obs, action)

                # Transition to ABORTED state where resources will be released after a timeout
                event.obs.obs_state = ObsState.ABORTED

                # Start timer till end of the scheduling block before releasing resources
                # Allows operators to investigate and potentially reset the observation before the end of the scheduling block
                timer_name = f"obs_abort_timer:{event.obs.obs_id}"

                time_ms_until_end = int((event.obs.scheduling_block_end - datetime.now(timezone.utc)).total_seconds() * 1000)
                action.set_timer_action(Action.Timer(
                    name=timer_name, 
                    timer_action=time_ms_until_end,
                    echo_data=event.obs))

            elif event.transition == ObsTransition.FAULT_OCCURRED:
                event.obs.obs_state = ObsState.FAULT

            elif event.transition == ObsTransition.RESET:

                # Can only reset observations in ABORTED or FAULT states
                if event.obs.obs_state in [ObsState.ABORTED, ObsState.FAULT, ObsState.IDLE]:
                    # Stop abort timer if active
                    timer_name = f"obs_abort_timer:{event.obs.obs_id}"
                    action.set_timer_action(Action.Timer(name=timer_name, timer_action=Action.Timer.TIMER_STOP))

                    # Reset target index and target scan index to start at the beginning if the Obs State was IDLE
                    if event.obs.obs_state == ObsState.IDLE:
                        event.obs.tgt_idx = 0
                        event.obs.tgt_scan = 0
                        event.obs.determine_scans()
                        self.reset_sdp_scan(obs=event.obs, action=action)

                    # Reset observation state to IDLE
                    event.obs.obs_state = ObsState.IDLE
                    # Try to assign resources for the next scan if possible
                    action.set_obs_transition(obs=event.obs, transition=ObsTransition.ASSIGN_RESOURCES)
                else:
                    logger.warning(f"Observation Execution Tool ignoring reset for observation {event.obs.obs_id} in state {event.obs.obs_state.name}. " + \
                        "Reset can only be applied to observations in ABORTED, FAULT or IDLE states.")
            else:
                logger.warning(f"Observation Execution Tool received unknown observation event transition: {event.transition}")
        
        return action

    def start_next_obs_timer(self, action) -> bool:
        """ Sets a timer to start the next scheduled observation with ObsState = EMPTY.
            Returns an True if a timer was set, False otherwise.
        """

        # Find observations with ObsState = EMPTY that are scheduled to start in the future
        empty_obs = [obs for obs in self.telmodel.oda.obs_store.obs_list if obs.obs_state == ObsState.EMPTY and obs.scheduling_block_start >= datetime.now(timezone.utc)]
        next_obs = min(empty_obs, key=lambda obs: obs.scheduling_block_start) if len(empty_obs) > 0 else None
        
        if next_obs is not None:
            # Observation start time is in the future, reset timer
            time_ms_until_start = int((next_obs.scheduling_block_start - datetime.now(timezone.utc)).total_seconds() * 1000)
            
            action.set_timer_action(Action.Timer(
                name=f"obs_start_timer", 
                timer_action=time_ms_until_start,
                echo_data=next_obs))
            logger.info(f"Observation Execution Tool next observation {next_obs.obs_id} starting at {next_obs.scheduling_block_start} in {time_ms_until_start} ms")
            return True

        return False

    def assign_resources(self, obs, action) -> bool:
        """ Process an observation resource allocation request.
            Grants an allocation request if the resource is available.
            Requests an allocation if the resource is busy.
            Will not create new allocation request if an existing request is pending.
            Returns True if resources were successfully granted, False otherwise.
        """
        # Lookup the dish using the observation's dsh_id
        dsh_model = next((dsh for dsh in self.telmodel.dsh_mgr.dish_store.dish_list if dsh.dsh_id == obs.dsh_id), None)

        if dsh_model is None:

            logger.warning(
                f"Observation Execution Tool could not find Dish {obs.dsh_id} in Dish Manager model. "
                f"Cannot assign dish for observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        elif dsh_model.capability not in [Capability.OPERATE_FULL, Capability.OPERATE_DEGRADED]:
            logger.warning(
                f"Observation Execution Tool found Dish {obs.dsh_id}, but it is not currently operational. Capability {dsh_model.capability.name}. "
                f"Cannot assign dish for observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        elif dsh_model.mode not in [DishMode.STANDBY_LP, DishMode.STANDBY_FP, DishMode.OPERATE, DishMode.CONFIG]:
            logger.warning(
                f"Observation Execution Tool found Dish {obs.dsh_id}, but it is not in an operational mode. Current mode {dsh_model.mode.name}. "
                f"Cannot assign dish for observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        if self.telmodel.dsh_mgr.tm_connected != CommunicationStatus.ESTABLISHED:
            logger.warning(
                f"Observation Execution Tool is not connected to Dish Manager. "
                f"Cannot assign dish for observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        elif self.telmodel.dsh_mgr.app.health not in [HealthState.OK, HealthState.DEGRADED]:
            logger.warning(
                f"Observation Execution Tool found Dish Manager, but it is not currently healthy. Health state {self.telmodel.dsh_mgr.app.health.name}. "
                f"Cannot assign resources to observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        # Lookup the digitiser using the dig_id associated with the dish
        dig_model = next((dig for dig in self.telmodel.dig_store.dig_list if dig.dig_id == dsh_model.dig_id), None)

        if dig_model is None:
            logger.warning(
                f"Observation Execution Tool found Dish {obs.dsh_id}, but it is not associated with a Digitiser. "
                f"Cannot assign digitiser to observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        elif dig_model.app.health not in [HealthState.OK, HealthState.DEGRADED]:
            logger.warning(
                f"Observation Execution Tool found Digitiser {dig_model.dig_id}, but it is not currently healthy. Health state {dig_model.app.health.name}. "
                f"Cannot assign digitiser to observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        sdp = self.telmodel.sdp
        if self.telmodel.sdp.tm_connected != CommunicationStatus.ESTABLISHED:
            logger.warning(
                f"Observation Execution Tool is not connected to Science Data Processor. "
                f"Cannot assign resources to observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        elif sdp.app.health not in [HealthState.OK, HealthState.DEGRADED]:
            logger.warning(
                f"Observation Execution Tool found Science Data Processor, but it is not currently healthy. Health state {sdp.app.health.name}. "
                f"Cannot assign resources to observation {obs.obs_id}. Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)
            return False

        with self._rlock:

            granted_all_resources = True    # Flag indicating if all resources were granted
        
            # Request new resource allocation for dish resources i.e. get in the queue
            dish_req = self.telmodel.tel_mgr.allocations.request_allocation(
                resource_type=ResourceType.DISH.value, 
                resource_id=dsh_model.dsh_id, 
                allocated_type=ResourceType.OBS.value, 
                allocated_id=obs.obs_id,
                expires=obs.scheduling_block_end)

            # Get current active allocation for dish resources 
            dish_alloc = self.telmodel.tel_mgr.allocations.get_active_allocation(
                resource_type=ResourceType.DISH.value, 
                resource_id=dsh_model.dsh_id)

            if not self.telmodel.tel_mgr.allocations.handle_resource_allocation(
                resource_type=ResourceType.DISH.value,
                resource_id=dsh_model.dsh_id,
                resource_req=dish_req,
                resource_alloc=dish_alloc
            ):
                granted_all_resources = False

            # Request new resource allocation for digitiser resources i.e. get in the queue
            dig_req = self.telmodel.tel_mgr.allocations.request_allocation(
                resource_type=ResourceType.DIGITISER.value, 
                resource_id=dig_model.dig_id, 
                allocated_type=ResourceType.OBS.value, 
                allocated_id=obs.obs_id,
                expires=obs.scheduling_block_end)

            # Get current active allocation for digitiser resources 
            dig_alloc = self.telmodel.tel_mgr.allocations.get_active_allocation(
                resource_type=ResourceType.DIGITISER.value, 
                resource_id=dig_model.dig_id)

            if not self.telmodel.tel_mgr.allocations.handle_resource_allocation(
                resource_type=ResourceType.DIGITISER.value,
                resource_id=dig_model.dig_id,
                resource_req=dig_req,
                resource_alloc=dig_alloc
            ):
                granted_all_resources = False

            return granted_all_resources

    def release_resources(self, obs: ObsModel, action: Action) -> bool:
        """ Process an observation resource release request.
            Returns true if at least one active resource was released, false otherwise.
        """
        released_active_resources = False
        
        # Find resource allocations for this observation
        obs_allocs = self.telmodel.tel_mgr.allocations.get_allocations(allocated_type=ResourceType.OBS.value, allocated_id=obs.obs_id)
        
        # Release each allocation
        for alloc in obs_allocs:

            if alloc.state == AllocationState.ACTIVE:
                released_active_resources = True

            logger.info(
                f"Observation Execution Tool releasing resource {alloc.resource_type} {alloc.resource_id} "
                f"allocated to {alloc.allocated_type} {alloc.allocated_id} in state {alloc.state.name} "
                f"with expiry {alloc.expires}")

            self.telmodel.tel_mgr.allocations.release_allocation(alloc)
            
        return released_active_resources

    def configure_resources(self, obs, action) -> bool:
        """ Process an observation resource configuration request.
            Returns true if all resources are already configured, false if any resource still requires configuration.
        """
        logger.info(f"Observation Execution Tool processing Configure Resources for observation {obs.obs_id} scheduled to start at {obs.scheduling_block_start}")

        already_configured = True

        # Get the current target config for the observation
        target_config = obs.get_target_config_by_index(obs.tgt_idx)

        if target_config is None:
            logger.error(f"Observation Execution Tool could not find next target config {obs.tgt_idx} to execute for observation {obs.obs_id}. " + \
                f"Nothing to configure.")
            return False

        # Get the current target scan set and specific target scan for the observation
        target_scan_set = obs.get_current_tgt_scan_set()
        target_scan = obs.get_current_tgt_scan()

        if target_scan is None:
            logger.error(f"Observation Execution Tool could not find target scan {obs.tgt_idx}-{obs.tgt_scan} to execute for observation {obs.obs_id}. " + \
                f"Nothing to configure.")
            return False

        # Lookup the current target for the observation
        target = obs.get_target_by_index(obs.tgt_idx)

        # Lookup the dish model for this observation
        dsh_model = next((dsh for dsh in self.telmodel.dsh_mgr.dish_store.dish_list if dsh.dsh_id == obs.dsh_id), None)

        if dsh_model is not None and target is not None:

            old_dsh_config = {}
            new_dsh_config = {}

            # on_target returns None if the wrong target is configured, True if ON the correct target, else False if not on the correct target
            on_target = self.is_on_target(obs, target, dsh_model)
            # If we are not on the correct target, set the dish to CONFIG mode and provide the new target
            if on_target is None:

                # Dish can only set target if in CONFIG mode
                if dsh_model.mode != DishMode.CONFIG:
                    old_dsh_config['mode'] = dsh_model.mode
                    new_dsh_config['mode'] = DishMode.CONFIG
                else:
                    old_dsh_config['target'] = dsh_model.pointing_altaz
                    new_dsh_config['target'] = target.to_dict()

            else:
                logger.info(f"Observation Execution Tool found Dish already configured for correct target for observation {obs.obs_id} with index {obs.tgt_idx}-{obs.tgt_scan}. " +
                    f"Dish target ID {dsh_model.tgt_id} matches expected target ID {obs.obs_id}-{obs.tgt_idx}. On Target {on_target}")
  
            if len(new_dsh_config) > 0:

                already_configured = False

                # Needed to direct the config to the correct dish and 
                # To transition the appropriate observation state once configuration is applied
                old_dsh_config['dsh_id'] = dsh_model.dsh_id 
                new_dsh_config['dsh_id'] = dsh_model.dsh_id 
                new_dsh_config['obs_id'] = obs.obs_id

                # Send configuration requests to the Dish if we are not already waiting for previous requests to complete
                if not any(timer.active for timer in Timer.manager.get_timers_by_keyword(f"{dsh_model.dsh_id}_req_timer")):
                    logger.info(f"Observation Execution Tool sending Dish configuration requests for observation {obs.obs_id} with index {obs.tgt_idx}-{obs.tgt_scan}")
                    action = self.tm.update_dsh_configuration(old_dsh_config, new_dsh_config, action)
            else:
                logger.info(f"Observation Execution Tool found Dish already configured for observation {obs.obs_id} with index {obs.tgt_idx}-{obs.tgt_scan}")
            
        # Lookup the digitiser model for this observation
        dig_model = next((dig for dig in self.telmodel.dig_store.dig_list if dig.dig_id == dsh_model.dig_id), None)

        # Define digitiser config parameter mappings: (digitiser attribute, source object, source attribute)
        config_params = [
            ('center_freq',   target_scan,     'center_freq'),
            ('bandwidth',     target_config,   'bandwidth'),
            ('sample_rate',   target_config,   'sample_rate'),
            ('gain',          target_config,   'gain'),
        ]

        # If we found a valid digitiser, check if it needs to be configured
        if dig_model is not None:

            old_dig_config = {}
            new_dig_config = {}

            if target_config.feed == Feed.LOAD and dig_model.load != True:
                old_dig_config['load'] = dig_model.load
                new_dig_config['load'] = True
            elif target_config.feed != Feed.LOAD and dig_model.load != False:
                old_dig_config['load'] = dig_model.load
                new_dig_config['load'] = False

            for dig_attr, source, source_attr in config_params:
                current = getattr(dig_model, dig_attr)
                desired = getattr(source, source_attr)
                if current != desired:
                    old_dig_config[dig_attr] = current
                    new_dig_config[dig_attr] = desired

            if len(new_dig_config) > 0:

                already_configured = False

                # If digitiser is aready scanning, update the observation and scan parameters
                if dig_model.scanning is not False:
                    old_dig_config['scanning'] = dig_model.scanning
                    new_dig_config['scanning'] = {'obs_id': obs.obs_id, 'tgt_idx': obs.tgt_idx, 'freq_scan': target_scan.freq_scan } 

                # Needed to direct the config to the correct digitiser and 
                # To transition the appropriate observation state once configuration is applied
                old_dig_config['dig_id'] = dig_model.dig_id 
                new_dig_config['dig_id'] = dig_model.dig_id 
                new_dig_config['obs_id'] = obs.obs_id  

                # Send configuration requests to the Digitiser if we are not already waiting for previous requests to complete
                if not any(timer.active for timer in Timer.manager.get_timers_by_keyword(f"{dig_model.dig_id}_req_timer")):
                    logger.info(f"Observation Execution Tool sending Digitiser configuration requests for observation {obs.obs_id} with index {obs.tgt_idx}-{obs.tgt_scan}")
                    action = self.tm.update_dig_configuration(old_dig_config, new_dig_config, action)
            else:
                logger.info(f"Observation Execution Tool found Digitiser already configured for observation {obs.obs_id} with index {obs.tgt_idx}-{obs.tgt_scan}")
  
        # Append additional config parameters
        config_params.extend([
            ('channels',      target_config,   'spectral_resolution'),
            ('scan_duration', target_scan_set, 'scan_duration'),
        ])
  
        if self.telmodel.sdp is not None:

            old_scan_config = {}
            new_scan_config = {}

            sdp_dig = next((dig for dig in self.telmodel.sdp.dig_store.dig_list if dig.dig_id == dig_model.dig_id), None) if dig_model is not None else None

            for dig_attr, source, source_attr in config_params:
                current = getattr(sdp_dig, dig_attr) if sdp_dig is not None else None
                desired = getattr(source, source_attr)
                if current != desired:
                    old_scan_config[dig_attr] = current
                    new_scan_config[dig_attr] = desired

            scanning = {'obs_id': obs.obs_id, 'tgt_idx': obs.tgt_idx, 'freq_scan': target_scan.freq_scan } if sdp_dig is not None else False

            if sdp_dig is not None and sdp_dig.scanning != scanning:
                old_scan_config['scanning'] = sdp_dig.scanning
                new_scan_config['scanning'] = scanning

            if target_config.feed == Feed.LOAD and sdp_dig.load != True:
                old_scan_config['load'] = sdp_dig.load
                new_scan_config['load'] = True
            elif target_config.feed != Feed.LOAD and sdp_dig.load != False:
                old_scan_config['load'] = sdp_dig.load
                new_scan_config['load'] = False

            if len(new_scan_config) > 0:

                already_configured = False

                # SDP needs to know about additional parameters to prepare for incoming scan samples
                new_scan_config['dig_id'] = dig_model.dig_id if dig_model is not None else None
                new_scan_config['obs_id'] = obs.obs_id
 
                old_sdp_config = {}
                new_sdp_config = {}
   
                old_sdp_config['scan_config'] = old_scan_config
                new_sdp_config['scan_config'] = new_scan_config

                new_sdp_config['sdp_id'] = self.telmodel.sdp.sdp_id
                new_sdp_config['obs_id'] = obs.obs_id

                # Send configuration requests to the Science Data Processor if we are not already waiting for previous requests to complete
                if not any(timer.active for timer in Timer.manager.get_timers_by_keyword(f"{self.telmodel.sdp.sdp_id}_req_timer")):
                    logger.info(f"Observation Execution Tool sending Science Data Processor configuration requests for observation {obs.obs_id} with index {obs.tgt_idx}-{obs.tgt_scan}")
                    action = self.tm.update_sdp_configuration(old_sdp_config, new_sdp_config, action)
            else:
                logger.info(f"Observation Execution Tool found Science Data Processor already configured for observation {obs.obs_id} with index {obs.tgt_idx}-{obs.tgt_scan}")

        if dsh_model is None or dig_model is None or self.telmodel.sdp is None:
            raise XSoftwareFailure(f"Observation Execution Tool could not configure missing critical resource for observation {obs.obs_id}. " + \
                f"Dish found: {dsh_model is not None}, Digitiser found: {dig_model is not None}, Science Data Processor found: {self.telmodel.sdp is not None}.")

        return already_configured

    def start_scanning(self, obs, action) -> bool:
        """ Process an observation start scanning request.
            Returns true if start scanning was requested, false otherwise.
        """
        logger.info(f"Observation Execution Tool processing Start Scanning for observation {obs.obs_id}")

        # Lookup the dish model for this observation
        dsh_model = next((dsh for dsh in self.telmodel.dsh_mgr.dish_store.dish_list if dsh.dsh_id == obs.dsh_id), None)

        if dsh_model is not None:
            pass # Nothing to do as it should be pointing and tracking already
  
        # Lookup the digitiser model for this observation
        dig_model = next((dig for dig in self.telmodel.dig_store.dig_list if dig.dig_id == dsh_model.dig_id), None)
        # If we found a valid digitiser, send it a start scanning instruction
        if dig_model is not None:

            old_dig_config = {}
            new_dig_config = {}

            # Get the current target scan for the observation
            target_scan_set = obs.get_current_tgt_scan_set()

            instruction = {
                "obs_id": obs.obs_id,
                "tgt_idx": obs.tgt_idx,
                "freq_scan": (obs.tgt_scan // target_scan_set.scan_iterations) if target_scan_set is not None else -1,
            }

            # Instruct the digitiser to start scanning 
            old_dig_config['scanning'] = dig_model.scanning
            new_dig_config['scanning'] = instruction

            old_dig_config['dig_id'] = dig_model.dig_id
            new_dig_config['dig_id'] = dig_model.dig_id
            new_dig_config['obs_id'] = obs.obs_id

            # Send configuration requests to the Digitiser if we are not already waiting for previous requests to complete
            if not any(timer.active for timer in Timer.manager.get_timers_by_keyword(f"{dig_model.dig_id}_req_timer")):
                logger.info(f"Observation Execution Tool sending Digitiser start scanning request with instruction {instruction}")
                action = self.tm.update_dig_configuration(old_dig_config, new_dig_config, action)

        if dsh_model is None or dig_model is None:
            raise XSoftwareFailure(f"Observation Execution Tool could not start scanning on missing critical resource for observation {obs.obs_id}. " + \
                f"Dish found: {dsh_model is not None}, Digitiser found: {dig_model is not None}.")

        return True

    def stop_scanning(self, obs, action) -> bool:
        """ Process an observation stop scanning request. 
            This is used when an observation has completed all scans or is aborted and needs to stop scanning immediately.
            Returns true if stop scanning was requested, false otherwise.
        """
        logger.info(f"Observation Execution Tool processing Stop Scanning for observation {obs.obs_id}")

        # Stop the scanning timer
        timer_name = f"obs_scanning_timer:{obs.obs_id}"
        action.set_timer_action(Action.Timer(name=timer_name, timer_action=Action.Timer.TIMER_STOP))

        # Lookup the dish model for this observation
        dsh_model = next((dsh for dsh in self.telmodel.dsh_mgr.dish_store.dish_list if dsh.dsh_id == obs.dsh_id), None)

        if dsh_model is not None:
            # Instruct the dish to go to STANDBY_FP mode and clear the target
            old_dsh_config = {}
            new_dsh_config = {}

            old_dsh_config['mode'] = dsh_model.mode
            new_dsh_config['mode'] = DishMode.STANDBY_FP

            # Clearing a target can be done in any dish mode
            old_dsh_config['target'] = dsh_model.target
            new_dsh_config['target'] = None

            old_dsh_config['dsh_id'] = dsh_model.dsh_id
            new_dsh_config['dsh_id'] = dsh_model.dsh_id
            new_dsh_config['obs_id'] = obs.obs_id

            # Send configuration requests to the Dish if we are not already waiting for previous requests to complete
            #if not any(timer.active for timer in Timer.manager.get_timers_by_keyword(f"{dsh_model.dsh_id}_req_timer")):
            logger.info(f"Observation Execution Tool sending Dish stop scanning request for observation {obs.obs_id}")
            action = self.tm.update_dsh_configuration(old_dsh_config, new_dsh_config, action)

        # Lookup the digitiser model for this observation
        dig_model = next((dig for dig in self.telmodel.dig_store.dig_list if dig.dig_id == dsh_model.dig_id), None)

        # If we found a valid digitiser, send stop scanning instruction
        if dig_model is not None:

            old_dig_config = {}
            new_dig_config = {}

            # Instruct the digitiser to stop scanning samples because the observation has completed / aborted
            old_dig_config['scanning'] = dig_model.scanning
            new_dig_config['scanning'] = False

            old_dig_config['dig_id'] = dig_model.dig_id
            new_dig_config['dig_id'] = dig_model.dig_id
            new_dig_config['obs_id'] = obs.obs_id

            # Send configuration requests to the Digitiser if we are not already waiting for previous requests to complete
            #if not any(timer.active for timer in Timer.manager.get_timers_by_keyword(f"{dig_model.dig_id}_req_timer")):
            logger.info(f"Observation Execution Tool sending Digitiser stop scanning request for observation {obs.obs_id}")
            action = self.tm.update_dig_configuration(old_dig_config, new_dig_config, action)

        if self.telmodel.sdp is not None:

            old_sdp_config = {}
            new_sdp_config = {}

            # Inform the Science Data Processor that the observation has completed / aborted
            old_sdp_config['obs_complete'] = None
            new_sdp_config['obs_complete'] = obs.obs_id

            old_sdp_config['sdp_id'] = self.telmodel.sdp.sdp_id
            new_sdp_config['sdp_id'] = self.telmodel.sdp.sdp_id
            new_sdp_config['obs_id'] = obs.obs_id

            # Send configuration requests to the Science Data Processor if we are not already waiting for previous requests to complete
            #if not any(timer.active for timer in Timer.manager.get_timers_by_keyword(f"{self.telmodel.sdp.sdp_id}_req_timer")):
            logger.info(f"Observation Execution Tool sending Science Data Processor observation complete request for observation {obs.obs_id}")
            action = self.tm.update_sdp_configuration(old_sdp_config, new_sdp_config, action)

        if dsh_model is None or dig_model is None or self.telmodel.sdp is None:
            raise XSoftwareFailure(f"Observation Execution Tool could not stop scanning on missing critical resource for observation {obs.obs_id}. " + \
                f"Dish found: {dish_model is not None}, Digitiser found: {dig_model is not None}, SDP found: {self.telmodel.sdp is not None}.")

        return True

    def complete_scan(self, obs, action) -> bool:
        """ Process an observation scan complete event.
            Returns true if all scans in the observation are complete, false otherwise.
        """
        logger.info(f"Observation Execution Tool processing Complete Scan for observation {obs.obs_id}")

        # Stop the scanning timer
        timer_name = f"obs_scanning_timer:{obs.obs_id}"
        action.set_timer_action(Action.Timer(name=timer_name, timer_action=Action.Timer.TIMER_STOP))

        # Lookup the current target scan set for the observation
        target_scan_set = obs.get_current_tgt_scan_set()

        if target_scan_set is not None:

            # Record the observation's current tgt and freq scan indexes
            old_tgt_idx = obs.tgt_idx
            old_freq_scan = obs.tgt_scan // target_scan_set.scan_iterations
            
            # Set the observation's next target and scan indexes
            obs.set_next_tgt_scan()

            new_tgt_idx = obs.tgt_idx
            new_freq_scan = obs.tgt_scan // target_scan_set.scan_iterations # This works even if tgt_idx was incremented (tgt_scan reset to 0)

            # If we have completed all target configs for this observation
            if obs.tgt_idx >= len(obs.target_configs):
                logger.info(f"Observation Execution Tool completed all target configs for observation {obs.obs_id}")
                return True
            
            # Trigger transition to configure resources (if needed)
            action.set_obs_transition(obs=obs, transition=ObsTransition.CONFIGURE_RESOURCES)
        else:
            logger.error(f"Observation Execution Tool could not find current target scan set for observation {obs.obs_id} with index {obs.tgt_idx}-{obs.tgt_scan}." + \
                "Aborting observation.")
            action.set_obs_transition(obs=obs, transition=ObsTransition.ABORT)

        return False

    def reset_sdp_scan(self, obs, action):
        """ Adds a SDP reset scan command to the action object.
            This is used when an observation is aborted during a scan and needs to reset.
            Alternatively a previous observation is repeatedly executed, and the SDP needs to be informed to reset
            the current scan index.
        """        
        logger.info(f"Observation Execution Tool processing Reset Scan for observation {obs.obs_id}")

        # Inform the Science Data Processor that we are resetting this observation scan iter to zero
        old_config = {}
        new_config = {}

        old_config['obs_reset'] = None
        new_config['obs_reset'] = obs.obs_id
        new_config['sdp_id'] = self.telmodel.sdp.sdp_id

        action = self.tm.update_sdp_configuration(old_config, new_config, action)

    def is_on_target(self, obs, target, dish) -> bool:
        """ Check if the dish is currently configured to point at the current target and is in the correct pointing state.
            Returns None if not configured for the correct target, True if on target, False if not on target.
        """
        if obs is None or target is None or dish is None:
            raise XSoftwareFailure(f"Observation Execution Tool could not determine if dish is on target due to missing observation, dish or target.")

        target_id = obs.obs_id + f"-{obs.tgt_idx}" # Unique target identifier within the observation (see DishModel.tgt_id)
        if dish.tgt_id != target_id:
            logger.info(f"Observation Execution Tool found Dish {dish.dsh_id} is NOT configured to point to correct target {target_id} for observation {obs.obs_id} " +
                f"Dish target ID {dish.tgt_id} does not match expected target ID {target_id}.")
            return None

        on_target = True
        if target.pointing in [PointingType.SIDEREAL_TRACK,PointingType.NON_SIDEREAL_TRACK] and dish.pointing_state != PointingState.TRACK:
            on_target = False
        elif target.pointing == PointingType.DRIFT_SCAN and dish.pointing_state != PointingState.READY:
            on_target = False
        elif target.pointing in [PointingType.FIVE_POINT_SCAN, PointingType.OFFSET_SCAN] and dish.pointing_state != PointingState.SCAN:
            on_target = False

        logger.info(f"Observation Execution Tool is {'ON' if on_target else 'OFF'} target for observation {obs.obs_id}, target index {obs.tgt_idx}, " + \
             f"target ID {target_id}, pointing type {target.pointing.name}, dish pointing state {dish.pointing_state.name}, dish target ID {dish.tgt_id}, dish {dish.dsh_id}")

        return on_target
        
