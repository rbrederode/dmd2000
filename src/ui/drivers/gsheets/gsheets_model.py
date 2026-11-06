import enum
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

class GSheetConfig(BaseModel):
    """A class representing the configuration for a Google Sheets driver."""

    schema = Schema({      
        "_type": And(str, lambda v: v == "GSheetConfig"),
        "sheet_id":  And(str, lambda v: isinstance(v, str)),                # Google Sheet ID to write model updates to
        "tm_range":  And(str, lambda v: isinstance(v, str)),                # Range for Telescope Manager model updates e.g. "TM_UI_API!A2"
        "dig_range": And(str, lambda v: isinstance(v, str)),                # Range for Digitiser model updates e.g. "TM_UI_API!D2"
        "dsh_range": And(str, lambda v: isinstance(v, str)),                # Range for Dish Manager model updates e.g. "TM_UI_API!G2"
        "sdp_range": And(str, lambda v: isinstance(v, str)),                # Range for Science Data Processor model updates e.g. "TM_UI_API!J2"
        "odt_range": And(str, lambda v: isinstance(v, str)),                # Range for Observation Design Tool model updates e.g. "TM_UI_API!M2"
        "oda_range": And(str, lambda v: isinstance(v, str)),                # Range for Observation Design Assistant model updates e.g. "TM_UI_API!P2"
        "ws_range":  And(str, lambda v: isinstance(v, str)),                # Range for Weather Station model updates e.g. "TM_UI_API!S2"
        "web_range": And(str, lambda v: isinstance(v, str)),                # Range for Web Server model updates e.g. "TM_UI_API!V2"
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "GSheetConfig",
            "sheet_id": "1r73N0VZHSQC6RjRv94gzY50pTgRvaQWfctGGOMZVpzc",     # Default to ALSTON RADIO TELESCOPE sheet",
            "tm_range":  "TM_UI_API++!A2:B",
            "dig_range": "TM_UI_API++!D2:E",
            "dsh_range": "TM_UI_API++!G2:H",
            "sdp_range": "TM_UI_API++!J2:K",
            "odt_range": "TM_UI_API++!M2:N",
            "oda_range": "TM_UI_API++!P2:Q",
            "ws_range":  "TM_UI_API++!S2:T",
            "web_range": "TM_UI_API++!V2:W",
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

if __name__ == "__main__":

    import pprint

    print("="*40)
    print("GSHEET Model Initialised")
    print("="*40)


    gs_cfg = GSheetConfig(
        sheet_id="1234567890",
        tm_range="TM_UI_API++!A2:B",
        dig_range="TM_UI_API++!D2:E",
        dsh_range="TM_UI_API++!G2:H",
        sdp_range="TM_UI_API++!J2:K",
        odt_range="TM_UI_API++!M2:N",
        oda_range="TM_UI_API++!P2:Q",
        ws_range="TM_UI_API++!S2:T",
        web_range="TM_UI_API++!V2:W",
        last_update=datetime.now(timezone.utc)
    )
    print("GSheetConfig created successfully:", gs_cfg.to_dict())
