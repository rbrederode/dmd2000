from datetime import datetime, timezone
import logging
import threading
from schema import And, Or, Schema

from imu.drivers.driver import IMUDriver
from models.base import BaseModel
from models.imu import IMUData, IMUDeviceModel
from util.math import (
    adafruit_quaternion_to_wxyz,
    float_or_none,
    quaternion_to_euler,
    radians_to_degrees,
    vector_or_none,
)

logger = logging.getLogger(__name__)

class BNO085Config(BaseModel):
    """Configuration for a BNO085/BNO08x I2C IMU."""

    schema = Schema({
        "_type": And(str, lambda v: v == "BNO085Config"),
        "i2c_bus": Or(None, And(int, lambda v: v >= 0)),
        "address": And(int, lambda v: 0x03 <= v <= 0x77),
        "refresh_rate": And(Or(int, float), lambda v: v > 0),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        legacy_uart_fields = {"device", "baudrate"} & set(kwargs)
        if legacy_uart_fields:
            fields = ", ".join(sorted(legacy_uart_fields))
            raise ValueError(f"BNO085Config no longer supports UART field(s): {fields}")

        defaults = {
            "_type": "BNO085Config",
            "i2c_bus": None,
            "address": 0x4A,
            "refresh_rate": 10,
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            kwargs.setdefault(key, value)

        super().__init__(**kwargs)


class BNO085Driver(IMUDriver):
    """IMU driver for BNO085/BNO08x sensors using the I2C feature API."""

    def __init__(self, imu_model: IMUDeviceModel = None):
        """Initialize the BNO085Driver with the given IMUDeviceModel."""

        super().__init__(imu_model=imu_model)

        if imu_model.driver_config is None:
            imu_model.driver_config = BNO085Config()
        if not isinstance(imu_model.driver_config, BNO085Config):
            raise TypeError(
                f"BNO085Driver requires BNO085Config, got "
                f"{type(imu_model.driver_config).__name__}"
            )

        self.config: BNO085Config = imu_model.driver_config
        self.i2c = None
        self.bno = None
        self._poll_thread = None
        self._stop_event = threading.Event()

    def _connect(self) -> bool:
        """Connect to the BNO085 IMU over I2C and start the polling thread."""
        
        from adafruit_bno08x import (
            BNO_REPORT_ACCELEROMETER,
            BNO_REPORT_GYROSCOPE,
            BNO_REPORT_MAGNETOMETER,
            BNO_REPORT_ROTATION_VECTOR,
        )
        from adafruit_bno08x.i2c import BNO08X_I2C

        self.i2c = self._make_i2c_bus()
        self.bno = BNO08X_I2C(self.i2c, address=self.config.address)
        self.bno.enable_feature(BNO_REPORT_ACCELEROMETER)
        self.bno.enable_feature(BNO_REPORT_GYROSCOPE)
        self.bno.enable_feature(BNO_REPORT_MAGNETOMETER)
        self.bno.enable_feature(BNO_REPORT_ROTATION_VECTOR)
        self._stop_event.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, name="BNO085DriverPoll", daemon=True)
        self._poll_thread.start()

        logger.info(
            "BNO085Driver connected to BNO085 IMU over I2C bus %s at address 0x%02X.",
            self.config.i2c_bus if self.config.i2c_bus is not None else "default",
            self.config.address,
        )
        return True

    def _disconnect(self) -> None:
        """Disconnect from the BNO085 IMU and stop the polling thread."""

        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None

        if self.i2c is not None and hasattr(self.i2c, "deinit"):
            self.i2c.deinit()

        self.i2c = None
        self.bno = None
        logger.info("BNO085Driver disconnected from BNO085 IMU.")

    def _make_i2c_bus(self):
        """Create the configured Raspberry Pi I2C bus."""

        if self.config.i2c_bus is not None:
            try:
                from adafruit_extended_bus import ExtendedI2C
            except ImportError as err:
                raise RuntimeError(
                    "BNO085Driver: Using a numbered I2C bus requires adafruit-extended-bus. "
                    "Install project requirements, then try again."
                ) from err

            return ExtendedI2C(self.config.i2c_bus)

        try:
            import board
            import busio
        except ImportError as err:
            raise RuntimeError(
                "BNO085Driver: Default Raspberry Pi I2C requires adafruit-blinka. "
                "Install project requirements, enable I2C, then try again."
            ) from err

        return busio.I2C(board.SCL, board.SDA)

    def _poll_loop(self):
        """Poll the BNO085 IMU at the configured refresh rate and update IMU data."""

        interval = 1.0 / float(self.config.refresh_rate)

        while not self._stop_event.is_set():
            try:
                self._read_heading()
            except Exception as exc:
                logger.error("BNO085Driver error reading IMU heading: %s", exc)

            self._stop_event.wait(interval)

    def _read_heading(self):
        """Read the current BNO085 reports and update the IMU data."""

        accel = vector_or_none(self.bno.acceleration, 3, source="BNO085Driver")
        gyro = vector_or_none(self.bno.gyro, 3, source="BNO085Driver")
        magnetic = vector_or_none(self.bno.magnetic, 3, source="BNO085Driver")
        quaternion = adafruit_quaternion_to_wxyz(self.bno.quaternion)
        angle = quaternion_to_euler(quaternion)
        temp_celsius = float_or_none(getattr(self.bno, "temperature", None))
        timestamp = datetime.now(timezone.utc)

        imu_data = IMUData(
            imu_id=self.imu_model.imu_id,
            acceleration=accel,
            angle=angle,
            angular_vel=radians_to_degrees(gyro),
            magnetic_vector=magnetic,
            temp_celsius=temp_celsius,
            quaternion=quaternion,
            last_update=timestamp,
        )
        self._set_imu_data(imu_data)
