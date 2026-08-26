from typing import Any, Dict

# Define DMD msg types
MSG_TYPE_REQ = "req"  # Request an action to be taken e.g. get or set a property that either succeeds or fails
MSG_TYPE_ADV = "adv"  # Advise that an action must be taken e.g. system is shutting down, so shutdown (no ifs or buts)
MSG_TYPE_RSP = "rsp"  # Response to a request or advice message

MSG_TYPES =  (
        MSG_TYPE_REQ,   
        MSG_TYPE_ADV,   
        MSG_TYPE_RSP,   
)

# Define DMD properties 
PROPERTY_TRACE  = "trace"    # Enable/disable tracing of API messages
PROPERTY_DEBUG  = "debug"    # Enable/disable debug logging of API messages
PROPERTY_STATUS = "status"   # Status of the application (e.g. health, availability, reliability)

PROPERTIES = (
        PROPERTY_TRACE,  
        PROPERTY_DEBUG,   
        PROPERTY_STATUS,  
)

# Define DMD action codes 
ACTION_CODE_GET = "get"         # Get the value of a property
ACTION_CODE_SET = "set"         # Set the value of a property
ACTION_CODE_RESYNC = "resync"   # Resync application configuration

ACTION_CODES = (
        ACTION_CODE_GET,      
        ACTION_CODE_SET,      
        ACTION_CODE_RESYNC,   
)

# Define DMD status codes
STATUS_SUCCESS   = "success"
STATUS_ERROR     = "error"

STATUS = (
        STATUS_SUCCESS,
        STATUS_ERROR,
)

# Define DMD origins (from) and destinations (to) of api msg calls
DM  = "dm"   # Dish Manager 
DIG = "dig"  # Digitiser 
TM  = "tm"   # Telescope Manager
SDP = "sdp"  # Science Data Processor
WS  = "ws"   # Weather Station
IMU = "imu"  # Inertial Measurement Unit
APP = "app"  # Client Application
CMD = "cmd"  # Command client used for application-wide control requests

# Allowable msg fields and types defining their format     
#   "field_name": "regex_pattern" | {"type": "type_name", "pattern": "regex_pattern", "enum": [...]} 
#   type_name is one of int, float, str, bool, list, dict, tuple

# Examples:
# "field_name": {"type": "int", "pattern": r"^\d{1,5}$"}  # Integer between 0 and 99999
# "field_name": {"type": "float", "pattern": r"^\d{1,5}\.\d{1,5}$"}  # Float between 0.0 and 99999.99999
# "field_name": {"type": "str", "pattern": r"^[A-Za-z0-9 _\-.,!?]+$"}  # String with certain allowed characters

# FIELD             TYPE                                            DESCRIPTION
METADATA_FIELD = {
    "property":     {"type": "str", "enum": PROPERTIES},           # Property name (one of PROPERTIES)
    "value":        {"type": Any},                                 # Value of the property (type depends on property)
}

# FIELD             TYPE                                            DESCRIPTION
MSG_FIELDS = {
    "msg_type":     {"enum": MSG_TYPES},                            # Message type (one of MSG_TYPES)
    "action_code":  {"enum": ACTION_CODES},                         # Action to be taken (one of ACTION_CODES)
    "metadata":     {
        "type":         "list",                                     # Metadata is a list
        "value_type":   "dict",                                     # Each value is a dict
        "value_schema": METADATA_FIELD},                            # Each value should match METADATA_FIELD
    "status":       {"enum": STATUS},                               # Status of response (e.g. success, error)
    "message":      {"type": "str"},                                # Additional information about the status
}

# Definition of required, conditional and optional fields for each api msg type
MSG_FIELDS_DEFINITIONS = {
   "req": {
        "required": {"msg_type", "action_code"},
        "conditional": {
            "property",     # Required if action_code is "get" or "set"
            "value",        # Required if action_code is "set" 
            "method",       # Required if action_code is "method"
            "params"        # Required if action_code is "method"
        },
    },
    "adv": {
        "required": {"msg_type", "action_code", "metadata"},
        "optional": {"status", "message"},   
    },
    "rsp": {
        "required": {"msg_type", "action_code", "status"},
        "conditional": {},
        "optional": {"message"},
        "optional": {"property"},   # Copied from req/adv
        "optional": {"value"},      
        "optional": {"method"},     # Copied from req/adv
        "optional": {"params"},
    },
}

"""
Example message flows between applications.
Time flows downwards.

Successful request/response message flow:

    +----------------------+                         +----------------------+
    | Requesting app       |                         | Responding app       |
    +----------------------+                         +----------------------+
               |                                                   |
               |-------------------- req ------------------------->|
               |<------------- rsp (success) ----------------------|
               |                                                   |


Request timeout, repeat, response flow:

    +----------------------+                         +----------------------+
    | Requesting app       |                         | Responding app       |
    +----------------------+                         +----------------------+
               |                                                   |
               |-------------------- req ------------------------->|
               |                                                   |
               |             response timeout                      |
               |                                                   |
               |--------------- req (repeat) --------------------->|
               |<------------- rsp (success) ----------------------|
               |                                                   |


Advice, response flow:

    +----------------------+                         +----------------------+
    | Advising app         |                         | Receiving app        |
    +----------------------+                         +----------------------+
               |                                                   |
               |---------------------- adv ----------------------->|
               |<----------------- rsp (success) ------------------|
               |                                                   |

Status advice flow:

    +----------------------+                         +----------------------+
    | Advising app         |                         | Receiving app        |
    +----------------------+                         +----------------------+
               |                                                   |
               |------------------- status adv ------------------->|
               |                no response needed                 |
               |                                                   |
               |                  30 sec later                     |
               |                                                   |
               |------------------- status adv ------------------->|
               |                   no response                     |
                                        etc
"""
