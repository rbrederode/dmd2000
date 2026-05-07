import enum
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

class H7T_1420Config(BaseModel):
    """A class representing the configuration for an H7T-1420 dish feed."""

    schema = Schema({      
        "_type": And(str, lambda v: v == "H7T_1420Config"),

        "gpio_pin_power": Or(None, And(int, lambda v: 0 <= v <= 27)),     # None disables GPIO power relay control
        "gpio_pin_load": Or(None, And(int, lambda v: 0 <= v <= 27)),      # None disables GPIO load relay control
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "H7T_1420Config",
            "gpio_pin_power": 19,
            "gpio_pin_load": 18,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)