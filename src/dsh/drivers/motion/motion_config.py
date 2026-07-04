from datetime import datetime, timezone
from schema import Schema, And

from models.base import BaseModel


class MotionDishConfig(BaseModel):
    """Configuration for a dish whose pointing is reported by an attached IMU."""

    schema = Schema({
        "_type": And(str, lambda v: v == "MotionDishConfig"),
        "imu_id": And(str, lambda v: isinstance(v, str)),
        "resolution": And(float, lambda v: v >= 0.0),
        "stow_alt": And(float, lambda v: -90.0 <= v <= 90.0),
        "stow_az": And(float, lambda v: -360.0 <= v <= 360.0),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "MotionDishConfig",
            "imu_id": "imu001",
            "resolution": 1.0,
            "stow_alt": 90.0,
            "stow_az": 0.0,
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            kwargs.setdefault(key, value)

        super().__init__(**kwargs)
