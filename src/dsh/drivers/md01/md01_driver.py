
from astropy.time import Time
from datetime import datetime, timezone
import logging
import pytest
import socket
import time
from typing import Tuple

from dsh.drivers.driver import DishDriver
from ipc.tcp_server import TCPServer
from dsh.drivers.md01.md01_config import MD01Config
from dsh.drivers.md01.md01_msg import MD01Msg
from models.dsh import DishModel, PointingState, Capability, DishMode, Feed, DriverType
from models.health import HealthState
from util.xbase import XBase, XTimeoutWaitingForResponse, XCommsFailure, XInvalidTransition
import util.util as util

logger = logging.getLogger(__name__)

# Basic Python3 script to communicate with MD-01 via Ethernet module
# Only using standard Python3 libraries
#
# Format from http://ryeng.name/blog/3
#The SPID protocol supports 3 commands: stop, status and set. The stop
#command stops the rotator in its current position. The status command
#returns the current position of the rotator, and the set command tells
#the rotator to rotate to a given position.
#All commands are issued as 13 byte packets, and responses are received
#12 byte packets (Rot2Prog).
#
#COMMAND PACKETS
#Byte:    0   1    2    3    4    5    6    7    8    9    10   11  12
#       -----------------------------------------------------------------
#Field: | S | H1 | H2 | H3 | H4 | PH | V1 | V2 | V3 | V4 | PV | K | END |
#       -----------------------------------------------------------------
#Value:   57  3x   3x   3x   3x   0x   3x   3x   3x   3x   0x   xF  20 (hex)
#
#S:     Start byte. This is always 0x57 ('W')
#H1-H4: Azimuth as ASCII characters 0-9
#PH:    Azimuth resolution in pulses per degree (ignored!)
#V1-V4: Elevation as ASCII characters 0-9
#PV:    Elevation resolution in pulses per degree (ignored!)
#K:     Command (0x0F=stop, 0x1F=status, 0x2F=set)
#END:   End byte. This is always 0x20 (space)

class MD01Driver(DishDriver):

    COMMAND_ATTEMPTS = 2

    def __init__(self, dsh_model: DishModel=None):
        super().__init__(dsh_model)

        self.md01_config: MD01Config = dsh_model.driver_config
        self.last_command_time = 0  # Track last command timestamp for rate limiting
        # Full encoded packet of the last SET command that received a valid
        # acknowledgement. Comparing packets applies the MD01's actual
        # position quantisation instead of comparing floating-point inputs.
        self._last_set_command_data = None

    def _get_rotation_speed(self) -> float:
        """ Get the rotation speed of the dish from the MD01 configuration.
            :return: The rotation speed in degrees per second.
        """
        return self.md01_config.rotation_speed

    def _get_min_max_alt(self) -> Tuple[float, float]:
        """ Get the minimum and maximum altitude limits of the dish from the MD01 configuration.
            :return: A tuple of (min_altitude, max_altitude) in degrees.
        """
        return (self.md01_config.min_alt, self.md01_config.max_alt)

    def _get_resolution(self) -> float:
        """ Get the resolution of the dish from the MD01 configuration.
            :return: The resolution in degrees per step.
        """
        return self.md01_config.resolution

    def _get_stow_altaz(self) -> Tuple[float, float]:
        """ Get the stow Alt Az position of the dish from the MD01 configuration.
            :return: The stow Alt Az position as a tuple of (altitude, azimuth).
        """
        return (self.md01_config.stow_alt, self.md01_config.stow_az)

    def _get_current_altaz(self) -> (float, float):
        """ Get the current Alt Az position of the dish from the MD01 controller.
            :return: The current Alt Az position of the dish as a tuple of (altitude, azimuth).
            :raises XBase: If there is an error getting the current Alt Az position.
        """
        alt, az = self._get_md01_altaz()
        return alt, az

    def _set_startup_mode(self):
        """
            Perform actions on the MD01 controller when setting the dish to startup mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_standby_fp_mode(self):
        """
            Perform actions on the MD01 controller when setting the dish to standby full power mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_standby_lp_mode(self):
        """
            Perform actions on the MD01 controller when setting the dish to standby low power mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_shutdown_mode(self):
        """
            Perform actions on the MD01 controller when setting the dish to shutdown mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_unknown_mode(self):
        """
            Perform actions on the MD01 controller when setting the dish to unknown mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_operate_mode(self):
        """
            Perform actions on the MD01 controller when setting the dish to operate mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        # Nothing to do here
        pass

    def _set_maintenance_mode(self):
        """
            Perform actions on the MD01 controller when setting the dish to maintenance mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        pass

    def _set_config_mode(self):
        """
            Perform actions on the MD01 controller when setting the dish to config mode.
            Do not set the dish model attributes here, that is done in the base class.
        """
        # Nothing to do here
        pass

    def _set_stow_mode(self, alt: float, az: float):
        """
            Perform actions on the MD01 controller when setting the dish to stow mode.
            Do not set the dish model attributes here, that is done in the base class.
            :raises XBase: If there is an error setting the telescope to stow mode.
        """               
        # Slew to stow position        
        self._set_md01_altaz(alt, az)

    def _stop(self):
        """ Stop any movement of the dish on the MD01 controller.
            :raises XBase: If there is an error stopping the dish.
        """
        self._stop_md01()

    def _track(self, alt: float, az: float):
        """
            Track the current target.
            Do not set the dish model attributes here, that is done in the base class.
        """

        # This MD01 mount does not support flipped coordinates. Always command the
        # requested encoder position so it remains consistent with desired_altaz.
        if not self.can_reach(alt, az):
            raise XInvalidTransition(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} cannot reach Alt: {alt} deg, Az: {az} deg due to dish limits: {self.md01_config.min_alt}-{self.md01_config.max_alt} deg altitude.")

        logger.debug(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} tracking to Alt: {alt} deg, Az: {az} deg.")
    
        self._set_md01_altaz(alt, az)

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

        # This MD01 mount does not support flipped coordinates. Always command the
        # requested encoder position so it remains consistent with desired_altaz.
        if not self.can_reach(alt, az):
            raise XInvalidTransition(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} cannot reach Alt: {alt} deg, Az: {az} deg due to dish limits: {self.md01_config.min_alt}-{self.md01_config.max_alt} deg altitude.")

        logger.debug(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} slewing to Alt: {alt} deg, Az: {az} deg.")
    
        self._set_md01_altaz(alt, az)
        
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

    def _rate_limit_wait(self, md01_cmd: MD01Msg):
        """Waits if necessary to enforce rate limiting between commands to the md01 controller."""

        if self.md01_config.rate_limit > 0.0:
            time_since_last_cmd = time.time() - self.last_command_time
            if time_since_last_cmd < self.md01_config.rate_limit:
                sleep_time = self.md01_config.rate_limit - time_since_last_cmd
                logger.info(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} rate limiting: {sleep_time:.3f}s before sending cmd {md01_cmd.get_cmd()}")
                time.sleep(sleep_time)

    def _send_md01_command(self, md01_cmd: MD01Msg) -> MD01Msg:
        """
            Sends a command to the MD-01 rotator and returns the response.
                :param md01_cmd: The command to send as MD01Msg object.
                :return: The response from the MD-01 as an MD01Msg object.
            :raises XTimeoutWaitingForResponse if no response is received when expected.
            :raises XCommsFailure if there is a communication failure.
        """
        for attempt in range(1, self.COMMAND_ATTEMPTS + 1):
            try:
                return self._send_md01_command_once(md01_cmd)
            except XTimeoutWaitingForResponse:
                if attempt >= self.COMMAND_ATTEMPTS:
                    raise

                logger.warning(
                    f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} "
                    f"received no valid response for {md01_cmd.get_cmd()} on attempt {attempt}/"
                    f"{self.COMMAND_ATTEMPTS}; retrying."
                )

        raise AssertionError("MD01 command retry loop exited unexpectedly")

    def _send_md01_command_once(self, md01_cmd: MD01Msg) -> MD01Msg:
        """Send one MD01 command attempt and return its decoded response."""
        # Enforce rate limiting between commands
        self._rate_limit_wait(md01_cmd)  
        
        # Create socket and connect to MD01 controller
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        try:
            sock.connect((self.md01_config.host, self.md01_config.port))
        except socket.error as e:
            logger.error(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} socket connection error: {e}")
            raise XCommsFailure(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} socket connection error: {e}")
        
        # Send command message to MD01
        cmd_data = md01_cmd.to_data()
        logger.debug(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} sending command to MD01 controller:\n{md01_cmd}")
        self.last_command_time = time.time()
        sock.sendall(cmd_data)
        self._trace_md01_message("TX", md01_cmd)

        time.sleep(0.01) # Seconds, to ensure message is ready, just in case

        # Every MD01 command, including SET, returns a 12-byte position
        # response. Consume that response before closing the connection so it
        # cannot remain buffered in the serial-to-Ethernet adapter and become
        # prefixed to the response for the next command.
        rsp_data = bytearray()
        discarded_data = bytearray()
        try:
            while True:
                # The serial-to-Ethernet adapter can prefix a response with
                # command-mode traffic (for example, b"AT+ENTM\r"). Find the
                # first complete, structurally valid MD01 response rather than
                # treating the first 12 TCP bytes as a position packet.
                start_idx = rsp_data.find(MD01Msg.START_BYTE)
                if start_idx < 0:
                    discarded_data.extend(rsp_data)
                    rsp_data.clear()
                elif start_idx > 0:
                    discarded_data.extend(rsp_data[:start_idx])
                    del rsp_data[:start_idx]

                if len(rsp_data) >= 12:
                    candidate = bytes(rsp_data[:12])
                    position_indices = (1, 2, 3, 4, 6, 7, 8, 9)
                    if (
                        candidate[-1:] == MD01Msg.END_BYTE
                        and all(candidate[idx] <= 9 for idx in position_indices)
                    ):
                        rsp_data = bytearray(candidate)
                        break

                    # This 0x57 was not the start of a valid response. Discard
                    # it and continue searching for the next start marker.
                    discarded_data.append(rsp_data[0])
                    del rsp_data[0]
                    continue

                chunk = sock.recv(12 - len(rsp_data))
                if not chunk:
                    break
                rsp_data.extend(chunk)
        except socket.timeout as e:
            raise XTimeoutWaitingForResponse(
                f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} "
                f"timed-out waiting for rsp after sending command {md01_cmd}."
            ) from e
        finally:
            sock.close()
        
        if len(rsp_data) == 0 and not discarded_data:
            raise XTimeoutWaitingForResponse(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} timed-out waiting for rsp." + \
                f" No data received after sending command {md01_cmd}.")

        if len(rsp_data) != 12:
            raise XTimeoutWaitingForResponse(
                f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} did not receive a valid "
                f"12-byte MD01 response after sending command {md01_cmd}. "
                f"Discarded data: {bytes(discarded_data)!r}; remaining data: {bytes(rsp_data)!r}."
            )

        if discarded_data:
            logger.warning(
                f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} discarded "
                f"{len(discarded_data)} unexpected byte(s) before MD01 response: {bytes(discarded_data)!r}"
            )
        
        # Decode response data (bytes) to MD01Msg
        md01_rsp = MD01Msg()
        md01_rsp.from_data(bytes(rsp_data))
        logger.debug(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} received response from MD01 controller:\n{md01_rsp}")
        self._trace_md01_message("RX", md01_rsp)
        return md01_rsp

    def _trace_md01_message(self, direction: str, msg: MD01Msg) -> None:
        """Trace controller traffic without allowing diagnostics to affect I/O."""
        trace = getattr(self, "trace", None)
        if trace is None:
            return

        dish_id = getattr(getattr(self, "dsh_model", None), "dsh_id", "unknown")
        interface = (
            f"MD01 {dish_id} "
            f"({self.md01_config.host}:{self.md01_config.port})"
        )
        try:
            trace.log_message(direction, msg, interface)
        except Exception as e:
            logger.warning(
                "MD01Driver for controller %s %s failed to trace %s message: %s",
                self.md01_config.host,
                self.md01_config.port,
                direction,
                e,
            )

    def _get_md01_altaz(self) -> Tuple[float, float]:
        """Returns the current altitude and azimuth of the dish as a tuple of decimal numbers [degrees]."""

        md01_cmd = MD01Msg()
        md01_cmd.set_cmd(MD01Msg.CMD_STATUS)

        rsp = self._send_md01_command(md01_cmd)

        if rsp is not None:
            # Apply inverse offsets from config
            talt, taz = self._offset_inv_corr(rsp.alt, rsp.az)
            return talt, taz
        
        return None, None

    def _set_md01_altaz(self, alt:float, az:float):
        """Sets the MD-01 to the specified altitude and azimuth position.

            :param alt: The target altitude to set the MD-01 to.
            :param az: The target azimuth to set the MD-01 to.
            :raises XBase: If there is an error sending the command.
        """
        #Apply offsets from config
        talt, taz = self._offset_corr(alt, az)
        
        #Ensure within limits
        if talt < self.md01_config.min_alt:
            talt = self.md01_config.min_alt
        if talt > self.md01_config.max_alt:
            talt = self.md01_config.max_alt

        md01_cmd = MD01Msg()
        md01_cmd.set_cmd(MD01Msg.CMD_SET)
        md01_cmd.set_position(talt, taz)

        command_data = md01_cmd.to_data()
        if command_data == getattr(self, "_last_set_command_data", None):
            logger.debug(
                f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} "
                f"skipping duplicate quantised SET command for Alt: {talt}, Az: {taz}."
            )
            return

        self._send_md01_command(md01_cmd)
        # Cache only an acknowledged command. If both attempts time out, the
        # position remains eligible to be sent on the next driver cycle.
        self._last_set_command_data = command_data

    def _stop_md01(self):
        """Stops any movement of the telescope 
            :raises XBase: If there is an error sending the command.
        """
        md01_cmd = MD01Msg()
        md01_cmd.set_cmd(MD01Msg.CMD_STOP)
        # A later SET to the same coordinates must not be suppressed after a
        # stop, even if this STOP's response is lost after reaching the mount.
        self._last_set_command_data = None
        rsp = self._send_md01_command(md01_cmd)

    def _offset_corr(self, alt, az):
        """Apply offset corrections to the given alt az coordinates in degrees."""
        alt_corr = alt + self.md01_config.offset_alt
        az_corr = az + self.md01_config.offset_az
        return (alt_corr, az_corr)

    def _offset_inv_corr(self, alt, az):
        """Apply inverse offset corrections to the given alt az coordinates in degrees."""
        alt_corr = alt - self.md01_config.offset_alt
        az_corr = az - self.md01_config.offset_az
        return (alt_corr, az_corr)

    def do_flip(self, alt, az, tracking: bool=False):
        """ Check if the desired alt az (degrees) is best reached via simple alt, az
            or flipping to 180-alt, az+180.
            :param alt: Target altitude in degrees.
            :param az: Target azimuth in degrees.
            :param tracking: If True, use tracking logic (currently not different).
            :return: True if flip is needed, False otherwise.
        """
        flip_alt = 180-alt
        flip_az = (az+180)%360

        # Check if directions are reachable
        origreach = self.can_reach(alt, az)
        flipreach = self.can_reach(flip_alt, flip_az)

        # If flip direction cannot be reached, return original one.
        # (even if it may also not be reached)
        if not flipreach:
            return False
            
        # But if flip direction can be reached, but not original one,
        # then we have to flip to point to this position
        elif flipreach and (not origreach):
            return True

        # For tracking, use original direction (avoid unnecessary flips)
        elif tracking:
            return False  

        # If both directions are valid, which is the most common case,
        # then we find the closest one (in azimuth driving, not in angular distance)
        # to the current pointing
        elif flipreach and origreach:

            (calt, caz) = self._get_md01_altaz()
            flip_dist = util.get_azimuth_distance(caz, flip_az)
            orig_dist = util.get_azimuth_distance(caz, az)
            if flip_dist < orig_dist:
                return True
            else:
                return False

    def can_reach(self, alt, az):
        """Check if telescope can reach this position. Altitude and azimuth input in degrees.
        
        All directions might not be possible due to telescope mechanics. Also,
        some angles such as pointing towards the earth, are not reachable due to limits. 

        This function will shift the given coordinates to a local range for doing the
        comparison, since the local azimuth might be negative in the telescope
        configuration."""

        (alt, az) = self._offset_corr(alt,az)
        if (alt > self.md01_config.max_alt or alt < self.md01_config.min_alt):
            alt = round(alt, 2)
            logger.debug(f"MD01Driver for controller {self.md01_config.host} {self.md01_config.port} cannot reach altitude {alt} deg.")
            return False
        return True

# Runs tests using: pytest dsh/drivers/md01/md01_driver.py -v
# -v for verbose output (or -vv or -vvv for more verbosity)
# -s to show print output

def test_can_reach(md01_driver):
    assert md01_driver.can_reach(45.0, 180.0) == True
    assert md01_driver.can_reach(-10.0, 180.0) == False
    assert md01_driver.can_reach(100.0, 180.0) == False
    assert md01_driver.can_reach(90.0, 360.0) == True
    assert md01_driver.can_reach(90.0, 361.0) == True

def test_do_flip(md01_driver):
    assert md01_driver.do_flip(100.0, 180.0) == True
    assert md01_driver.do_flip(45.0, 180.0) == False
    assert md01_driver.do_flip(90.0, 180.0) == True
    assert md01_driver.do_flip(91.0, 180.0) == True
    assert md01_driver.do_flip(89.9, 180.0) == False
    assert md01_driver.do_flip(89.9, 361.0) == False  

if __name__ == "__main__":

    md01_cfg = MD01Config(
        host="192.168.0.2",
        port=65000,
        stow_alt=90.0,
        stow_az=0.0,
        offset_alt=0.0,
        offset_az=0.0,
        min_alt=0.0,
        max_alt=90.0,
        resolution=0.1,      # degrees / step
        rotation_speed=2.5,  # degrees / sec
        rate_limit=1.0,      # msgs / sec
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
        driver_type=DriverType.MD01,
        driver_config=md01_cfg,
        last_update=datetime.now(timezone.utc)
    )

    md01_driver = MD01Driver(dsh_model=dish002)

    dist = util.get_angular_distance(35.25, 325.75, 40.25, 330.75)

    md01_driver._stop_md01()
    alt, az = md01_driver._get_md01_altaz()
    print(f"Current Altitude: {alt} degrees, Azimuth: {az} degrees")

    import astropy.units as u
    from astropy.coordinates import EarthLocation, AltAz, SkyCoord

    now = Time(datetime.now(timezone.utc))
    frame = AltAz(obstime=now, location=md01_driver.location)

    m33 = SkyCoord.from_name("M33")
    m33_altaz = m33.transform_to(AltAz(obstime=now, location=md01_driver.location))
    print(f"Altitude: {m33_altaz.alt:.2f}, Azimuth: {m33_altaz.az:.2f}")
    md01_driver._set_md01_altaz(m33_altaz.alt.degree, m33_altaz.az.degree)

    while True:
        alt, az = md01_driver._get_md01_altaz()
        print(f"Slewing... Current Altitude: {alt} degrees, Azimuth: {az} degrees")
        if (abs(alt - m33_altaz.alt.degree) <= md01_cfg.resolution and
            abs(az - m33_altaz.az.degree) <= md01_cfg.resolution):
            print("Slew complete.")
            break
        time.sleep(1)
    
    alt, az = md01_driver._get_md01_altaz()
    print(f"After Slew - Current Altitude: {alt} degrees, Azimuth: {az} degrees")
