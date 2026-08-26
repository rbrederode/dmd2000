# Message

import io
import json
import sys
import struct
import datetime
import pytest

from util.util import find_json_object_end
from util.xbase import XStreamUnableToExtract, XStreamUnableToEncode

import logging
logger = logging.getLogger(__name__)

class Message:

    ENCODING = 'utf-8'  # Encoding used for JSON headers and content

    def __init__(self):
        """
        Initializes the message instance.
        """

        # The message body is a byte array containing the data stream representation.
        self.msg_data = bytearray()
        self.msg_length = 0  

    def from_data(self, data):
        """
        Converts a byte array to a message.
        Parameters
        msg     An array of bytes containing the data stream representation.
        Returns the offset of the first byte in msg that does not form part of this message.
        """

        # Check if the data is a bytes or bytearray object
        if not isinstance(data, (bytes, bytearray)):
            raise XStreamUnableToExtract("Message: data must be a bytes or bytearray object.")

        self.msg_length = len(data)
        self.msg_data = data

        if self.msg_length == 0:
            raise XStreamUnableToExtract("Message: Data cannot be empty.")

        return 0 # Base class does not unpack any data into fields, so return a zero offset.

    def to_data(self):
        """
        Pack this message instance into its data stream representation.
        Returns an array of bytes containing the data stream representation.
        """
        return self.msg_data

    def __str__(self):
        """
        Returns a human-readable string representation of this event.
        """

        if self.msg_data is None:
            return ""
        else:
            display_length = min(self.msg_length, 1024)
            display_data = self.msg_data[0:display_length]

        lines = []
        line_length = 30
        for i in range(0, display_length, line_length):
            chunk = display_data[i:i+line_length]
            hex_part = ' '.join(f"{b:02x}" for b in chunk)
            # Pad hex_part to always align (30 bytes * 3 chars per byte minus 1 for last space)
            hex_part = hex_part.ljust((line_length * 3) - 1)
            ascii_part = chunk.decode('ascii', errors='replace').replace('�', '.').replace('?', '.')
            # Pad ascii_part to 30 chars for alignment
            ascii_part = ascii_part.ljust(line_length)
            lines.append(f"{hex_part}  {ascii_part}")

        return (
            f"Data (hex)"+" "*(line_length*3+1-len("Data (hex)"))+"Data (ascii)\n" +
            '\n'.join(lines) +
            f"\nDisplaying {display_length} of {self.msg_length} bytes in the msg\n"
        )

class AppMessage(Message):
    """ A class representing an application message, containing a JSON header
        "byteorder":            big or little
        "content-type":         media type of the content e.g. text/json
        "content-encoding":     encoding used for the content e.g. utf-8
        "content-length":       length of the content in bytes 
    Inherits from the Message class.
    """

    def __init__(self):
        """
        Initializes the application message instance.
        """
        super().__init__()
        self.json_header_length = None        # The length of the JSON header.
        self.json_header_bytes = bytearray()  # The JSON header is a byte array containing its data stream representation
        self.json_header_dict = None          # The JSON header is a dictionary containing its dictionary representation.

        self.content_bytes = bytearray()      # The application data is a byte array containing the data stream representation.

    def _json_encode(self, obj, encoding):
        """
        Encodes a Python object into a JSON byte array.
        """
        return json.dumps(obj, ensure_ascii=False).encode(encoding)

    def _json_decode(self, json_bytes, encoding):
        """
        Decodes a JSON byte array into a Python object.
        """
        json_str = json_bytes.decode(encoding)
        decoder = json.JSONDecoder()
        obj, index = decoder.raw_decode(json_str)
        return obj

    def set_json_header(self, content_type, content_encoding, content_bytes):
        """
        Sets the json header data.
        """
        # Check if the content_bytes is a bytes or bytearray object
        if not isinstance(content_bytes, (bytes, bytearray)):
            raise XStreamUnableToEncode("AppMessage: content_bytes must be a bytes or bytearray object.")
       
        self.json_header_dict = {
            "byteorder": sys.byteorder,
            "content-type": content_type,
            "content-encoding": content_encoding,
            "content-length": len(content_bytes)
        }
        self.content_bytes = content_bytes

    def get_json_header(self):
        """
        Returns the JSON header as a dictionary.
        """
        return self.json_header_dict

    def from_data(self, data):
        """
        Converts a byte array to an application message, by decoding the JSON header
        """

        # Call the parent class to handle the initial message processing
        # and retrieve the offset in the data where its processing ends.
        offset = super().from_data(data)

        # Peek ahead to find the first complete JSON object in the data stream
        idx = find_json_object_end(data[offset:])
        if idx == -1:
            raise XStreamUnableToExtract("AppMessage: Invalid data, JSON header not found.")

        self.json_header_length = idx
        self.json_header_bytes = data[offset:offset + self.json_header_length]  

        self.json_header_dict = self._json_decode(self.json_header_bytes, self.ENCODING)

        # Check if the JSON header contains the required json key value pairs
        for key in ("byteorder", "content-length", "content-type", "content-encoding"):
            if key not in self.json_header_dict:
                raise XStreamUnableToExtract(f"AppMessage: Missing required key in JSON header '{key}'.")

        self.content_bytes = data[self.json_header_length:]

        offset += self.json_header_length
        return offset

    def to_data(self):
        """
        Pack this application message instance into its data stream representation.
        """

        # If there is no JSON header, then let the base class handle the packing
        if self.json_header_dict is None or self.json_header_length == 0:
            return super().to_data()

        self.json_header_bytes = self._json_encode(self.json_header_dict, self.ENCODING)
        self.json_header_length = len(self.json_header_bytes)

        self.msg_data = self.json_header_bytes + self.content_bytes
        self.msg_length = len(self.msg_data)
        
        return self.msg_data

    def __str__(self):
        """
        Returns a human-readable string representation of the app message
        """

        if self.json_header_dict is None:
            return super().__str__() + \
                    "App header: None\n"
        else:
            return super().__str__() + \
                   f"App header (length={self.json_header_length}): {json.dumps(self.json_header_dict, indent=4)}\n\n"

class APIMessage(AppMessage):
    """ 
    A class representing an API message, containing a JSON dictionary consisting of:
        "api_version":          major.minor version of the API e.g. 1.0
        "timestamp":            YYYY-MM-DDTHH:MM:SS.sssZ formatted timestamp (UTC) of when the message was transmitted 
        "from":                 system originating the api call e.g. cam
        "to":                   system intended to receive the api call e.g. sdp
        "entity":               optional entity ID to uniquely identify an instance of an entity e.g. Dishes: 'dsh001', Digitisers: 'dig001'
        "api_call":             json formatted api call request or response
        "echo_data":            optional json formatted data that will simply be echoed back in a subsequent response
        "payload_length":       conditional if payload is present, integer length of payload data in bytes 
    A Payload byte array (if present) is appended after the JSON API header.
    Inherits from the AppMessage class.
    """

    def __init__(self, api_msg: dict=None, api_version: str=None, payload: bytearray=None):
        """
        Initializes the API message instance.
            :param api_msg: If provided, initializes a APIMessage based on the api_msg dictionary.
            :param api_version: If provided, sets the api_version field in the json API header
            :param payload: If provided, sets the payload data in the APIMessage
        """
        super().__init__()
        self.json_api_header_length = 0             # The length of the JSON API header.
        self.json_api_header_bytes = bytearray()    # The JSON API header is a byte array containing its data stream representation
        self.json_api_header_dict = None            # The JSON API header is a dictionary containing a dictionary representation.

        self.payload_data = bytearray()             # The payload data is a byte array containing the data stream representation.
        self.payload_length = 0                     # The length of the payload data in bytes.

        # If a request message is provided, then initialise a response message with an empty api_call
        if api_msg is not None and isinstance(api_msg, dict):
            self.set_json_api_header(
                api_version=api_msg.get("api_version"),
                dt=datetime.datetime.fromisoformat(api_msg.get("timestamp").replace("Z", "+00:00")),
                from_system=api_msg.get("from"),      
                to_system=api_msg.get("to"),
                entity=api_msg.get("entity"),
                api_call=api_msg.get("api_call"),
                echo=api_msg.get("echo_data")
            )

        if api_version is not None:
            self.set_api_version(api_version)

        if payload is not None:
            self.set_payload_data(payload)

    def set_json_api_header(self, api_version: str, dt: datetime, from_system: str, to_system: str, api_call: dict=None, echo: dict=None, entity: str=None):
        """
        Sets the json API header data.
        """
        self.set_api_version(api_version)
        self.set_from(from_system)
        self.set_to(to_system)
        
        if entity is not None:
            self.set_entity(entity)

        self.set_timestamp(dt)
        self.set_api_call(api_call)
        self.set_echo_data(echo)
        self.set_payload_length(self.payload_length)

    def set_api_version(self, api_version: str):
        """
        Sets the api_version field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

        self.json_api_header_dict["api_version"] = api_version 

    def set_timestamp(self, dt: datetime):
        """
        Sets the timestamp field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

         # Check if dt is a datetime object
        if not isinstance(dt, datetime.datetime):
            raise XStreamUnableToEncode("APIMessage: dt must be a datetime object.")
        
        # Ensure dt is in UTC timezone
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            raise XStreamUnableToEncode("APIMessage: dt must be timezone-aware and in UTC timezone.")

        self.json_api_header_dict["timestamp"] = dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    def set_from(self, from_system: str):
        """
        Sets the from field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

        self.json_api_header_dict["from"] = from_system 

    def set_to(self, to_system: str):
        """
        Sets the to field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

        self.json_api_header_dict["to"] = to_system

    def set_entity(self, entity: str):
        """
        Sets the entity field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

        self.json_api_header_dict["entity"] = entity

    def switch_from_to(self):
        """
        Switches the from and to fields in the json API header data.
        """
        if self.json_api_header_dict is None:
            return

        from_system = self.json_api_header_dict.get("from", None)
        to_system = self.json_api_header_dict.get("to", None)

        self.json_api_header_dict["from"] = to_system 
        self.json_api_header_dict["to"] = from_system 

    def set_api_call(self, api_call: dict):
        """
        Sets the api_call field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

        self.json_api_header_dict["api_call"] = api_call if api_call is not None else {}

    def set_echo_data(self, echo: dict=None):
        """
        Sets the echo_data field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

        if echo is not None:
            if not isinstance(echo, dict):
                raise XStreamUnableToEncode("APIMessage: echo must be a dictionary.")
            self.json_api_header_dict["echo_data"] = echo
        else:
            if "echo_data" in self.json_api_header_dict:
                del self.json_api_header_dict["echo_data"]

    def set_payload_data(self, payload: bytearray=None):
        """
        Sets the payload_data field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

        self.payload_data = bytearray() if payload is None else payload   
        self.payload_length = 0 if payload is None else len(payload)      
        self.set_payload_length(self.payload_length)

    def set_payload_length(self, length: int):
        """
        Sets the payload_length field in the json API header data.
        """
        if self.json_api_header_dict is None:
            self.json_api_header_dict = {}

        if not isinstance(length, int) or length < 0:
            raise XStreamUnableToEncode("APIMessage: payload_length must be a non-negative integer.")

        self.json_api_header_dict["payload_length"] = length

    def get_json_api_header(self):
        """
        Returns the JSON API header as a dictionary.
        """
        return self.json_api_header_dict

    def get_api_version(self):
        """
        Returns the api_version field from the JSON API header as a string.
        """
        if self.json_api_header_dict is None:
            return None

        if "api_version" in self.json_api_header_dict:
            return self.json_api_header_dict["api_version"]

        return None

    def get_timestamp(self):
        """
        Returns the timestamp field from the JSON API header as a string.
        """
        if self.json_api_header_dict is None:
            return None

        if "timestamp" in self.json_api_header_dict:
            return self.json_api_header_dict["timestamp"]

        return None

    def get_to_system(self):
        """
        Returns the to field from the JSON API header as a string.
        """
        if self.json_api_header_dict is None:
            return None

        if "to" in self.json_api_header_dict:
            return self.json_api_header_dict["to"]

        return None

    def get_from_system(self):
        """
        Returns the from field from the JSON API header as a string.
        """
        if self.json_api_header_dict is None:
            return None

        if "from" in self.json_api_header_dict:
            return self.json_api_header_dict["from"]

        return None

    def get_entity(self):
        """
        Returns the entity field from the JSON API header as a string.
        """
        if self.json_api_header_dict is None:
            return None

        if "entity" in self.json_api_header_dict:
            return self.json_api_header_dict["entity"]

        return None

    def get_api_call(self):
        """
        Returns the api_call field from the JSON API header as a dictionary.
        """
        if self.json_api_header_dict is None:
            return None

        if "api_call" in self.json_api_header_dict:
            return self.json_api_header_dict["api_call"]

        return None

    def get_echo_data(self):
        """
        Returns the echo_data field from the JSON API header as a dictionary.
        """
        if self.json_api_header_dict is None:
            return None

        if "echo_data" in self.json_api_header_dict:
            return self.json_api_header_dict["echo_data"]

        return None

    def get_payload_data(self):
        """
        Returns the payload_data field as a byte array.
        """
        return self.payload_data

    def add_echo_api_header(self):
        """
        Appends the current API header information to the echo_data field.
        """
        if self.json_api_header_dict is None:
            raise XStreamUnableToEncode("APIMessage: Cannot echo api header, json_api_header_dict is None.")

        echo = {
            "client": "APIMessage",
            "api_version": self.get_api_version(),
            "timestamp": self.get_timestamp(),
            "from": self.get_from_system(),
            "to": self.get_to_system(),
            "entity": self.get_entity(),
            "echo_data": self.get_echo_data()
        }

        self.set_echo_data(echo)

    def get_echo_api_header(self):
        """
        Returns the echo_data field from the JSON API header as a dictionary.
        If the echo_data field contains an API header, then this is returned, else None is returned.
        """
        if self.json_api_header_dict is None:
            return None

        echo = self.get_echo_data()
        if echo is not None and isinstance(echo, dict):
            if echo.get("client") == "APIMessage":
                return echo

        return None

    def remove_echo_api_header(self):
        """
        Removes the api header information from the echo_data field.
        """
        if self.json_api_header_dict is None:
            raise XStreamUnableToEncode("APIMessage: Cannot remove echo api header, json_api_header_dict is None.")

        echo = self.get_echo_data()
        if echo is not None and isinstance(echo, dict):

            if echo.get("client") == "APIMessage":
                echo_data = echo.get("echo_data")
                self.set_echo_data(echo_data)

    def from_data(self, data):
        """
        Converts a byte array to an API message, by decoding the JSON header
        """

        # Call the parent class to handle the initial message processing
        # and retrieve the offset in the data where its processing ends.
        offset = super().from_data(data)

        # Decode the JSON API header from the data stream starting at the offset
        idx = find_json_object_end(data[offset:])
        if idx == -1:
            raise XStreamUnableToExtract("APIMessage: Invalid data, JSON API header not found.")

        self.json_api_header_dict = self._json_decode(data[offset:offset + idx], self.ENCODING)

        self.json_api_header_length = idx
        self.json_api_header_bytes = data[offset:offset + idx].decode(self.ENCODING)

        # Check if the JSON header contains the required json key value pairs
        for key in ("api_version", "timestamp", "from", "to", "api_call", "payload_length"):
            if key not in self.json_api_header_dict:
                raise XStreamUnableToExtract(f"APIMessage: Missing required key in JSON header '{key}'.")

        self.payload_length = self.json_api_header_dict.get("payload_length", 0)
        if not isinstance(self.payload_length, int) or self.payload_length < 0:
            raise XStreamUnableToExtract("APIMessage: payload_length must be a non-negative integer.")

        self.payload_data = data[offset + idx:offset + idx + self.payload_length]

        offset += self.json_api_header_length + self.payload_length
        return offset

    def to_data(self):
        """
        Pack this API message instance into its data stream representation.
        """
        # If there is no JSON header, then let the base class handle the packing
        if self.json_api_header_dict is None:
            return super().to_data()

        self.json_api_header_bytes = self._json_encode(self.json_api_header_dict, self.ENCODING)
        self.json_api_header_length = len(self.json_api_header_bytes)
        
        self.set_json_header(
            content_type="application/json", 
            content_encoding=self.ENCODING, 
            content_bytes=self.json_api_header_bytes + self.payload_data)

        return super().to_data()

    def __str__(self):
        """
        Returns a human-readable string representation of the api message
        """

        if self.json_api_header_dict is None:
            return super().__str__() + \
                     "API header: None\n"
        else:
            return super().__str__() + \
                   f"API header (length={self.json_api_header_length}): {json.dumps(self.json_api_header_dict, indent=4)}\n\n"

# --- Pytest test functions below ---

def test_message_pack_unpack():
    msg = Message()
    data = "Hello, Wörld!".encode(msg.ENCODING)
    offset = msg.from_data(data)
    assert offset == 0
    assert msg.msg_data == data
    assert msg.msg_length == len(data)
    packed_data = msg.to_data()
    assert packed_data == data

def test_app_message_pack_unpack():
    test_payload = '''{
        "name": "Frieda",
        "is_dog": true,
        "hobbies": ["eating", "sleeping", "barking"],
        "age": 8,
        "address": {"work": null, "home": ["Berlin", "Germany"]},
        "friends": [
            {"name": "Philipp", "hobbies": ["eating", "sleeping", "reading"]},
            {"name": "Mitch", "hobbies": ["running", "snacking"]}
        ]
    }'''
    app_msg = AppMessage()
    app_msg.set_json_header(
        content_type="text/json",
        content_encoding=app_msg.ENCODING,
        content_bytes=test_payload.encode(app_msg.ENCODING)
    )
    packed = app_msg.to_data()
    assert isinstance(packed, (bytes, bytearray))
    assert app_msg.get_json_header()["content-type"] == "text/json"


    # Unpack
    data = b'{"byteorder": "big", "content-type": "text/json", "content-encoding": "utf-8", "content-length": 14}{Hello, Server!}'
    app_msg.from_data(data)
    assert app_msg.json_header_dict["byteorder"] == "big"
    assert app_msg.json_header_dict["content-type"] == "text/json"
    assert app_msg.content_bytes == b'{Hello, Server!}'

def test_api_message_pack_unpack():
    api_msg = APIMessage()
    api_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(datetime.timezone.utc),
        from_system="cam",
        to_system="dig",
        api_call={"action": "get", "property": "frequency"}
    )
    packed = api_msg.to_data()
    assert isinstance(packed, (bytes, bytearray))
    assert api_msg.get_json_api_header()["api_version"] == "1.0"

    # Unpack
    data = b'{"byteorder": "little", "content-type": "application/json", "content-encoding": "utf-8", "content-length": 316}{"payload_length": 1, "api_version": "1.0", "timestamp": "2025-09-15T15:35:39.610Z", "from": "cam", "to": "dig", "api_call": {"action": "get", "property": "frequency"}}Z{"api_version": "2.2", "timestamp": "2025-09-30T15:35:39.610Z", "from": "sdp", "to": "cam", "api_call": {"action": "set", "property": "frequency"}}'
    offset = api_msg.from_data(data)
    assert offset > 0
    assert api_msg.get_api_version() == "1.0"
    assert isinstance(api_msg.get_payload_data(), (bytes, bytearray))

def test_api_message_with_payload():
    payload = b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x88'
    api_msg_with_payload = APIMessage(api_version="1.0", payload=payload)
    api_msg_with_payload.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(datetime.timezone.utc),
        from_system="cam",
        to_system="dig",
        entity="dig001",
        api_call={"action": "set", "property": "data"},
        echo={"request_id": "67890", "note": "This message contains a payload"}
    )
    packed = api_msg_with_payload.to_data()
    assert isinstance(packed, (bytes, bytearray))
    assert api_msg_with_payload.get_payload_data() == payload

    # Unpack
    api_msg_with_payload.from_data(packed)
    assert api_msg_with_payload.get_payload_data() == payload

def test_api_message_with_echo_data():
    api_echo_msg = APIMessage()
    api_echo_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(datetime.timezone.utc),
        from_system="cam",
        to_system="dig",
        entity="dsh101",
        api_call={"action": "get", "property": "frequency"},
        echo={"request_id": "12345", "note": "This is a test echo"}
    )
    packed = api_echo_msg.to_data()
    assert isinstance(packed, (bytes, bytearray))
    assert api_echo_msg.get_echo_data() == {"request_id": "12345", "note": "This is a test echo"}

    # Unpack
    data = b'{"byteorder": "little", "content-type": "application/json", "content-encoding": "utf-8", "content-length": 394}{"payload_length": 0, "api_version": "1.0", "timestamp": "2025-09-15T15:35:39.610Z", "from": "cam", "to": "dig", "entity": "dsh101", "api_call": {"action": "get", "property": "frequency"}, "echo_data": {"request_id": "12345", "note": "This is a test echo"}}{"api_version": "2.2", "timestamp": "2025-09-30T15:35:39.610Z", "from": "sdp", "to": "cam", "api_call": {"action": "set", "property": "frequency"}}'
    offset = api_echo_msg.from_data(data)
    assert offset > 0
    assert isinstance(api_echo_msg.get_echo_data(), dict)
    assert api_echo_msg.get_echo_data() == {"request_id": "12345", "note": "This is a test echo"}


if __name__ == "__main__":

    # Example usage of Message
    msg = Message()
    data = "Hello, Wörld!".encode(msg.ENCODING)

    print('-'*50)
    print(f"Test unpacking a Message data stream")
    print('-'*50)
    msg.from_data(data)
    print(f"Unpacked Message: {msg}")

    print('-'*50)
    print("Test packing a Message data stream")
    print('-'*50)
    packed_data = msg.to_data()
    print(f"Packed Message: {packed_data}")

    test_payload = '''{
        "name": "Frieda",
        "is_dog": true,
        "hobbies": [
            "eating",
            "sleeping",
            "barking"
        ],
        "age": 8,
        "address": {
            "work": null,
            "home": [
            "Berlin",
            "Germany"
            ]
        },
        "friends": [
            {
            "name": "Philipp",
            "hobbies": [
                "eating",
                "sleeping",
                "reading"
            ]
            },
            {
            "name": "Mitch",
            "hobbies": [
                "running",
                "snacking"
            ]
            }
        ]
        }'''

    # Example usage of AppMessage
    app_msg = AppMessage()

    print('-'*50)
    print(f"Test packing an AppMessage data stream")
    print('-'*50)
    # Set the JSON header
    app_msg.set_json_header(
        content_type="text/json",
        content_encoding=app_msg.ENCODING,
        content_bytes=test_payload.encode(app_msg.ENCODING)
    )
    
    print(f"App message to_data {app_msg.to_data()}")
    print(f"App message json_header_dict: {app_msg.get_json_header()}")

    print('-'*50)
    print(f"Test unpacking an app message")    
    print('-'*50)
    data = b'{"byteorder": "big", "content-type": "text/json", "content-encoding": "utf-8", "content-length": 14}{Hello, Server!}'
    app_msg.from_data(data)
    print(f"JSON header: {app_msg.json_header_dict}")
    print(f"JSON header length: {app_msg.json_header_length}")
    print(f"JSON header bytes: {app_msg.json_header_bytes}")

    print('-'*50)
    print("Testing string representation of a message")
    print('-'*50)
    print(f"Message: {msg}")
    print(f"AppMessage: {app_msg}")

    # Example usage of APIMessage
    api_msg = APIMessage()

    print('-'*50)
    print(f"Test packing an API Message data stream")
    print('-'*50)

    api_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(datetime.timezone.utc),
        from_system="cam",
        to_system="dig", 
        entity="dsh101",
        api_call={"action": "get", "property": "frequency"}
    )

    print(f"API message to_data {api_msg.to_data()}")
    print(f"API message json_api_header_dict: {api_msg.get_json_api_header()}")

    print('-'*50)
    print(f"Test unpacking an api message")    
    print('-'*50)
    data = b'{"byteorder": "little", "content-type": "application/json", "content-encoding": "utf-8", "content-length": 316}{"payload_length": 1, "api_version": "1.0", "timestamp": "2025-09-15T15:35:39.610Z", "from": "cam", "to": "dig", "entity": "dsh101", "api_call": {"action": "get", "property": "frequency"}}Z{"api_version": "2.2", "timestamp": "2025-09-30T15:35:39.610Z", "from": "sdp", "to": "cam", "api_call": {"action": "set", "property": "frequency"}}'
    offset = api_msg.from_data(data)
    print(f"{api_msg}")
    print(f"Offset after unpacking: {offset}")
    print(f"Payload data: {api_msg.get_payload_data()}")

     # Example usage of APIMessage with echo data
    api_echo_msg = APIMessage()

    print('-'*50)
    print(f"Test packing an API Message with echo data stream")
    print('-'*50)

    api_echo_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(datetime.timezone.utc),
        from_system="cam",
        to_system="dig", 
        entity="dig001",
        api_call={"action": "get", "property": "frequency"},
        echo={"request_id": "12345", "note": "This is a test echo"}
    )

    print(f"API message to_data {api_echo_msg.to_data()}")
    print(f"API message json_api_header_dict: {api_echo_msg.get_json_api_header()}")

    print('-'*50)
    print(f"Test unpacking an api message with echo data")    
    print('-'*50)

    api_echo_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(datetime.timezone.utc),
        from_system="cam",
        to_system="dig", 
        api_call={"action": "get", "property": "frequency"}
    )

    print(f"API message to_data {api_echo_msg.to_data()}")
    print(f"API message json_api_header_dict: {api_echo_msg.get_json_api_header()}")

    print('-'*50)
    print(f"Test unpacking an api message with echo data")    
    print('-'*50)
    data = b'{"byteorder": "little", "content-type": "application/json", "content-encoding": "utf-8", "content-length": 394}{"payload_length": 0, "api_version": "1.0", "timestamp": "2025-09-15T15:35:39.610Z", "from": "cam", "to": "dig", "api_call": {"action": "get", "property": "frequency"}, "echo_data": {"request_id": "12345", "note": "This is a test echo"}}{"api_version": "2.2", "timestamp": "2025-09-30T15:35:39.610Z", "from": "sdp", "to": "cam", "api_call": {"action": "set", "property": "frequency"}}'
    offset = api_echo_msg.from_data(data)
    print(f"{api_echo_msg}")

    print('-'*50)
    print("Testing initialising an APIMessage from a request message")
    print('-'*50)

    api_req_msg = APIMessage()
    api_req_msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(datetime.timezone.utc),
        from_system="cam",
        to_system="dig", 
        api_call={"action": "get", "property": "frequency"},
        echo={"request_id": "12345", "note": "This is a test echo"}
    )

    print(f"API request message")
    print(api_req_msg)

    api_resp_msg = APIMessage(api_req_msg)
    print(f"API response message initialised from request message")
    print(api_resp_msg)

    print('-'*50)
    print("Testing packing an APIMessage containing a payload")
    print('-'*50)

    payload = b'\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x88'
    api_msg_with_payload = APIMessage(api_version="1.0", payload=payload)
    api_msg_with_payload.set_json_api_header(
        api_version="1.0",
        dt=datetime.datetime.now(datetime.timezone.utc),
        from_system="cam",
        to_system="dig", 
        api_call={"action": "set", "property": "data"},
        echo={"request_id": "67890", "note": "This message contains a payload"}
    )
    print(f"API message with payload")
    print(api_msg_with_payload)

    print(f"API message with payload to_data {api_msg_with_payload.to_data()}")

    received_data = api_msg_with_payload.to_data()
    print('-'*50)
    print("Testing unpacking an APIMessage containing a payload")
    print('-'*50)

    api_msg_with_payload.from_data(received_data)
    print(f"API message with payload after unpacking")
    print(api_msg_with_payload)
    print(f"Payload data: {api_msg_with_payload.get_payload_data()}")
