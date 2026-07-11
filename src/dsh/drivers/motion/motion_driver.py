from datetime import datetime, timezone
import logging
import socket
import time
from typing import Tuple

from api import imu_app
from dsh.drivers.driver import DishDriver
from dsh.drivers.motion.motion_config import MotionDishConfig
from imu.imu import IMU, load_imu_device_list
from ipc.message import APIMessage
from models.dsh import DishModel, PointingState, Capability, DishMode, Feed, DriverType
from models.imu import IMUDeviceList, IMUData
from util.xbase import XBase, XTimeoutWaitingForResponse, XCommsFailure, XStreamUnableToExtract

logger = logging.getLogger(__name__)

class MotionDriver(DishDriver):
    """Dish driver that reports actual pointing using an attached Inertial Measurement Unit."""

    def __init__(self, dsh_model: DishModel = None, profile: str = "default"):
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

        self.imu_system = "imu"
        self.imu_api = imu_app.IMU_APP()
        self.last_command_time = 0          # Track last command timestamp for rate limiting

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
        return self._get_motion_altaz()

    def _set_startup_mode(self):
        if not self.imu.is_connected():
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
        if not self.imu.is_connected():
            self.imu.connect()

    def _set_maintenance_mode(self):
        pass

    def _set_config_mode(self):
        if not self.imu.is_connected():
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

    def _get_motion_altaz(self) -> Tuple[float, float]:
        """Returns the current altitude and azimuth of the dish as a tuple of decimal numbers [degrees]."""

        imu_req = APIMessage(api_version=self.imu_api.get_api_version())
        
        imu_req.set_json_api_header(
            api_version=self.imu_api.get_api_version(), 
            dt=datetime.now(timezone.utc), 
            from_system=self.dsh_model.dsh_id,
            to_system=self.imu_system, 
            api_call={
                "msg_type": "req", 
                "action_code": "get", 
                "property": imu_app.PROPERTY_IMU_DATA, 
            })

        imu_rsp = self._send_imu_request(imu_req)

        if imu_rsp is not None:

            api_call = imu_rsp.get_api_call()
            value = api_call.get("value", None) if api_call is not None else None
            imu_data = IMUData.from_dict(value) if value is not None else None

            return tuple(imu_data.altaz) if imu_data is not None and imu_data.altaz is not None else (None, None)
        
        return None, None

    def _rate_limit_wait(self, imu_req: APIMessage):
        """Waits if necessary to enforce rate limiting between commands to the imu controller."""

        if self.motion_config.rate_limit > 0.0:
            time_since_last_cmd = time.time() - self.last_command_time
            if time_since_last_cmd < self.motion_config.rate_limit:
                sleep_time = self.motion_config.rate_limit - time_since_last_cmd
                logger.info(f"IMUDriver for controller {self.motion_config.imu_host} {self.motion_config.imu_port} rate limiting: {sleep_time:.3f}s before sending cmd {imu_req.get_cmd()}")
                time.sleep(sleep_time)

    def _send_imu_request(self, imu_req: APIMessage) -> APIMessage:
        """ Sends a request to the IMU server and returns the response.
                :param imu_req: The request to send as APIMessage object.
                :return: The response from the IMU server as APIMessage object (or None if no response).
            :raises XTimeoutWaitingForResponse if no response is received when expected.
            :raises XCommsFailure if there is a communication failure.
        """
        # Enforce rate limiting between commands
        self._rate_limit_wait(imu_req)  
        
        # Create socket and connect to IMU server
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        try:
            sock.connect((self.motion_config.imu_host, self.motion_config.imu_port))
        except socket.error as e:
            logger.error(f"IMUDriver for controller {self.motion_config.imu_host} {self.motion_config.imu_port} socket connection error: {e}")
            raise XCommsFailure(f"IMUDriver for controller {self.motion_config.imu_host} {self.motion_config.imu_port} socket connection error: {e}")
        
        # Send request message to IMU
        req_data = imu_req.to_data()
        logger.debug(f"IMUDriver for controller {self.motion_config.imu_host} {self.motion_config.imu_port} sending request to IMU server:\n{imu_req}")
        self.last_command_time = time.time()
        sock.send(req_data)

        time.sleep(0.01) # Seconds, to ensure message is ready, just in case
        
        # Read response data (bytes) from IMU server
        rsp_data = sock.recv(1024)
        sock.close()
        
        if len(rsp_data) == 0:
            raise XTimeoutWaitingForResponse(f"IMUDriver for controller {self.motion_config.imu_host} {self.motion_config.imu_port} timed-out waiting for rsp." + \
                f" No data received after sending request {imu_req}.")
        
        # Decode response data (bytes) to APIMessage
        imu_rsp = APIMessage()
        try:
            imu_rsp.from_data(rsp_data)
        except XStreamUnableToExtract as e:
            logger.error(f"IMUDriver for controller {self.motion_config.imu_host} {self.motion_config.imu_port} failed to decode response data: {e}")
            raise XStreamUnableToExtract(f"IMUDriver for controller {self.motion_config.imu_host} {self.motion_config.imu_port} failed to decode response data: {e}", data=rsp_data)
        logger.debug(f"IMUDriver for controller {self.motion_config.imu_host} {self.motion_config.imu_port} received response from IMU server:\n{imu_rsp}")
        return imu_rsp

if __name__ == "__main__":

    motion_cfg = MotionDishConfig(
        imu_host="192.168.0.36",
        imu_port=52500,
        stow_alt=90.0,
        stow_az=0.0,
        resolution=0.1,      # degrees / step
        rate_limit=0.1,      # msgs / sec
        last_update=datetime.now(timezone.utc)
    )

    dish001 = DishModel(
        dsh_id="dish001",
        short_desc="3m Jodrell Dish",
        diameter=3.0,
        fd_ratio=0.43,
        latitude=53.2421, longitude=-2.3067, height=80.0,
        mode=DishMode.STANDBY_FP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.NONE,
        dig_id="dig001",
        capability=Capability.OPERATE_FULL,
        driver_type=DriverType.MOTION,
        driver_config=motion_cfg,
        last_update=datetime.now(timezone.utc)
    )

    motion_driver = MotionDriver(dsh_model=dish001)

    alt, az = motion_driver._get_motion_altaz()
    print(f"Current altitude: {alt}, azimuth: {az}")
