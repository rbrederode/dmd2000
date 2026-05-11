from ipc.message import APIMessage
from models.obs import ObsModel
from util.timer import Timer
from util.xbase import XSoftwareFailure

import logging
logger = logging.getLogger(__name__)

class Action:

    class Comms:
            
        CONNECT = -2
        DISCONNECT = -3        

        def __init__(self, endpoint_name: str, comms_action: int):
            if endpoint_name is None:
                raise XSoftwareFailure("Action.Comms endpoint_name cannot be None")

            if comms_action is None:
                raise XSoftwareFailure("Action.Comms comms_action cannot be None")

            self.endpoint_name = endpoint_name
            self.comms_action = comms_action
        
        def get_endpoint_name(self):
            return self.endpoint_name
        
        def get_comms_action(self):
            return self.comms_action

        def __str__(self):
            return f"Action.Comms\n" + \
                f" - Endpoint Name: {self.endpoint_name}\n" + \
                f" - Comms Action: {self.comms_action}\n"

    class Timer:

        TIMER_STOP = -1
        TIMER_START = 0  # and above are valid timer actions

        def __init__(self, name:str, timer_action:int, echo_data=None):

            if name is None:
                raise XSoftwareFailure("Action.Timer name cannot be None")

            if timer_action is None:
                raise XSoftwareFailure("Action.Timer timer_action cannot be None")

            self.name = name
            self.timer_action = timer_action
            self.echo_data = echo_data

        def get_name(self):
            return self.name

        def get_timer_action(self):
            return self.timer_action

        def get_echo_data(self):
            return self.echo_data

        def __str__(self):
            return f"Action.Timer\n" + \
                f" - Name: {self.name}\n" + \
                f" - Timer Action: {self.timer_action}\n" + \
                f" - Echo Data: {self.echo_data}\n"

    class Transition:

        def __init__(self, obs: ObsModel=None, transition=None, echo_data=None):

            if transition is None:
                raise XSoftwareFailure("Action.Transition transition cannot be None")

            if obs is None:
                raise XSoftwareFailure("Action.Transition obs cannot be None")

            self.obs = obs
            self.transition = transition
            self.echo_data = echo_data

        def get_transition(self):
            return self.transition

        def get_obs(self):
            return self.obs

        def get_echo_data(self):
            return self.echo_data

        def __str__(self):
            return f"Action.Transition\n" + \
                f" - Obs: {self.obs}\n" + \
                f" - Transition Type: {self.transition.name if self.transition else None}\n" + \
                f" - Echo Data: {self.echo_data}\n"

    #--------------------------
    # Main Action class methods
    #--------------------------

    def __init__(self):
        self.cause = None
        self.msgs_to_remote = []
        self.timer_actions = []
        self.connection_actions = []
        self.obs_transitions = []

    def set_cause(self, cause):
        self.cause = cause

    def set_msg_to_remote(self, msg: APIMessage):
        if msg is not None:
            self.msgs_to_remote.append(msg)
        return self

    def set_connection_action(self, comms_action: "Action.Comms"):
        if comms_action is not None and comms_action.get_comms_action() is not None:
            self.connection_actions.append(comms_action)
        return self

    def set_timer_action(self, timer_action: "Action.Timer"):
        if timer_action is not None and timer_action.get_timer_action() is not None:
            self.timer_actions.append(timer_action)
        return self

    def set_obs_transition(self, transition=None, obs: ObsModel=None, echo_data=None):
        transition = Action.Transition(transition=transition, obs=obs, echo_data=echo_data)
        self.obs_transitions.append(transition)
        return self

    def clear_msgs_to_remote(self):
        self.msgs_to_remote.clear()
        return self

    def clear_timer_actions(self):
        self.timer_actions.clear()
        return self

    def clear_connection_actions(self):
        self.connection_actions.clear()
        return self

    def clear_obs_transitions(self):
        self.obs_transitions.clear()
        return self

    def is_empty(self) -> bool:
        return (len(self.msgs_to_remote) == 0 and
                len(self.timer_actions) == 0 and
                len(self.connection_actions) == 0 and
                len(self.obs_transitions) == 0)

    def __str__(self):
        return f"Action\n" + \
            f" - Cause: {self.cause}\n" + \
            f" - Messages to Remote: {self.msgs_to_remote}\n" + \
            f" - Timer Actions: {self.timer_actions}\n" + \
            f" - Connection Actions: {self.connection_actions}\n" + \
            f" - Observation Transitions: {self.obs_transitions}\n"
