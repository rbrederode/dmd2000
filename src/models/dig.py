# -*- coding: utf-8 -*-

import ast
import enum
import json
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.app import AppModel
from models.base import BaseModel
from models.dsh import Feed
from models.health import HealthState
from models.comms import CommunicationStatus
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

class LoadState(BaseModel):
    """A class representing the load status of a digitiser. The load state is used to control the load relay switch on the digitiser needed for calibration purposes.
        The load state can be controlled by the Telescope Manager to switch a relay on/off via a specified GPIO pin to apply a load resister in the signal chain.
    """

    schema = Schema({
        "_type": And(str, lambda v: v == "LoadState"),
        "load": And(bool, lambda v: isinstance(v, bool)),
        "gpio_pin": Or(None, And(int, lambda v: 0 <= v <= 27)), # None disables GPIO load relay control
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "LoadState",
            "load": False,
            "gpio_pin": 18,  # Default GPIO pin for load control
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class DigitiserModel(BaseModel):
    """A class representing a digitiser application. The digitiser application is deployed at the telescope to digitise the analog RF signals.
        The digitiser is controlled by the Telescope Manager.    
        The digitiser streams digitised RF data to the SDP for processing.
    """

    schema = Schema({
        "_type": And(str, lambda v: v == "DigitiserModel"),
        "dig_id": And(str, lambda v: isinstance(v, str)),
        "app": And(AppModel, lambda v: isinstance(v, AppModel)),
        "load_state": And(LoadState, lambda v: isinstance(v, LoadState)),
        "gain": And(float, lambda v: 0 <= v <= 100.0),
        "sample_rate": And(float, lambda v: v >= 0.0),
        "bandwidth": And(float, lambda v: v >= 0.0),
        "center_freq": And(float, lambda v: v >= 0.0),
        "freq_correction": And(int, lambda v: -1000 <= v <= 1000),
        "channels": And(int, lambda v: v >= 0),                      # Digitiser property of interest to the Science Data Processor
        "scan_duration": And(int, lambda v: v >= 0),                 # Digitiser property of interest to the Science Data Processor (in seconds)
        "tm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "sdp_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "sdr_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "scanning": And(Or(bool, str, dict, int), lambda v: isinstance(v, bool) or isinstance(v, str) or isinstance(v, dict) or isinstance(v, int)),
        "sdr_eeprom": And(dict, lambda v: isinstance(v, dict)),
        "last_err_msg": Or(None, And(str, lambda v: isinstance(v, str))),                        # Last error message from the app
        "last_err_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),               # Last error datetime from the app
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "DigitiserModel",
            "dig_id": "<undefined>",
            "app": AppModel(
                app_name="dig",
                app_running=False,
                num_processors=0,
                queue_size=0,
                interfaces=[],
                processors=[],
                health=HealthState.UNKNOWN,
                last_update=datetime.now(timezone.utc),
            ),
            "load_state": LoadState(
                load=False,
                gpio_pin=18,
                last_update=datetime.now(timezone.utc),
            ),
            "gain": 0.0,
            "sample_rate": 0.0,
            "bandwidth": 0.0,
            "center_freq": 0.0,
            "freq_correction": 0,
            "channels": 0,
            "scan_duration": 0,
            "scanning": False,
            "tm_connected": CommunicationStatus.NOT_ESTABLISHED,
            "sdp_connected": CommunicationStatus.NOT_ESTABLISHED,
            "sdr_connected": CommunicationStatus.NOT_ESTABLISHED,
            "sdr_eeprom": {},
            "last_err_msg": None,
            "last_err_dt": None,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class DigitiserList(BaseModel):
    """A class representing a list of digitisers."""

    schema = Schema({
        "_type": And(str, lambda v: v == "DigitiserList"),
        "list_id": And(str, lambda v: isinstance(v, str)),                  # Digitiser List identifier e.g. "active"         
        "dig_list": And(list, lambda v: isinstance(v, list)),               # List of DigitiserModel objects
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "DigitiserList",
            "list_id": "<undefined>",
            "dig_list": [],
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def get_dig_by_id(self, dig_id: str) -> DigitiserModel:
        """ Retrieve a DigitiserModel from the dig_list by its dig_id.
        """
        for dig in self.dig_list:
            if dig.dig_id == dig_id:
                return dig
        return None

    def get_dig_by_obs_id(self, obs_id: str) -> DigitiserModel:
        """ Retrieve a DigitiserModel object from the dig_list that is currently scanning for a given obs_id.
        """
        for dig in self.dig_list:
            if isinstance(dig.scanning, dict) and dig.scanning.get("obs_id") == obs_id:
                return dig
        return None

if __name__ == "__main__":
    
    dig001 = DigitiserModel(
        dig_id="dig001",
        app=AppModel(
            app_name="dig",
            app_running=False,
            num_processors=4,
            queue_size=0,
            interfaces=[],
            processors=[],
            health=HealthState.UNKNOWN,
            last_update=datetime.now()
        ),
        load_state=LoadState(
            load=False,
            gpio_pin=17,
            last_update=datetime.now(timezone.utc),
        ),
        gain=0.0,
        sample_rate=0.0,
        bandwidth=0.0,
        center_freq=0.0,
        freq_correction=0,
        channels=0,
        scan_duration=0,
        scanning={"obs_id": "obs001", "tgt_index": 1, "freq_scan": 5},
        tm_connected=CommunicationStatus.NOT_ESTABLISHED,
        sdp_connected=CommunicationStatus.NOT_ESTABLISHED,
        sdr_connected=CommunicationStatus.NOT_ESTABLISHED,
        sdr_eeprom={},
        last_update=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    )

    dig002 = DigitiserModel(dig_id="dig002", scanning={"obs_id": "obs003", "tgt_index": 5, "freq_scan": 2},)

    import pprint
    print("="*40)
    print("Digitiser 001")
    print("="*40)
    pprint.pprint(dig001.to_dict())
    print("="*40)
    print("Digitiser 002")
    print("="*40)
    pprint.pprint(dig002.to_dict())

    dig001 = DigitiserModel.from_dict(dig002.to_dict())
    print("="*40)
    print("Digitiser 001 after from_dict with Digitiser 002 data")
    print("="*40)
    pprint.pprint(dig001.to_dict())

    dig_json = """
        {'_type': 'DigitiserModel',
            'app': {'_type': 'AppModel',
                    'app_name': 'dig',
                    'app_running': True,
                    'arguments': None,
                    'health': {'_type': 'enum.IntEnum',
                                'instance': 'HealthState',
                                'value': 'UNKNOWN'},
                    'interfaces': ['tm', 'sdp'],
                    'last_update': {'_type': 'datetime',
                                    'value': '2025-12-16T15:10:34.004551'},
                    'msg_timeout_ms': 10000,
                    'num_processors': 2,
                    'processors': [],
                    'queue_size': 0},
            'bandwidth': 200000.0,
            'center_freq': 1420000000.0,
            'freq_correction': 0,
            'gain': 0.0,
            'dig_id': 'dig001',
            'last_update': {'_type': 'datetime', 'value': '2025-11-01T12:00:00+00:00'},
            'sample_rate': 2400000.0,
            'sdp_connected': {'_type': 'enum.IntEnum',
                            'instance': 'CommunicationStatus',
                            'value': 'NOT_ESTABLISHED'},
            'sdr_connected': {'_type': 'enum.IntEnum',
                            'instance': 'CommunicationStatus',
                            'value': 'NOT_ESTABLISHED'},
            'sdr_eeprom': {},
            'scanning': False,
            'tm_connected': {'_type': 'enum.IntEnum',
                            'instance': 'CommunicationStatus',
                            'value': 'NOT_ESTABLISHED'}}
"""
    
    print("="*40)
    print("Digitiser 003 from JSON string")
    print("="*40)
    print(dig_json)
    print("="*40)

    dig003 = DigitiserModel(dig_id="dig003")

    # Convert Python dict literal string to dictionary (strip indentation first)
    dig_json_dict = ast.literal_eval(dig_json.strip())

    dig003 = DigitiserModel.from_dict(dig_json_dict)
    
    print("="*40)
    print("Digitiser 003 after from_dict")
    print("="*40)
    pprint.pprint(dig003.to_dict())

    print("="*40)
    print("Digitiser List Model")
    print("="*40)
    diglist001 = DigitiserList(
        list_id="diglist001",
        dig_list=[dig001, dig002],
        last_update=datetime.now(timezone.utc)
    )
    pprint.pprint(diglist001.to_dict())

    # Retrieve digitiser "dig002" from the digitiser list
    dig_retrieved = next((dig for dig in diglist001.dig_list if dig.dig_id == "dig002"), None)
    print("="*40)
    print("Retrieved Digitiser dig002 from Digitiser List")
    print("="*40)
    pprint.pprint(dig_retrieved.to_dict() if dig_retrieved else "Digitiser not found")

    print("="*40)
    print("Save Digitiser List to disk as JSON")
    print("="*40)

    diglist001.save_to_disk(filename="model_diglist.json")

    print("="*40)
    print("Delete and then Load Digitiser List from disk as JSON")
    print("="*40)   
    del diglist001
    diglist001 = DigitiserList().load_from_disk(filename="model_diglist.json")
    pprint.pprint(diglist001.to_dict())

    default_dig001 = DigitiserModel(dig_id="dig001",
        app=AppModel(
            arguments={"local_host": "192.168.0.48"},
        ))

    default_dig002 = DigitiserModel(dig_id="dig002",
        app=AppModel(
            arguments={"local_host": "192.168.0.2"},
        ))

    default_diglist = DigitiserList(
        list_id="default",
        dig_list=[default_dig001, default_dig002],
    )

    default_diglist.save_to_disk(output_dir="./config/default")
