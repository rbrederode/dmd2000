import enum
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

class MD01Config(BaseModel):
    """A class representing the configuration for an MD-01 dish driver."""

    schema = Schema({      
        "_type": And(str, lambda v: v == "MD01Config"),

        "host": And(str, lambda v: isinstance(v, str)),                     # Hostname or IP address of the dish controller
        "port": And(int, lambda v: 0 <= v <= 65535),                        # Port number for the dish controller
        "stow_alt": And(float, lambda v: -90.0 <= v <= 90.0),               # Altitude stow position in degrees
        "stow_az": And(float, lambda v: 0.0 <= v <= 360.0),                 # Azimuth stow position in degrees
        "offset_alt": And(float, lambda v: -90.0 <= v <= 90.0),             # Altitude offset in degrees
        "offset_az": And(float, lambda v: -360.0 <= v <= 360.0),            # Azimuth offset in degrees
        "min_alt": And(float, lambda v: -90.0 <= v <= 90.0),                # Minimum allowable altitude in degrees
        "max_alt": And(float, lambda v: -90.0 <= v <= 90.0),                # Maximum allowable altitude in degrees
        "resolution": And(float, lambda v: v >= 0.0),                       # Degrees per step resolution of the dish
        "rotation_speed": And(float, lambda v: v >= 0.0),                   # Rotation speed in degrees per second 
        "rate_limit": And(float, lambda v: v >= 0.0),                       # Minimum time in seconds between commands
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "MD01Config",
            "host": "localhost",
            "port": 65000,
            "stow_alt": 80.0,
            "stow_az": 0.0,
            "offset_alt": 0.0,
            "offset_az": 0.0,
            "min_alt": 10.0,
            "max_alt": 85.0,
            "resolution": 0.1,
            "rotation_speed": 2.5,
            "rate_limit": 1.0,
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
    print("MD01 Model Initialised")
    print("="*40)


    md01_cfg = MD01Config(
        host="localhost",
        port=65000,
        stow_alt=90.0,
        stow_az=0.0,
        offset_alt=0.0,
        offset_az=0.0,
        min_alt=0.0,
        max_alt=90.0,
        rotation_speed=2.5,      # degrees / sec
        resolution=0.1,          # degrees / step
        rate_limit=1.0,          # msgs / sec
        last_update=datetime.now(timezone.utc)
    )
    print("MD01Config created successfully:", md01_cfg.to_dict())
    
    print("="*40)

