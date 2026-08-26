import json
import re
import datetime
from datetime import timezone
from typing import Any, Dict
from api.api import API
import api.protocol as dmd_protocol
from ipc.message import Message, AppMessage, APIMessage
from util.xbase import XBase, XStreamUnableToExtract, XStreamUnableToEncode, XAPIValidationFailed, XAPIUnsupportedVersion

import logging
logger = logging.getLogger(__name__)

API_VERSION = "1.0" # Version of the API implemented in this module.
LEGACY_SUPPORTED_VERSIONS = [] # Requires translator methods to/from API_VERSION

MSG_TYPES    = dmd_protocol.MSG_TYPES
ACTION_CODES = dmd_protocol.ACTION_CODES
STATUS       = dmd_protocol.STATUS

# Allowable origins (from) and destinations (to) of api msg calls
FROM = (
    dmd_protocol.TM,
    dmd_protocol.SDP,
)

TO = (
    dmd_protocol.SDP,
    dmd_protocol.TM,
)

# Allowable properties to get or set on the system
PROPERTY_SCAN_CONFIG    = 'scan_config'      # Set scan configuration (e.g. center frequency etc.) for a digitiser
PROPERTY_SCAN_COMPLETE  = 'scan_complete'    # Notify Telescope Manager that a scan has completed
PROPERTY_OBS_RESET      = 'obs_reset'        # Notify Science Data Processor to reset observation data
PROPERTY_OBS_COMPLETE   = 'obs_complete'     # Notify Science Data Processor that an observation has completed
PROPERTY_SIGNAL_DISPLAY = 'signal_display'   # Control the signal display (show/hide) for a digitiser
PROPERTY_SPECTRAL_RESOLUTION = 'spectral_resolution'  # Number of spectral bins / FFT size (part of scan_config)
PROPERTY_CHANNELS       = 'channels'         # Legacy name for spectral_resolution
PROPERTY_SCAN_DURATION  = 'scan_duration'    # Scan duration in seconds (part of scan_config)

PROPERTIES = dmd_protocol.PROPERTIES + (
    PROPERTY_SCAN_CONFIG,
    PROPERTY_SCAN_COMPLETE,
    PROPERTY_OBS_COMPLETE,
    PROPERTY_OBS_RESET,
    PROPERTY_SIGNAL_DISPLAY,
)

# Allowable msg fields and types defining their format     
#   "field_name": "regex_pattern" | {"type": "type_name", "pattern": "regex_pattern", "enum": [...]} 
#   type_name is one of int, float, str, bool, list, dict, tuple

# Examples:
# "field_name": {"type": "int", "pattern": r"^\d{1,5}$"}  # Integer between 0 and 99999
# "field_name": {"type": "float", "pattern": r"^\d{1,5}\.\d{1,5}$"}  # Float between 0.0 and 99999.99999
# "field_name": {"type": "str", "pattern": r"^[A-Za-z0-9 _\-.,!?]+$"}  # String with certain allowed characters

# FIELD             TYPE                                            DESCRIPTION
MSG_FIELDS = {
    "msg_type":     {"enum": MSG_TYPES},                            # Message type (one of MSG_TYPES)
    "action_code":  {"enum": ACTION_CODES},                         # Action to be taken (one of ACTION_CODES)
    "property":     {"enum": PROPERTIES},                           # Property name (one of PROPERTIES)
    "value":        {"type": "(int, float, str, dict)"},            # Value to set or value returned
    "status":       {"enum": STATUS},                               # Status of response (e.g. success, error)
    "message":      {"type": "str"},                                # Additional information about the status
    "obs_data":     {"type": "dict"},                               # Observation data in dictionary format
}

# Definition of required, conditional and optional fields for each api msg type
MSG_FIELDS_DEFINITIONS = {
    "req": {
        "required": {"msg_type", "action_code"},
        "conditional": {
            "property",     # Required if action_code is "get" or "set"
            "value",        # Required if action_code is "set" 
         },
         "optional": {"obs_data"},  # Optional field
    },
    "adv": {
        "required": {"msg_type", "action_code"},
        "conditional": {
            "property",     # Required if action_code is "get" or "set"
            "value",        # Required if action_code is "set"
        },
        "optional": {"obs_data"},  # Optional field
    },
    "rsp": {
        "required": {"msg_type", "action_code", "status"},
        "optional": {"message"},
        "optional": {"property"},   # Copied from req/adv
        "optional": {"value"},      # Copied from req/adv
        "optional": {"obs_data"},   # Optional field
    },
}

class TM_SDP(API):
    """
    Class responsible for enforcing the Telescope Manager-Science Data Processor API.

    The API defines the structure and rules for API messages between these systems.

    API calls are wrapped within an API message before being exchanged. This enforces that 
    each API call is associated with metadata such as the source and destination of the call,
    the version of the API being used, and a timestamp.

    This class provides methods to validate and translate API messages.
    """

    def __init__(self):
        super().__init__()

    def get_api_version(self) -> str:
        """ Returns the API version implemented by this class.
        """
        return API_VERSION

    def get_legacy_supported_versions(self) -> list:
        """ Returns a list of legacy API versions supported by this class.
        """
        return LEGACY_SUPPORTED_VERSIONS

    def validate(self, api_msg: Dict[str, Any]):
        """
        Validates that the api_msg dictionary contains the required fields and that the fields
        conform to the implementation's API_VERSION types and formats.
            :param api_msg: APIMessage dictionary containing the API call in API_VERSION format
            :raises XAPIValidationFailed: If the message fails validation
        """

        logger.debug(f"Validating API message: {json.dumps(api_msg, indent=4)}")

        if 'api_version' not in api_msg:
            raise XAPIValidationFailed("Message missing required field 'api_version'")

        if api_msg['api_version'] != API_VERSION:
            if api_msg['api_version'] not in LEGACY_SUPPORTED_VERSIONS:
                raise XAPIValidationFailed(f"Unsupported API version {api_msg['api_version']}")

        # API call is contained within the API message
        api_call = api_msg['api_call'] 

        if 'msg_type' not in api_call:
            raise XAPIValidationFailed("Message missing required field 'msg_type'")

        msg_type = api_call['msg_type']
        if msg_type not in MSG_FIELDS_DEFINITIONS:
            raise XAPIValidationFailed(f"Unsupported message type '{msg_type}'")

        # Check for required fields
        required_fields = MSG_FIELDS_DEFINITIONS[msg_type].get('required', set())
        for field in required_fields:
            if field not in api_call:
                raise XAPIValidationFailed(f"Message of type '{msg_type}' missing required field '{field}'")

        # Check for conditional fields
        conditional_fields = MSG_FIELDS_DEFINITIONS[msg_type].get('conditional', set())
        for field in conditional_fields:
            # Switch based on the field and its conditions
            if field == "property":
                if api_call.get('action_code') in (dmd_protocol.ACTION_CODE_GET, dmd_protocol.ACTION_CODE_SET) and 'property' not in api_call:
                    raise XAPIValidationFailed(f"Message of type '{msg_type}' with action_code '{api_call.get('action_code')}' missing required field 'property'")
            elif field == "value":
                if api_call.get('action_code') == dmd_protocol.ACTION_CODE_SET and 'value' not in api_call:
                    raise XAPIValidationFailed(f"Message of type '{msg_type}' with action_code '{dmd_protocol.ACTION_CODE_SET}' missing required field 'value'")
  
        # Validate each field's value against its expected type and format
        for field, value in api_call.items():
            if field in MSG_FIELDS:
                if isinstance(MSG_FIELDS[field], str):
                    if not re.fullmatch(MSG_FIELDS[field], str(value)):
                        raise XAPIValidationFailed(f"Invalid value for field '{field}': {value}")
                elif isinstance(MSG_FIELDS[field], dict):
                    if 'type' in MSG_FIELDS[field]:
                        expected_type = MSG_FIELDS[field]['type']
                        if not isinstance(value, eval(expected_type)):
                            raise XAPIValidationFailed(f"Invalid type for field '{field}': expected {expected_type}, got {type(value).__name__}")
                        # Check pattern if present
                        if 'pattern' in MSG_FIELDS[field]:
                            if not re.fullmatch(MSG_FIELDS[field]['pattern'], str(value)):
                                raise XAPIValidationFailed(f"Invalid pattern for field '{field}': {value}")
                    if 'enum' in MSG_FIELDS[field]:
                        if value not in MSG_FIELDS[field]['enum']:
                            raise XAPIValidationFailed(f"Invalid value for field '{field}': {value}")

    def translate(self, api_msg: Dict[str, Any], target_version: str=API_VERSION) -> Dict[str, Any]:
        """
        Translates an api_msg dictionary to a target_version.
        Must support translation between an API implementation's API_VERSION and LEGACY_SUPPORTED_VERSIONS.
        Must support bi-directional translation i.e. to and from versions.
            :param api_msg: Dictionary containing an APIMessage dictionary
            :param target_version: Target version of the API
            :return: Translated api message dictionary
            :raises XAPIValidationFailed: If the api message fails validation
        """

        if 'api_version' not in api_msg:
            raise XAPIValidationFailed("Message missing required field 'api_version'")

        if api_msg['api_version'] not in LEGACY_SUPPORTED_VERSIONS + [API_VERSION]:
            raise XAPIValidationFailed(f"Unsupported API version {api_msg['api_version']}")

        source_version = api_msg['api_version']

        if target_version is None or source_version == target_version:
            return api_msg

        # Example translation logic (expand as needed for real differences)
        translated_msg = api_msg.copy()

        if source_version == "1.0" and target_version == "2.0":
            logger.debug("Translating from 1.0 to 2.0")
            # Example: rename 'fc' to 'center_freq'
            if translated_msg['api_call'].get("property") == "fc":
                logger.debug("Renaming property 'fc' to 'center_freq'")
                translated_msg['api_call']["property"] = PROPERTY_CENTER_FREQ

            translated_msg['api_version'] = "2.0"

        elif source_version == "2.0" and target_version == "1.0":
            logger.debug("Translating from 2.0 to 1.0")
            # Example: rename 'center_freq' to 'fc'
            if translated_msg['api_call'].get("property") == PROPERTY_CENTER_FREQ:
                logger.debug("Renaming property 'center_freq' to 'fc'")
                translated_msg['api_call']["property"] = "fc"

            translated_msg['api_version'] = "1.0"
        
        else:
            raise XAPIUnsupportedVersion(f"Translation from version {source_version} to {target_version} not supported")

        # Add more translation rules as necessary

        return translated_msg

def main():
    
    # Convert the message dictionary to a byte array 
    send_msg = APIMessage()

    api_call = {
        "msg_type":     "req",
        "action_code":  "set",
        "property":     "debug",
        "value":        "on",
    }

    send_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(timezone.utc),
        from_system="tm",
        to_system="sdp",
        api_call=api_call
    )

    api = TM_SDP()
    api.validate(send_msg.get_json_api_header())

    sent_data = send_msg.to_data()

    print("Sent message")
    print(send_msg)

    # Simulate sending the data over a network
    received_data = sent_data

    # Convert the received data back to a dictionary
    receive_msg = APIMessage()
    receive_msg.from_data(received_data)

    print("Received message")
    print(receive_msg)

    api_call = api.translate(receive_msg.get_json_api_header(), target_version="2.0")
    api.validate(api_call)

    print("Translated message to v2.0")
    print(json.dumps(api_call, indent=4))

if __name__ == "__main__":
    main()
