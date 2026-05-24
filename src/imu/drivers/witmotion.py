from datetime import datetime, timezone
import logging
import subprocess

from schema import And, Schema
import witmotion

from models.base import BaseModel
from models.imu import IMUDeviceModel
from imu.drivers.driver import IMUDriver

logger = logging.getLogger(__name__)

class WitMotionConfig(BaseModel):
    """Configuration for a WitMotion serial IMU."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WitMotionConfig"),
        "device": And(str, lambda v: isinstance(v, str)),
        "baudrate": And(int, lambda v: v > 0),
        "refresh_rate": And(int, lambda v: v > 0),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "WitMotionConfig",
            "device": "auto",
            "baudrate": 9600,
            "refresh_rate": 1,
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            kwargs.setdefault(key, value)

        super().__init__(**kwargs)


class WitMotionDriver(IMUDriver):
    """IMU driver for WitMotion serial devices."""

    def __init__(self, imu_model: IMUDeviceModel = None):
        super().__init__(imu_model=imu_model)

        if imu_model.driver_config is None:
            imu_model.driver_config = WitMotionConfig()
        if not isinstance(imu_model.driver_config, WitMotionConfig):
            raise TypeError(
                f"WitMotionDriver requires WitMotionConfig, got "
                f"{type(imu_model.driver_config).__name__}"
            )

        self.config: WitMotionConfig = imu_model.driver_config
        self.imu = None

    def _connect(self) -> bool:
        if self.config.device == "auto":
            detected = self.auto_detect_imu()
            if detected is not None:
                self.config.device = detected

        self.imu = witmotion.IMU(self.config.device, self.config.baudrate)
        self.imu.set_update_rate(self.config.refresh_rate)
        self.imu.subscribe(self._handle_message)
        logger.info("WitMotionDriver connected to WitMotion IMU on %s at %s baud.", self.config.device, self.config.baudrate)
        return True

    def _disconnect(self) -> None:
        if self.imu is not None:
            self.imu.close()
            self.imu = None
            logger.info("WitMotionDriver disconnected from WitMotion IMU.")

    def auto_detect_imu(self):
        """Return the first serial device that looks like a USB IMU."""
        result = subprocess.run(
            r"ls /dev/tty* | grep -E '(usbserial|ttyUSB)'",
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("WitMotionDriver error trying to detect IMU device: %s", result.stderr)
            return None

        devices = result.stdout.splitlines()
        if len(devices) > 0:
            return devices[0]

        logger.warning("WitMotionDriver: No IMU devices found.")
        return None

    def _handle_message(self, msg):
        try:
            imu_data = self.get_imu_data()
            imu_data.last_update = datetime.now(timezone.utc)

            if isinstance(msg, witmotion.protocol.MagneticMessage):
                imu_data.magnetic_vector = _vector_or_none(msg.mag, 3)
            elif isinstance(msg, witmotion.protocol.AccelerationMessage):
                imu_data.acceleration = _vector_or_none(msg.a, 3)
                imu_data.temp_celsius = float(msg.temp_celsius)
            elif isinstance(msg, witmotion.protocol.AngularVelocityMessage):
                imu_data.angular_vel = _vector_or_none(msg.w, 3)
                imu_data.temp_celsius = float(msg.temp_celsius)
            elif isinstance(msg, witmotion.protocol.AngleMessage):
                imu_data.angle = [float(msg.roll), float(msg.pitch), float(msg.yaw)]
            elif isinstance(msg, witmotion.protocol.QuaternionMessage):
                imu_data.quaternion = _vector_or_none(msg.q, 4)

            self._set_imu_data(imu_data)

        except Exception as exc:
            logger.error("WitMotionDriver error processing IMU message: %s", exc)


def _vector_or_none(value, length):
    if value is None:
        return None
    if len(value) != length:
        raise ValueError(f"WitMotionDriver error: Expected vector length {length}, got {len(value)}")
    if all(v is None for v in value):
        return None
    return [float(v) for v in value]
