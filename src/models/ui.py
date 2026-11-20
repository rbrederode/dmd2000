import enum
import logging
from pathlib import Path
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from util.xbase import XAPIValidationFailed

logger = logging.getLogger(__name__)

class UIDriverType(enum.IntEnum):
    GSHEETS = 1         # Google Sheets
    EXCEL = 2           # M365 Excel
    REST_API = 3        # REST API
    CUSTOM_UI = 4       # Custom UI implementation
    UNKNOWN = 5

class UIDriver(BaseModel):
    """A class representing a UI driver for the telescope manager."""

    schema = Schema({
        "_type": And(str, lambda v: v == "UIDriver"),
        "type": And(UIDriverType, lambda v: isinstance(v, UIDriverType)),
        "short_desc": Or(None, And(str, lambda v: isinstance(v, str))),     # Optional short description of the UI driver
        "config": Or(None, lambda v: v is None or isinstance(v, dict)),     # Configuration dictionary for the UI driver
        "instance": Or(None, lambda v: v is None or isinstance(v, object)), # Optional instance of the UI driver (e.g. GoogleSheetsDriver object)
        "poll_period": And(lambda value: type(value) is int, lambda value: value > 0), # Seconds between model pushes
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "UIDriver",
            "type": UIDriverType.UNKNOWN,
            "short_desc": None,
            "config": None,
            "instance": None,   
            "poll_period": 30,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def to_dict(self):
        """Override to exclude the 'instance' field which holds a non-serializable runtime object."""
        return {k: BaseModel._serialise(v) for k, v in self._data.items() if k != "instance"}


class UIDriverList(BaseModel):
    """Profile UI integration definitions, without application/runtime state."""

    schema = Schema({
        '_type': And(str, lambda value: value == 'UIDriverList'),
        'list_id': And(str, lambda value: bool(value.strip())),
        'ui_drivers': And(list, lambda values: all(isinstance(value, UIDriver) for value in values)),
    })

    def __init__(self, **kwargs):
        if set(kwargs) - set(self.schema.schema):
            raise XAPIValidationFailed('UIDriverList accepts only list_id and ui_drivers')
        super().__init__(**({'_type': 'UIDriverList', 'list_id': 'active', 'ui_drivers': []} | kwargs))

    def to_dict(self):
        return {'_type': 'UIDriverList', 'list_id': self.list_id,
                'ui_drivers': [{key: BaseModel._serialise(value) for key, value in driver._data.items()
                                if key not in ('instance', 'last_update')} for driver in self.ui_drivers]}

if __name__ == "__main__":

    import pprint

    gsheets_driver = UIDriver(
        type=UIDriverType.GSHEETS,
        config={
            "sheet_id": "1234567890",
            "tm_range": "TM_UI_API++!A2",
            "dig_range": "TM_UI_API++!D2",
            "dsh_range": "TM_UI_API++!G2",
            "sdp_range": "TM_UI_API++!J2",
            "odt_range": "TM_UI_API++!M2",
            "oda_range": "TM_UI_API++!P2",
        },  
        short_desc="Google Sheets UI Driver",
        poll_period=30,
        last_update=datetime.now(timezone.utc)
    )

    print("="*40)
    print("UIDriver object created:")
    print("="*40)
    pprint.pprint(gsheets_driver.to_dict())
