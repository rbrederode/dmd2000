from datetime import datetime, timezone
from schema import And, Or, Schema

from models.base import BaseModel

class TempReading(BaseModel):
    """ Temperature sensor reading with optional humidity and pressure values.
        Initially used by the Digitiser to measure temperature inside the Digitiser Assembly.
        Available for generic use by any temperature sensor.
    """

    schema = Schema({
        "_type": And(str, lambda v: v == "TempReading"),
        "temperature": Or(None, And(float, lambda v: -100 <= v <= 100)), # Celsius
        "humidity": Or(None, And(float, lambda v: 0 <= v <= 100)),       # Relative Humidity in %
        "pressure": Or(None, And(float, lambda v: v >= 0)),              # Pressure in hPa
        "last_update": And(datetime, lambda v: isinstance(v, datetime)), # Last update time of the reading (UTC)
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        now = datetime.now(timezone.utc)
        defaults = {
            "_type": "TempReading",
            "temperature": None,
            "humidity": None,
            "pressure": None,
            "last_update": now,
        }

        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def __str__(self):
        return (
            "TempReading("
            f"temperature={self.temperature}, "
            f"humidity={self.humidity}, "
            f"pressure={self.pressure}, "
            f"last_update={self.last_update.isoformat()})"
        )
