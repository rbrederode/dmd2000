from datetime import datetime, timezone
from schema import Schema, And

from models.base import BaseModel


class MotionDishConfig(BaseModel):
    """Configuration for a dish whose pointing is reported by an attached IMU."""

    schema = Schema({
        "_type": And(str, lambda v: v == "MotionDishConfig"),
        "imu_id": And(str, lambda v: isinstance(v, str)),                   # ID of the attached IMU Device e.g. "imu001"
        "imu_host": And(str, lambda v: isinstance(v, str)),                 # Hostname or IP address of the imu server
        "imu_port": And(int, lambda v: 0 <= v <= 65535),                    # Port number for the imu server
        "resolution": And(float, lambda v: v >= 0.0),                       # Degrees per step resolution of the dish
        "stow_alt": And(float, lambda v: -90.0 <= v <= 90.0),               # Stow altitude in degrees
        "stow_az": And(float, lambda v: -360.0 <= v <= 360.0),              # Stow azimuth in degrees
        "rate_limit": And(float, lambda v: v >= 0.0),                       # Minimum time in seconds between commands
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),    # Last update timestamp
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "MotionDishConfig",
            "imu_id": "imu001",
            "imu_host": "127.0.0.1",
            "imu_port": 52500,
            "resolution": 1.0,
            "stow_alt": 90.0,
            "stow_az": 0.0,
            "rate_limit": 0.1,
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            kwargs.setdefault(key, value)

        super().__init__(**kwargs)
