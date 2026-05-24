
import astropy.units as u
from astropy.coordinates import AltAz
from astropy.time import Time
from datetime import datetime, timezone
import logging
import pytest
import socket
import time
from typing import Tuple

from dsh.drivers.driver import DishDriver
from ipc.tcp_server import TCPServer
from dsh.drivers.drift.drift_config import DriftConfig
from models.dsh import DishModel, PointingState, Capability, DishMode, Feed, DriverType
from models.health import HealthState
from util.xbase import XBase, XTimeoutWaitingForResponse, XCommsFailure, XInvalidTransition
import util.util as util

logger = logging.getLogger(__name__)

class DriftDriver(DishDriver):

    def __init__(self, dsh_model: DishModel=None):
        super().__init__(dsh_model)

        self.drift_config: DriftConfig = dsh_model.driver_config
        self.last_command_time = 0  # Track last command timestamp for rate limiting

    def _get_rotation_speed(self) -> float:
        """ Get the rotation speed of the dish.
            :return: The rotation speed in degrees per second.
        """
        return 0.0

    def _get_min_max_alt(self) -> Tuple[float, float]:
        """ Get the minimum and maximum altitude limits of the dish from the MD01 configuration.
            :return: A tuple of (min_altitude, max_altitude) in degrees.
        """
        return (self.drift_config.alt, self.drift_config.alt)

    def _get_resolution(self) -> float:
        """ Get the resolution of the dish.
            :return: The resolution in degrees per step.
        """
        return 0.0

    def _get_stow_altaz(self) -> Tuple[float, float]:
        """ Get the stow Alt Az position of the dish from the Drift configuration.
            :return: The stow Alt Az position as a tuple of (altitude, azimuth).
        """
        return (self.drift_config.alt, self.drift_config.az)

    def _get_current_altaz(self) -> (float, float):
        """ Get the current Alt Az position of the dish from the Drift configuration.
            :return: The current Alt Az position of the dish as a tuple of (altitude, azimuth).
            :raises XBase: If there is an error getting the current Alt Az position.
        """
        return self.drift_config.alt, self.drift_config.az

    def get_desired_altaz(self, target) -> AltAz:
        """Return the fixed physical pointing for any target accepted by a drift dish."""
        time = Time(datetime.now(timezone.utc))
        desired_altaz = AltAz(
            obstime=time,
            location=self.location,
            alt=float(self.drift_config.alt) * u.deg,
            az=float(self.drift_config.az) * u.deg,
        )
        self.set_desired_altaz(desired_altaz)
        return desired_altaz

    def slew(self, altaz: AltAz):
        """Accept target acquisition immediately because a drift dish cannot move."""
        self.set_desired_altaz(self.get_desired_altaz(self.dsh_model.target))
        self.dsh_model.pointing_state = PointingState.READY
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def track(self):
        """Keep a drift dish READY rather than entering a tracking state."""
        self.set_desired_altaz(self.get_desired_altaz(self.dsh_model.target))
        self.dsh_model.pointing_state = PointingState.READY
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def scan(self):
        """Keep a drift dish READY rather than entering a scan state."""
        self.set_desired_altaz(self.get_desired_altaz(self.dsh_model.target))
        self.dsh_model.pointing_state = PointingState.READY
        self.dsh_model.last_update = datetime.now(timezone.utc)

    def _set_startup_mode(self):
        """
            Perform actions on the dish to set startup mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_standby_fp_mode(self):
        """
            Perform actions on the dish to set standby full power mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_standby_lp_mode(self):
        """
            Perform actions on the dish to set standby low power mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_shutdown_mode(self):
        """
            Perform actions on the dish to set shutdown mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_unknown_mode(self):
        """
            Perform actions on the dish when setting the dish to unknown mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_operate_mode(self):
        """
            Perform actions on the dish when setting the dish to operate mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        # Nothing to do here
        pass

    def _set_maintenance_mode(self):
        """
            Perform actions on the dish when setting the dish to maintenance mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_config_mode(self):
        """
            Perform actions on the dish when setting the dish to config mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        # Nothing to do here
        pass

    def _set_stow_mode(self, alt: float, az: float):
        """
            Perform actions on the dish when setting the dish to stow mode.
            Do not set the dish model attributes here, that is done in the base class.
            :raises XBase: If there is an error setting the telescope to stow mode.
        """               
        # Nothing to do here
        pass

    def _stop(self):
        """ Stop any movement of the dish.
            :raises XBase: If there is an error stopping the dish.
        """
        # Nothing to do here
        pass

    def _track(self, alt: float, az: float):
        """
            Track the current target.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _scan(self, alt: float, az: float):
        """
            Scan to the current target (same as tracking).
            Do not set the dish model attributes here, that is done in the base class.
        """
        self._track(alt, az)

    def _slew(self, alt: float, az: float):
        """
            Slew to the specified AltAz position.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def start_scan(self):
        """
            Start scanning the current target.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def end_scan(self):
        """
            Stop scanning the current target.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

# Runs tests using: pytest dsh/drivers/drift/driver.py -v
# -v for verbose output (or -vv or -vvv for more verbosity)
# -s to show print output

if __name__ == "__main__":

    from dsh.drivers.drift.drift_config import DriftConfig

    drift_cfg = DriftConfig(
        alt=90.0,
        az=0.0,
        last_update=datetime.now(timezone.utc)
    )

    dish002 = DishModel(
        dsh_id="dish002",
        short_desc="3m Jodrell Dish",
        diameter=3.0,
        fd_ratio=0.43,
        latitude=53.2421, longitude=-2.3067, height=80.0,
        mode=DishMode.STANDBY_FP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.NONE,
        dig_id="dig002",
        capability=Capability.OPERATE_FULL,
        driver_type=DriverType.DRIFT,
        driver_config=drift_cfg,
        last_update=datetime.now(timezone.utc)
    )

    drift_driver = DriftDriver(dsh_model=dish002)

    dist = util.get_angular_distance(35.25, 325.75, 40.25, 330.75)

    drift_driver._stop()
    alt, az = drift_driver._get_current_altaz()
    print(f"Current Altitude: {alt} degrees, Azimuth: {az} degrees")

    import astropy.units as u
    from astropy.coordinates import EarthLocation, AltAz, SkyCoord

    now = Time(datetime.now(timezone.utc))
    frame = AltAz(obstime=now, location=drift_driver.location)

    m33 = SkyCoord.from_name("M33")
    m33_altaz = m33.transform_to(AltAz(obstime=now, location=drift_driver.location))
    print(f"Altitude: {m33_altaz.alt:.2f}, Azimuth: {m33_altaz.az:.2f}")
    try:
        print(f"Attempt slew to M33 at Alt: {m33_altaz.alt.degree:.2f} deg, Az: {m33_altaz.az.degree:.2f} deg")
        drift_driver._slew(m33_altaz.alt.degree, m33_altaz.az.degree)
    except Exception as e:
        print(f"Error occurred while slewing: {e}")

    alt, az = drift_driver._get_current_altaz()
    print(f"After Slew - Current Altitude: {alt} degrees, Azimuth: {az} degrees")

    print("Setting stow mode...")
    drift_driver._set_stow_mode(alt=drift_cfg.alt, az=drift_cfg.az)
    alt, az = drift_driver._get_current_altaz()
    print(f"After Stow - Current Altitude: {alt} degrees, Azimuth: {az} degrees")

