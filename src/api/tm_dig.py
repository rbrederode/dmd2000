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

API_VERSION = "2.0" # Version of the API implemented in this module.
LEGACY_SUPPORTED_VERSIONS = ["1.0","1.1"] # Requires translator methods to/from API_VERSION

MSG_TYPES = dmd_protocol.MSG_TYPES
STATUS    = dmd_protocol.STATUS

# Allowable api msg actions 
ACTION_CODE_METHOD = "method"
ACTION_CODES = dmd_protocol.ACTION_CODES + (
    ACTION_CODE_METHOD,   # Call a method on the subsystem
)

# Allowable origins (from) and destinations (to) of api msg calls
FROM = (
    dmd_protocol.TM,
    dmd_protocol.DIG,
)

TO = (
    dmd_protocol.DIG,
    dmd_protocol.TM,
)

# Allowable properties to get or set
PROPERTY_LOAD_ACTIVE     = 'load_active'      # Load state flag; optional GPIO control is configured by the bandpass filter config
PROPERTY_CENTER_FREQ     = 'center_freq'      # Center frequency in Hz
PROPERTY_SAMPLE_RATE     = 'sample_rate'      # Sample rate in samples per second
PROPERTY_BANDWIDTH       = 'bandwidth'        # Bandwidth in Hz
PROPERTY_GAIN            = 'gain'             # Gain in dB
PROPERTY_FREQ_CORRECTION = 'freq_correction'  # Frequency correction in ppm
PROPERTY_SCANNING        = 'scanning'         # Scanning status True/False or Observation Id (same as True)
PROPERTY_SDP_COMMS       = 'sdp_comms'        # SDP communication status (established/not established)
PROPERTIES = dmd_protocol.PROPERTIES + (
    PROPERTY_LOAD_ACTIVE,
    PROPERTY_CENTER_FREQ,
    PROPERTY_SAMPLE_RATE,
    PROPERTY_BANDWIDTH,
    PROPERTY_GAIN,
    PROPERTY_FREQ_CORRECTION,
    PROPERTY_SCANNING,
    PROPERTY_SDP_COMMS,
)

# Allowable methods to call on the subsystem 
METHOD_GET_GAINS            = 'get_gains'           # Get a list of available gain settings
METHOD_GET_TUNER_TYPE       = 'get_tuner_type'      # Get the type of tuner in the device
METHOD_SET_DIRECT_SAMPLING  = 'set_direct_sampling' # Set direct sampling mode (0=off, 1=I-ADC, 2=Q-ADC)
METHOD_READ_BYTES           = 'read_bytes'          # Read raw bytes from the device
METHOD_READ_SAMPLES         = 'read_samples'        # Read samples from the device
METHOD_GET_AUTO_GAIN        = 'get_auto_gain'       # Determine optimal gain setting e.g. 20 dB
METHOD_SET_AUTO_GAIN        = 'set_auto_gain'       # Automatically set optimal gain setting e.g. 20 dB
METHOD_GET_GAIN_GAUSSIANITY = 'get_gain_gaussianity' # Run gaussianity test (Shapiro–Wilk) on current gain setting

METHODS = (
    METHOD_GET_GAINS,
    METHOD_GET_TUNER_TYPE,
    METHOD_SET_DIRECT_SAMPLING,
    METHOD_READ_BYTES,
    METHOD_READ_SAMPLES,
    METHOD_GET_AUTO_GAIN,
    METHOD_SET_AUTO_GAIN,
    METHOD_GET_GAIN_GAUSSIANITY,
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
    "value":        {"type": "(int, float, str, dict, bool)"},      # Value to set or value returned
    "method":       {"enum": METHODS},                              # Method name (one of METHODS)
    "params":       {"type": "dict"},                               # Key Value pairs in a dictionary e.g. {"num_samples": 1024}
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
            "method",       # Required if action_code is "method"
            "params"        # Required if action_code is "method"
        },
        "optional": {"obs_data"},  # Optional field
    },
    "adv": {
        "required": {"msg_type", "action_code"},
        "conditional": {
            "property",     # Required if action_code is "get" or "set"
            "value",        # Required if action_code is "set"
            "method",       # Required if action_code is "method"
            "params"        # Required if action_code is "method"
        },
        "optional": {"obs_data"},  # Optional field
    },
    "rsp": {
        "required": {"msg_type", "action_code", "status"},
        "optional": {"message"},
        "optional": {"property"},   # Copied from req/adv
        "optional": {"value"},      
        "optional": {"method"},     # Copied from req/adv
        "optional": {"params"},
        "optional": {"obs_data"},   # Optional field
    },
}

class TM_DIG(API):
    """
    Class responsible for enforcing the Telescope Manager-Digitiser API.

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
            elif field == "method":
                if api_call.get('action_code') == ACTION_CODE_METHOD and 'method' not in api_call:
                    raise XAPIValidationFailed(f"Message of type '{msg_type}' with action_code '{ACTION_CODE_METHOD}' missing required field 'method'")
            elif field == "params":
                if api_call.get('action_code') == ACTION_CODE_METHOD and 'params' not in api_call:
                    raise XAPIValidationFailed(f"Message of type '{msg_type}' with action_code '{ACTION_CODE_METHOD}' missing required field 'params'")

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
        "property":     "fc",
        "value":        1420.40e6
    }

    send_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(timezone.utc),
        from_system="tm",
        to_system="dig001",
        api_call=api_call
    )

    api = TM_DIG()
    api.translate(send_msg.get_json_api_header(), target_version="2.0")
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
