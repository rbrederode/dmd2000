from datetime import datetime, timezone
import logging
from typing import Tuple

from dsh.drivers.driver import DishDriver
from dsh.drivers.motion.motion_config import MotionDishConfig
from imu.imu import IMU, load_imu_device_list
from models.dsh import DishModel
from models.imu import IMUDeviceList

logger = logging.getLogger(__name__)

class MotionDriver(DishDriver):
    """Dish driver that reports current pointing from an attached IMU."""

    def __init__(self, dsh_model: DishModel = None, profile: str = "default", config_root: str = "config"):
        super().__init__(dsh_model)

        if dsh_model.driver_config is None:
            dsh_model.driver_config = MotionDishConfig()
        if not isinstance(dsh_model.driver_config, MotionDishConfig):
            raise TypeError(
                f"MotionDriver requires MotionDishConfig, got "
                f"{type(dsh_model.driver_config).__name__}"
            )

        self.motion_config: MotionDishConfig = dsh_model.driver_config
        self.profile = profile
        self.config_root = config_root
        self.imu_device_list: IMUDeviceList = load_imu_device_list(profile=profile, config_root=config_root)
        self.imu_device = self.imu_device_list.get_imu_by_id(self.motion_config.imu_id)

        if self.imu_device is None:
            raise ValueError(
                f"MotionDriver for Dish {dsh_model.dsh_id} could not find IMU "
                f"{self.motion_config.imu_id!r} in profile {profile!r}."
            )

        self.imu = IMU(
            imu_device=self.imu_device,
            imu_device_list=self.imu_device_list,
            profile=profile,
            config_root=config_root,
        )

    def get_poll_interval_ms(self) -> int:
        return self.imu.driver.get_poll_interval_ms() if self.imu.driver is not None else super().get_poll_interval_ms()

    def _get_rotation_speed(self) -> float:
        return 0.0

    def _get_min_max_alt(self) -> Tuple[float, float]:
        return (-90.0, 90.0)

    def _get_resolution(self) -> float:
        return self.motion_config.resolution

    def _get_stow_altaz(self) -> Tuple[float, float]:
        return self.motion_config.stow_alt, self.motion_config.stow_az

    def _get_current_altaz(self) -> Tuple[float, float]:
        if not self.imu.get_connected():
            if not self.imu.connect():
                raise RuntimeError(
                    f"MotionDriver failed to connect IMU {self.motion_config.imu_id} "
                    f"for Dish {self.dsh_model.dsh_id}."
                )

        alt, az = self.imu.get_altaz()
        if alt is None or az is None:
            raise ValueError(
                f"MotionDriver IMU {self.motion_config.imu_id} returned invalid AltAz "
                f"for Dish {self.dsh_model.dsh_id}: alt={alt}, az={az}"
            )

        return alt, az

    def _set_startup_mode(self):
        if not self.imu.get_connected():
            self.imu.connect()

    def _set_standby_fp_mode(self):
        pass

    def _set_standby_lp_mode(self):
        pass

    def _set_shutdown_mode(self):
        self.imu.disconnect()

    def _set_unknown_mode(self):
        pass

    def _set_operate_mode(self):
        if not self.imu.get_connected():
            self.imu.connect()

    def _set_maintenance_mode(self):
        pass

    def _set_config_mode(self):
        if not self.imu.get_connected():
            self.imu.connect()

    def _set_stow_mode(self, alt: float, az: float):
        logger.info(
            "MotionDriver cannot command Dish %s to stow; requested AltAz is alt=%s az=%s.",
            self.dsh_model.dsh_id,
            alt,
            az,
        )

    def _stop(self):
        pass

    def _track(self, alt: float, az: float):
        pass

    def _scan(self, alt: float, az: float):
        pass

    def _slew(self, alt: float, az: float):
        pass

    def start_scan(self):
        pass

    def end_scan(self):
        pass
