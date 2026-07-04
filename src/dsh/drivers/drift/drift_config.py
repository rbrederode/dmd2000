import enum
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

class DriftConfig(BaseModel):
    """A class representing the configuration for a drift scan dish.
        A drift scan dish does not have the ability to slew to a target and track it, but instead 
        remains fixed at a specific azimuth and altitude while the sky drifts overhead. 
        
        The configuration provides the fixed position (altitude and azimuth) at which the dish is 
        pointed during on the sky, that is also the stow position.
    
    """

    schema = Schema({      
        "_type": And(str, lambda v: v == "DriftConfig"),
        "alt": And(float, lambda v: -90.0 <= v <= 90.0),                    # Altitude pointing in degrees
        "az": And(float, lambda v: -360.0 <= v <= 360.0),                   # Azimuth pointing in degrees
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "DriftConfig",
            "alt": 90.0,
            "az": 0.0,
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
    print("Drift Model Initialised")
    print("="*40)

    drift_cfg = DriftConfig(
        alt=90.0,
        az=0.0,
        last_update=datetime.now(timezone.utc)
    )
    print("DriftConfig created successfully:", drift_cfg.to_dict())
    
    print("="*40)

