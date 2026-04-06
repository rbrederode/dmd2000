from env.processor import Processor
from env.events import InitEvent, StatusUpdateEvent, ConfigEvent, ObsEvent
from queue import Queue, Empty
from datetime import datetime, timezone
import time
import json
from api.api import API
from ipc.tcp_server import TCPServer
from ipc.message import AppMessage, APIMessage
from ipc.action import Action
from models.base import BaseModel
from models.comms import InterfaceType
from util.xbase import XBase
from util.timer import Timer, TimerManager
from env import events

import logging
logger = logging.getLogger(__name__)

class AppProcessor(Processor):

    def __init__(self, name=None, event_q=None, driver=None):
        super().__init__(name=name, event_q=event_q)
        self.driver = driver
        self.debug = False

    def initialise_app(self):

        Processor.single_thread()

        handler_method = "process_init"

        self.performActions(getattr(self.driver, handler_method)())
        logger.debug(f"AppProcessor {self.name} initialised")

        Processor.free_thread()

    def process_config_event(self, event: ConfigEvent):

        Processor.single_thread()

        handler_method = "process_config"

        self.performActions(getattr(self.driver, handler_method)(event))
        logger.debug(f"AppProcessor {self.name} config resync'ed")

        Processor.free_thread()

    def process_status_update(self, event: StatusUpdateEvent):
        
        try:
            event.notify_dequeued()

            handler_method = "get_health_state"
            if hasattr(self.driver, handler_method) and callable(getattr(self.driver, handler_method)):
                self.driver.set_health_state(getattr(self.driver, handler_method)())

            logger.debug(f"AppProcessor {self.name} health state is {self.driver.app_model.health.name}")

            handler_method = "process_status_event"
            if hasattr(self.driver, handler_method) and callable(getattr(self.driver, handler_method)):
                self.performActions(getattr(self.driver, handler_method)(event))

        finally:
            event.notify_update_completed()

    def _get_entity(self, event) -> (str, BaseModel):
        """Resolve the entity id from the event by calling the driver's get_<from_system>_entity handler.
            : param event: The event to extract the entity ID from
            : return: A tuple of (entity ID, entity) or (None, None) if not found, 
        """
        entity = (None, None)

        # Check if the event is a ConnectEvent, DisconnectEvent, or DataEvent
        if isinstance(event, events.ConnectEvent) or isinstance(event, events.DisconnectEvent) or isinstance(event, events.DataEvent):
        
            api, endpoint, interface_type = self.driver.get_interface(event.local_sap.description)

            if interface_type == InterfaceType.ENTITY_DRIVER:

                # Resolve the entity from the event using the driver's get_<from_system>_entity handler
                handler_method = "get_" + event.local_sap.description + "_entity"

                if hasattr(self.driver, handler_method) and callable(getattr(self.driver, handler_method)):
                    try:
                        entity = getattr(self.driver, handler_method)(event) # Expecting a tuple (entity_id, entity)
                    except Exception as e:
                        logger.exception(self.driver.set_last_err(f"AppProcessor {self.name} exception in driver handler {handler_method} while processing event {event}: {e}"))
                        return None, None
                else:
                    logger.warning(f"AppProcessor {self.name} driver has no handler to get entity ID from event {event}")
                    return None, None

        return entity

    def _construct_rsp_msg(self, api_msg: APIMessage, status: str, message: str) -> APIMessage:
        """Construct a response APIMessage based on the given api_msg with the specified status and message.
            : param api_msg: The original APIMessage to respond to
            : param status: The status string ('success' or 'error')
            : param message: The message string providing additional information
            : return: A new APIMessage representing the response
        """
        rsp_msg = APIMessage(api_msg.get_json_api_header())
        rsp_msg.switch_from_to()
        
        api_call = rsp_msg.get_api_call()
        api_call['msg_type'] = 'rsp'
        api_call['status'] = status
        api_call['message'] = message

        rsp_msg.set_api_call(api_call)
        return rsp_msg
    
    def process_event(self, event) -> bool:

        start_time = time.time()
        st = datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat()
        
        logger.debug(f"AppProcessor {self.name} started processing event {type(event)} at {st}")

        try:
            if isinstance(event, InitEvent):

                try:
                    self.initialise_app()
                except Exception as e:
                    logger.exception(self.driver.set_last_err(f"AppProcessor: Exception initialising app: {e}"))
                    return False

            elif isinstance(event, StatusUpdateEvent):

                try:
                    self.process_status_update(event)
                except Exception as e:
                    logger.exception(self.driver.set_last_err(f"AppProcessor: Exception processing status update event {event}: {e}"))
                    return False

            elif isinstance(event, events.TimerEvent):

                if event.timer_cancelled:
                    logger.debug(f"AppProcessor {self.name} ignoring a cancelled timer event: {event}")
                    return True

                if event.user_callback is not None:
                    logger.debug(f"AppProcessor {self.name} received timer event with callback: {event}")
                    try:
                        event.user_callback(event.user_ref)
                    except Exception as e:
                        logger.exception(self.driver.set_last_err(f"AppProcessor {self.name} exception in user callback for timer event {event}: {e}"))
                        return False

                handler_method = "process_timer_event"

                if hasattr(self.driver, handler_method) and callable(getattr(self.driver, handler_method)):
                    try:
                        self.performActions(getattr(self.driver, handler_method)(event))
                    except Exception as e:
                        logger.exception(self.driver.set_last_err(f"AppProcessor {self.name} exception in driver handler {handler_method} while processing timer event {event}: {e}"))
                        return False
                return True

            elif isinstance(event, events.DataEvent):
            
                api_msg = APIMessage()

                try:
                    # Unpack the event's data into an API message
                    api_msg.from_data(event.data)
                    api_msg.add_echo_api_header()

                    api, endpoint, interface_type = self.driver.get_interface(api_msg.get_from_system())

                    # Validate and translate the API message to the driver's API version
                    api_transl_msg = api.translate(api_msg.get_json_api_header())
                    api.validate(api_transl_msg)

                    # Safely resolve the driver's application name
                    if getattr(self.driver, "app_model", None) is not None and hasattr(self.driver.app_model, "app_name"):
                        driver_app_name = self.driver.app_model.app_name
                    else:
                        logger.error(self.driver.set_last_err(f"AppProcessor {self.name} driver has no app_model.app_name attribute"))
                        driver_app_name = getattr(self.driver, "app_name", None) or type(self.driver).__name__

                    # Check if the API message is not intended for this App (using from_system/to_system api header fields)
                    if api_msg.get_to_system() != driver_app_name:
                        logger.warning(f"AppProcessor {self.name} received API message intended for different App: {api_msg.get_to_system()} (this App: {driver_app_name}): {event}")
                        rsp_msg = self._construct_rsp_msg(api_msg, 'error', f"Message not intended for {driver_app_name}, but for {api_msg.get_to_system()}")
                        self.performActions(Action().set_msg_to_remote(rsp_msg), event.local_sap, event.remote_conn, event.remote_addr)
                        return True

                    # Handle debug get/set requests
                    api_call = api_msg.get_api_call()
                    if api_call['msg_type'] == 'req' and api_call['action_code'] in ('set', 'get') and api_call['property'] in ("debug"):
                        rsp_msg = self._handle_debug_req(api_msg, api_call)
                        self.performActions(Action().set_msg_to_remote(rsp_msg), event.local_sap, event.remote_conn, event.remote_addr)
                        return True

                    # If ENTITY_DRIVER interface, perform entity matching and setup appropriate handler
                    if interface_type == InterfaceType.ENTITY_DRIVER:

                        # Ask the driver if it can resolve the entity from the event based on entity configuration
                        entity_id, entity = self._get_entity(event)
                        # Perform entity id matching betweem the incoming message and the driver entity configuration
                        entity_match = not (api_msg.get_entity() is None or entity_id is None or api_msg.get_entity() != entity_id)

                        # If no entity match (or entity unknown), respond with an error
                        if not entity_match:
                            logger.warning(f"AppProcessor {self.name} received API message for unknown Entity {api_msg.get_entity()}. Check configuration!\n{event}")
                            rsp_msg = self._construct_rsp_msg(api_msg, 'error', f"Received API message for unknown entity {driver_app_name}:{api_msg.get_entity()}. Check configuration!")
                            self.performActions(Action().set_msg_to_remote(rsp_msg), event.local_sap, event.remote_conn, event.remote_addr)
                            return True

                        # Store the entity_id and corresponding connection in the driver's entity connection map for future use    
                        self.driver.entity_connection_map[entity_id] = (event.remote_conn, event.remote_addr)

                        handler_method = "process_" + api_msg.get_from_system() + "_entity_msg"
                        handler_parameters = (event, api_msg.get_json_api_header(), api_msg.get_api_call(), api_msg.get_payload_data(), entity)

                    else:

                        handler_method = "process_" + api_msg.get_from_system() + "_msg"
                        handler_parameters = (event, api_msg.get_json_api_header(), api_msg.get_api_call(), api_msg.get_payload_data())
                    
                    # Invoke the appropriate driver handler with its parameters
                    if hasattr(self.driver, handler_method) and callable(getattr(self.driver, handler_method)):
                        try:
                            self.performActions(getattr(self.driver, handler_method)(*handler_parameters), 
                                event.local_sap, event.remote_conn, event.remote_addr)
                        except Exception as e:
                            logger.exception(self.driver.set_last_err(f"AppProcessor {self.name} exception in driver handler {handler_method} while processing message " + \
                                f"from {api_msg.get_from_system()}.\n{event}\nException: {e}"))
                            return False
                    else:
                        logger.warning(f"AppProcessor {self.name} driver has no handler {handler_method} for messages from {api_msg.get_from_system()}.\n{event}")

                except XBase as e:
                    logger.exception(self.driver.set_last_err(f"AppProcessor {self.name} failed to process data event from Service Access Point {event.local_sap.description}: {e}"))
                    return False

                return True

            elif isinstance(event, events.ConnectEvent):

                api, endpoint, interface_type = self.driver.get_interface(event.local_sap.description)

                # Ensure the connection is coming from a known entity for ENTITY_DRIVER interfaces
                if interface_type == InterfaceType.ENTITY_DRIVER:

                    entity_id, entity = self._get_entity(event)

                    if entity_id is None or entity is None:
                        logger.error(self.driver.set_last_err(f"AppProcessor {self.name} received connect event from unknown entity on {interface_type.name} interface.\n{event}"))
                        return False

                    # Store the entity_id and corresponding connection in the driver's entity connection map for future use
                    self.driver.entity_connection_map[entity_id] = (event.remote_conn, event.remote_addr)

                    handler_method = "process_" + event.local_sap.description + "_entity_connected"
                    handler_parameters = (event, entity)

                else:
                    handler_method = "process_" + event.local_sap.description + "_connected"
                    handler_parameters = (event,)

                if hasattr(self.driver, handler_method) and callable(getattr(self.driver, handler_method)):
                    try:
                        self.performActions(getattr(self.driver, handler_method)(*handler_parameters),
                            event.local_sap, event.remote_conn, event.remote_addr)
                    except Exception as e:
                        logger.exception(self.driver.set_last_err(f"AppProcessor {self.name} exception in driver handler {handler_method} while processing connect event {event}: {e}"))
                        return False
                return True
                
            elif isinstance(event, events.DisconnectEvent):
                
                api, endpoint, interface_type = self.driver.get_interface(event.local_sap.description)

                # Check if the disconnect is for an ENTITY_DRIVER interface
                if interface_type == InterfaceType.ENTITY_DRIVER:

                    entity_id, entity = self._get_entity(event)

                    if entity_id is None or entity is None:
                        logger.error(self.driver.set_last_err(f"AppProcessor {self.name} received disconnect event from unknown entity on {interface_type.name} interface.\n{event}"))
                        return False

                    # Remove the entity connection from the driver's entity connection map
                    if entity_id in self.driver.entity_connection_map:
                        del self.driver.entity_connection_map[entity_id]

                    handler_method = "process_" + event.local_sap.description + "_entity_disconnected"
                    handler_parameters = (event, entity)
                    
                else:
                    handler_method = "process_" + event.local_sap.description + "_disconnected"
                    handler_parameters = (event,)

                if hasattr(self.driver, handler_method) and callable(getattr(self.driver, handler_method)):
                    try:
                        self.performActions(getattr(self.driver, handler_method)(*handler_parameters),
                            event.local_sap, event.remote_conn, event.remote_addr)
                    except Exception as e:
                        logger.exception(self.driver.set_last_err(f"AppProcessor {self.name} exception in driver handler {handler_method} while processing disconnect event {event}: {e}"))
                        return False
                return True

            elif isinstance(event, events.ConfigEvent):
                try:
                    self.process_config_event(event)
                except Exception as e:
                    logger.exception(self.driver.set_last_err(f"AppProcessor: Exception processing config event {event}: {e}"))
                    return False
                return True

            elif isinstance(event, events.ObsEvent):

                handler_method = "process_obs_event"

                if hasattr(self.driver, handler_method) and callable(getattr(self.driver, handler_method)):
                    try:
                        self.performActions(getattr(self.driver, handler_method)(event))
                    except Exception as e:
                        logger.exception(self.driver.set_last_err(f"AppProcessor {self.name} exception in driver handler {handler_method} while processing observation event {event}: {e}"))
                        return False
                return True

            else:
                return False  # Event not processed

        finally:
            end_time = time.time()
            et = datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat()
            logger.debug(f"AppProcessor {self.name} finished processing event {type(event)} at {et} taking {(end_time-start_time):.3f} seconds")

        return True

    def performActions(self, action: Action, local_sap=None, remote_conn=None, remote_addr=None):
        """Performs the actions specified in the Action object.
            Remove actions from the Action object once performed.
            Leave actions in the Action object if they could not be performed.
            : param action: The Action object containing the actions to perform
            : param local_sap: The local service access point (TCPServer or TCPClient)associated with the event (if any)
            : param remote_conn: The remote connection socket associated with the event (if any)
            : param remote_addr: The remote address associated with the event (if any)
            Call the superclass method at the end to process any remaining actions.
        """

        # if no actions to perform, return
        if action is None:
            return

        logger.debug(f"AppProcessor {self.name} performing actions: {action}")

        # Perform message actions
        for msg in action.msgs_to_remote[:]:    # Iterate over a copy [:] of the list to allow removal during iteration

            logger.debug(f"AppProcessor {self.name} performing action: send message to remote:\n{msg}")

            if not isinstance(msg, APIMessage):
                logger.error(self.driver.set_last_err(f"AppProcessor {self.name} failed to perform action 'send message to remote' because message is not an APIMessage instance:\n{msg}"))
                continue

            dest_system = msg.get_to_system()
            api, endpoint, interface_type = self.driver.get_interface(dest_system)

            msg_to_send = msg

            try:
                api.validate(msg.get_json_api_header())
                api_header = msg.get_echo_api_header()
                
                if api_header is not None:
                    orig_version = api_header.get('api_version', api.get_api_version())
                    msg.remove_echo_api_header()
                    api_transl_msg = api.translate(api_msg=msg.get_json_api_header(), target_version=orig_version)

                    msg_to_send = APIMessage(api_msg=api_transl_msg, payload=msg.get_payload_data())

            except XBase as e:
                logger.error(self.driver.set_last_err(f"AppProcessor {self.name} failed to perform action 'send message to remote' because validate/translate of API message failed: {e} Message:\n{msg}"))
                continue

            # If the destination endpoint is the same as the local_sap of the originating event, send the message on the originating connection (client_socket)
            if endpoint == local_sap and remote_conn is not None:

                endpoint.send(msg_to_send, remote_conn)  # Send the message on the originating connection (socket)

            elif interface_type in [InterfaceType.ENTITY_DRIVER]:
                entity_id = msg.get_entity()

                if entity_id is None:
                    logger.error(self.driver.set_last_err(f"AppProcessor {self.name} failed to perform action 'send message to remote' because entity ID is not specified in message for an ENTITY interface:\n{msg}"))
                    continue
                if entity_id not in self.driver.entity_connection_map:
                    logger.error(self.driver.set_last_err(f"AppProcessor {self.name} failed to perform action 'send message to remote' because no connection found for entity ID {entity_id} in ENTITY interface:\n{msg}"))
                    continue
                else:
                    conn, addr = self.driver.entity_connection_map[entity_id]

                endpoint.send(msg_to_send, conn)         # Send the message on the entity connection (socket)
            else:
                endpoint.send(msg_to_send)               # Send the message on the registered endpoint's default connection (socket)
        
            action.msgs_to_remote.remove(msg)  # Remove the msg from the list                
        
        # Perform timer actions
        for timer in action.timer_actions[:]:  # Iterate over a copy [:] of the list to allow removal during iteration

            logger.debug(f"AppProcessor {self.name} performing action: set timer: {timer}")

            if not isinstance(timer, Action.Timer):
                logger.error(self.driver.set_last_err(f"AppProcessor {self.name} failed to perform timer action {timer} because it is not an Action.Timer instance"))
                continue

            if Timer.manager is None:
                logger.error(self.driver.set_last_err(f"AppProcessor {self.name} failed to perform timer action {timer} because Timer.manager is not initialized"))
                continue

            timers = Timer.manager.get_timers_by_name(timer.name)

            for t in timers:
                logger.debug(f"AppProcessor {self.name} cancelling existing timer: {t}")
                t.cancel()

            if timer.get_timer_action() != Action.Timer.TIMER_STOP:

                new_timer = Timer(                      # Create a new timer
                    name=timer.get_name(), 
                    event_q=self.get_queue(), 
                    duration_ms=timer.get_timer_action(), 
                    user_ref=timer.get_echo_data())  

                Timer.manager.add_timer(new_timer)

                action.timer_actions.remove(timer)      # Remove the timer action from the list
                logger.debug(f"AppProcessor {self.name} started new timer: {new_timer}")
            
        # Perform connection actions
        for conn_action in action.connection_actions[:]:  # Iterate over a copy [:] of the list to allow removal during iteration

            logger.debug(f"AppProcessor {self.name} performing action: set connection: {conn_action}")

            if not isinstance(conn_action, Action.Connection):
                logger.error(self.driver.set_last_err(f"AppProcessor {self.name} failed to perform connection action {conn_action} because it is not an Action.Connection instance"))
                continue

            # Placeholder for actual connection handling logic
            action.connection_actions.remove(conn_action)  # Remove the connection action from the list
            logger.debug(f"AppProcessor {self.name} processed connection action: {conn_action}")

        # For each observation transition action, create an ObsEvent and enqueue it (hand it over to another processor)
        for obs_transition in action.obs_transitions[:]:  # Iterate over a copy [:] of the list to allow removal during iteration

            logger.debug(f"AppProcessor {self.name} performing action: observation transition: {obs_transition}")

            if not isinstance(obs_transition, Action.Transition):
                logger.error(self.driver.set_last_err(f"AppProcessor {self.name} failed to perform observation transition action {obs_transition} because it is not an Action.Transition instance"))
                continue

            obs_event = ObsEvent(transition=obs_transition.get_transition(), obs=obs_transition.get_obs(), user_ref=obs_transition.get_echo_data(), timestamp=datetime.now(timezone.utc))
            self.get_queue().put(obs_event)  # Enqueue the observation event for processing

            action.obs_transitions.remove(obs_transition)  # Remove the observation transition action from the list
            logger.debug(f"AppProcessor {self.name} processed observation transition action: {obs_transition}")

    def _handle_debug_req(self, api_msg: APIMessage, api_call: dict) -> APIMessage:
        
        prop_name = api_call['action_code'] + '_' + api_call['property']
        prop_value = api_call['value']

        status = 'success'

        if prop_name in ('set_debug') and prop_value in ('on'):

            self.debug = True
            logger.setLevel(logging.DEBUG)
            logger.info(f"AppProcessor {self.name} set debug level to ON")
            message = f"Debug level set to ON"

        elif prop_name in ('set_debug') and prop_value in ('off'):
            
            self.debug = False
            logger.setLevel(logging.INFO)
            logger.info(f"AppProcessor {self.name} set debug level to OFF")
            message = f"Debug level set to OFF"

        elif prop_name in ('get_debug'):
            
            logger.info(f"AppProcessor {self.name} debug level is { 'ON' if self.debug else 'OFF' }")
            message = f"Debug level is { 'ON' if self.debug else 'OFF' }"

        else:

            status = 'error'
            message = f"Unknown property or value: {prop_name}={prop_value}"
            logger.warning(f"AppProcessor {self.name} {message}")
        
        rsp_msg = APIMessage(api_msg.get_json_api_header())
        rsp_msg.switch_from_to()
        
        api_call = rsp_msg.get_api_call()
        api_call['status'] = status
        api_call['message'] = message
        api_call['value'] = 'ON' if self.debug else 'OFF'

        rsp_msg.set_api_call(api_call)
        return rsp_msg
        
if __name__ == "__main__":
    import queue
    import time

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,  # Or DEBUG for more verbosity
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logger = logging.getLogger(__name__)

    q = queue.Queue()

    test1 = AppProcessor(name="Test1", event_q=q)
    test2 = AppProcessor(name="Test2", event_q=q)
    test3 = AppProcessor(name="Test3", event_q=q)
    test4 = AppProcessor(name="Test4", event_q=q)

    test1.start()
    test2.start()
    test3.start()
    test4.start()

    status_update_event = StatusUpdateEvent()
    status_update_event.notify_queued()

    q.put(InitEvent("ComponentA"))
    q.put(status_update_event)

    time.sleep(1)  # Give threads time to start

    for i in range(300):
        q.put(f"Event {i}")

    time.sleep(2)  # Allow some time for processing

    Processor.stop_all()
