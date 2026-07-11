from datetime import datetime, timezone
import enum
import logging
import re
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from models.comms import CommunicationStatus
from util import log
from util.format import fmt_title

logger = logging.getLogger(__name__)

class IMUDriverType(enum.IntEnum):
    """Supported IMU driver implementations."""

    UNKNOWN = 0
    WITMOTION = 1
    BNO085 = 2

class IMUData(BaseModel):
    """A class representing intertial measurement unit (IMU) data from a specific imu device and time."""

    schema = Schema({
        "_type": And(str, lambda v: v == "IMUData"),
        "imu_id": And(str, lambda v: re.fullmatch(r"imu\d{3}", v) is not None),                                                                         # IMU device ID e.g "imu001"
        "acceleration": Or(None, And(lambda v: isinstance(v, (list, tuple)), lambda v: all(isinstance(x, (int, float)) for x in v) and len(v) == 3)),   # Acceleration vector (x, y, z) in m/s^2
        "angle": Or(None, And(lambda v: isinstance(v, (list, tuple)), lambda v: all(isinstance(x, (int, float)) for x in v) and len(v) == 3)),          # Angle vector (roll, pitch, yaw) in degrees
        "angular_vel": Or(None, And(lambda v: isinstance(v, (list, tuple)), lambda v: all(isinstance(x, (int, float)) for x in v) and len(v) == 3)),    # Angular velocity vector (roll_rate, pitch_rate, yaw_rate) in degrees/s
        "magnetic_vector": Or(None, And(lambda v: isinstance(v, (list, tuple)), lambda v: all(isinstance(x, (int, float)) for x in v) and len(v) == 3)),# Magnetic field vector (x, y, z) in microteslas
        "temp_celsius": Or(None, And(float, lambda v: isinstance(v, float))),                                                                           # Temperature in degrees Celsius
        "quaternion": Or(None, And(lambda v: isinstance(v, (list, tuple)), lambda v: all(isinstance(x, (int, float)) for x in v) and len(v) == 4)),     # Quaternion vector (w, x, y, z)
        "altaz": Or(None, And(lambda v: isinstance(v, (list, tuple)), lambda v: all(isinstance(x, (int, float)) for x in v) and len(v) == 2)),          # Altitude and azimuth vector (altitude, azimuth) in degrees
        "last_update": Or(None, And(datetime, lambda v: isinstance(v, datetime))),                                                                      # Timestamp when the imu data was collected
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "IMUData",
            "imu_id": None,
            "acceleration": None,
            "angle": None,
            "angular_vel": None,
            "magnetic_vector": None,
            "temp_celsius": None,
            "quaternion": None,
            "altaz": None,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def __str__(self):
        return (
            "ImuData from device: "
            f"{self.imu_id} (\n"
            f"  acceleration={self.acceleration} m/s^2,\n"
            f"  angle={self.angle} deg,\n"
            f"  angular_vel={self.angular_vel} deg/s,\n"
            f"  magnetic_vector={self.magnetic_vector} μT,\n"
            f"  temp_celsius={self.temp_celsius} °C,\n"
            f"  quaternion={self.quaternion},\n"
            f"  altaz={self.altaz},\n"
            f"  last_update={self.last_update.isoformat() if self.last_update else None})"
        )

class IMUDeviceModel(BaseModel):
    """
    Model for Inertial Measurement Unit (IMU) device.
    """

    schema = Schema({
        "_type": And(str, lambda v: v == "IMUDeviceModel"),
        "driver_type": And(IMUDriverType, lambda v: isinstance(v, IMUDriverType)),                   # IMU driver implementation
        "driver_config": Or(None, lambda v: v is None or isinstance(v, BaseModel)),                  # IMU driver configuration
        "imu_id": And(str, lambda v: isinstance(v, str)),                                            # IMU device ID e.g "imu001"    
        "az_offset": And(float, lambda v: isinstance(v, float)),                                     # Azimuth offset in degrees
        "alt_offset": And(float, lambda v: isinstance(v, float)),                                    # Altitude offset in degrees        
        "alt_vector": And(str, lambda v: v in {"roll", "pitch"}),                                    # Altitude vector   
        "az_vector": And(str, lambda v: v in {"roll", "yaw"}),                                       # Azimuth vector
        "imu_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

      # Default values
        defaults = {
            "_type": "IMUDeviceModel",
            "imu_id": None,                                         # IMU device ID e.g "imu001"
            "driver_type": IMUDriverType.WITMOTION,
            "driver_config": None,
            "az_offset": 0.0,
            "alt_offset": 0.0,
            "alt_vector": "roll",
            "az_vector": "yaw",
            "imu_connected": CommunicationStatus.NOT_ESTABLISHED,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)


# Backwards-compatible alias for callers that use the shorter schema name.
IMUDevice = IMUDeviceModel

class IMUDeviceList(BaseModel):
    """A class representing a list of IMU devices."""

    schema = Schema({
        "_type": And(str, lambda v: v == "IMUDeviceList"),
        "list_id": And(str, lambda v: isinstance(v, str)),
        "imu_list": And(list, lambda v: isinstance(v, list)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "IMUDeviceList",
            "list_id": "<undefined>",
            "imu_list": [],
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def get_imu_by_id(self, imu_id: str) -> IMUDeviceModel:
        for imu in self.imu_list:
            if imu.imu_id == imu_id:
                return imu
        return None

if __name__ == "__main__":

    imu_device001 = IMUDeviceModel(
        imu_id="imu001",
        driver_type=IMUDriverType.WITMOTION,
        driver_config=None,
        az_offset=0.0,
        alt_offset=0.0,
        alt_vector="roll",
        az_vector="yaw",
        imu_connected=CommunicationStatus.NOT_ESTABLISHED,
        last_update=datetime.now(timezone.utc)
    )

    import pprint
    print(fmt_title("IMU Device 001"))
    pprint.pprint(imu_device001.__dict__)

    imu_list001 = IMUDeviceList(list_id="default", imu_list=[imu_device001])
    print(fmt_title("IMU Device List 001"))
    pprint.pprint(imu_list001.__dict__)

    imu_data001 = IMUData(
        imu_id="imu001",
        acceleration=[0.0, 0.0, 9.8],
        angle=[0.0, 0.0, 0.0],
        angular_vel=[0.0, 0.0, 0.0],
        magnetic_vector=[30.0, 60.0, 90.0],
        temp_celsius=25.0,
        quaternion=[1.0, 0.0, 0.0, 0.0],
        last_update=datetime.now(timezone.utc)
    )
    print(fmt_title("IMU Data 001"))
    pprint.pprint(imu_data001.__dict__)
