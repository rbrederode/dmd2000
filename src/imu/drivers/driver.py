from datetime import datetime, timezone
import logging
import threading
from typing import Callable, Optional

from models.comms import CommunicationStatus
from models.imu import IMUData, IMUDeviceModel, IMUDriverType
from util.registry import (
    IMU_DRIVER_NAMESPACE,
    get as registry_get,
    load_class_from_path as registry_load_class_from_path,
    register as registry_register,
)

logger = logging.getLogger(__name__)

class IMUDriver:
    """Base interface for physical IMU drivers.

    Concrete IMU drivers must implement:
    - `_connect()`: open the physical/logical device and start sampling.
    - `_disconnect()`: stop sampling and release device resources.

    Concrete drivers should call `_set_imu_data()` whenever a new IMU sample is
    available. For push/callback devices, that usually happens from the vendor
    callback. For polling devices, `_connect()` may start a timer/thread that
    samples at `get_poll_interval_ms()` and calls `_set_imu_data()` after each
    successful read.

    Concrete drivers normally should not override `connect()`, `disconnect()`,
    `get_imu_data()`, or `_set_imu_data()`; those methods keep the shared model
    state and callbacks consistent.
    """

    def __init__(self, imu_model: IMUDeviceModel = None):
        if imu_model is None:
            raise ValueError("IMUDriver requires an IMUDeviceModel.")

        self.imu_model = imu_model
        self.imu_data = IMUData(imu_id=imu_model.imu_id)
        self._rlock = threading.RLock()
        self._data_callback: Optional[Callable[[IMUData], None]] = None

    def set_data_callback(self, callback: Optional[Callable[[IMUData], None]]) -> None:
        """Register a callback invoked after `_set_imu_data()` stores a new sample."""
        self._data_callback = callback

    def get_poll_interval_ms(self) -> int:
        """Return the desired polling period for devices that need active polling."""
        refresh_rate = getattr(self.imu_model.driver_config, "refresh_rate", 1)
        return int(1000 / refresh_rate) if refresh_rate > 0 else 1000

    def connect(self) -> bool:
        """Open the driver and update the shared IMU communication state."""
        with self._rlock:
            connected = self._connect()
            self.imu_model.imu_connected = (
                CommunicationStatus.ESTABLISHED if connected else CommunicationStatus.NOT_ESTABLISHED
            )
            self.imu_model.last_update = datetime.now(timezone.utc)
            return connected

    def disconnect(self) -> None:
        """Close the driver and update the shared IMU communication state."""
        with self._rlock:
            self._disconnect()
            self.imu_model.imu_connected = CommunicationStatus.NOT_ESTABLISHED
            self.imu_model.last_update = datetime.now(timezone.utc)

    def get_imu_data(self) -> IMUData:
        """Return a copy of the most recent IMU sample."""
        with self._rlock:
            return self.imu_data.copy()

    def _set_imu_data(self, imu_data: IMUData) -> None:
        """Store a new IMU sample and notify the higher-level IMU wrapper.

        Concrete drivers call this after decoding or polling a fresh sample.
        """
        with self._rlock:
            self.imu_data = imu_data
            self.imu_model.last_update = datetime.now(timezone.utc)

        if self._data_callback is not None:
            self._data_callback(imu_data.copy())

    def _connect(self) -> bool:
        """Device-specific connect/start-sampling implementation."""
        raise NotImplementedError

    def _disconnect(self) -> None:
        """Device-specific disconnect/stop-sampling implementation."""
        raise NotImplementedError


def create_imu_driver(imu_model: IMUDeviceModel) -> Optional[IMUDriver]:
    """Instantiate the concrete driver selected by IMUDeviceModel."""
    if imu_model.driver_type == IMUDriverType.WITMOTION:
        from imu.drivers.witmotion import WitMotionDriver

        ctor = WitMotionDriver
        registry_register(IMU_DRIVER_NAMESPACE, "witmotion", ctor)
        return ctor(imu_model=imu_model)

    driver_identifier = None
    if imu_model.driver_config is not None:
        cfg = imu_model.driver_config
        if isinstance(cfg, str):
            driver_identifier = cfg
        else:
            driver_identifier = getattr(cfg, "driver", None) or getattr(cfg, "name", None)

    if driver_identifier:
        ctor = registry_get(IMU_DRIVER_NAMESPACE, driver_identifier)
        if ctor:
            return ctor(imu_model=imu_model)

        try:
            cls = registry_load_class_from_path(driver_identifier)
            return cls(imu_model=imu_model)
        except Exception:
            logger.exception("imu.drivers.driver:Failed to load IMU driver with identifier '%s'", driver_identifier)

    if imu_model.driver_type == IMUDriverType.UNKNOWN:
        logger.warning("imu.drivers.driver: IMU %s has no physical driver configured.", imu_model.imu_id)
        return None

    raise ValueError(f"imu.drivers.driver: Unsupported IMU driver type or configuration: {imu_model.driver_type}")
