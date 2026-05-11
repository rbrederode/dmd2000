from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from util.format import fmt_title

class SAWbirdH1_BareBones(BaseModel):
    """A class representing the configuration for a Nooelec Sawbird HI Barebones Bandpass Filter installed in the signal chain."""

    schema = Schema({
        "_type": And(str, lambda v: v == "SAWbirdH1_BareBones"),
        "gpio_pin_power": Or(None, And(int, lambda v: 0 <= v <= 27)),       # None disables GPIO power control
        "gpio_pin_load": Or(None, And(int, lambda v: 0 <= v <= 27)),        # None disables GPIO load control 
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "SAWbirdH1_BareBones",
            "gpio_pin_power": 17,  # Default GPIO pin for bandpass filter power control
            "gpio_pin_load": 18,   # Default GPIO pin for bandpass filter load control
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

if __name__ == "__main__":
    
    sawbird = SAWbirdH1_BareBones(
        gpio_pin_power=20,
        gpio_pin_load=21,
        last_update=datetime.now(timezone.utc),
    )

    import pprint

    print(fmt_title("SAWbird H1 Barebones Bandpass Filter Configuration"))
    pprint.pprint(sawbird.to_dict())