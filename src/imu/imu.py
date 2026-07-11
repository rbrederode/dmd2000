from abc import ABC, abstractmethod
import logging
import numpy as np

import datetime
from pathlib import Path
import threading
import time

from models.comms import CommunicationStatus
from models.imu import IMUData, IMUDeviceList, IMUDeviceModel, IMUDriverType
from imu.drivers.driver import create_imu_driver
from util.convert import angle_to_altitude, yaw_to_azimuth
from util.xbase import XSoftwareFailure

# IMU = Inertial Measurement Unit
logger = logging.getLogger(__name__)

MAX_HISTORY = 1000  # Store last X IMU-derived pointing readings
ANGLE_INDEX = {"roll": 0, "pitch": 1, "yaw": 2}


class IMUProvider(ABC):
    """Interface for objects that can provide current IMU data."""

    @abstractmethod
    def get_imu_data(self) -> IMUData:
        """Return the most recent IMUData sample."""
        raise NotImplementedError


class IMU(IMUProvider):
    """ Class to interface with an IMU device.
        The IMU provides acceleration, angular velocity, angle (roll, pitch, yaw), magnetic vector, temperature, and quaternion data.
        The IMU is connected to a driver that handles the communication with the physical device.
        The IMU class subscribes to IMU messages and updates its internal state via a callback function.

        The IMU class maintains the current state of the IMU data and provides methods to access the data in a thread-safe manner.
        The IMU class also provides methods to convert the raw IMU data into altitude and azimuth angles based on the configured offsets and vectors.
    """

    def __init__(self, imu_device: IMUDeviceModel):
        """ Initialize the IMU class with the given parameters.
            Parameters
                imu_device: IMUDeviceModel containing driver, connection and alt/az calibration configuration
        """
        if imu_device is None or not isinstance(imu_device, IMUDeviceModel):
            raise ValueError(f"IMU requires an IMUDeviceModel instance, got {type(imu_device).__name__ if imu_device is not None else 'None'}")
  
        self.imu_device = imu_device
        self.imu_device.imu_connected = CommunicationStatus.NOT_ESTABLISHED

        # Altitude and azimuth offsets for calibration.
        self.imu_device.az_offset = float(self.imu_device.az_offset) % 360.0 # Compass offset in degrees
        self.imu_device.alt_offset = max(-90.0, min(90.0, float(self.imu_device.alt_offset))) # Roll\Pitch -90 to 90 deg adjustment

        self.driver = create_imu_driver(self.imu_device)
        if self.driver is not None:
            self.driver.set_data_callback(self.callback)
            logging.info(f"IMU initialised with driver_type={self.imu_device.driver_type.name}")
        else:
            logging.error(f"IMU failed to initialise IMU driver for driver_type={self.imu_device.driver_type.name}")
            raise XSoftwareFailure(f"IMU failed to initialise IMU driver for driver_type={self.imu_device.driver_type.name}")

        self.angle_hist = np.zeros((MAX_HISTORY, 6))  # Store last MAX_HISTORY angle readings (timestamp, roll, pitch, yaw, altitude, azimuth)
        self._lock = threading.Lock()  # Lock for thread-safe access to shared resources (angle_hist numpy array)

    @property
    def connected(self) -> bool:
        """ Return True if the IMU is connected, False otherwise. """
        return self.imu_device.imu_connected == CommunicationStatus.ESTABLISHED

    def is_connected(self) -> bool:
        return self.connected

    def connect(self):
        """ Connect to the IMU device and start receiving data.
            Returns True if connection is successful, False otherwise.
        """
        if self.connected:
            return True

        try:
            self.driver.connect()
            logging.info("IMU connected to %s using %s driver.", self.imu_device.imu_id, self.imu_device.driver_type.name)
        except Exception as e:
            logging.error(f"IMU failed to connect to %s using %s driver: {e}", self.imu_device.imu_id, self.imu_device.driver_type.name)
            self.imu_device.imu_connected = CommunicationStatus.NOT_ESTABLISHED

        return self.connected

    @property
    def imu_id(self) -> str:
        return self.imu_device.imu_id

    @property
    def alt_vector(self) -> str:
        return self.imu_device.alt_vector

    @property
    def az_vector(self) -> str:
        return self.imu_device.az_vector

    @property
    def alt_offset(self) -> float:
        return self.imu_device.alt_offset

    @alt_offset.setter
    def alt_offset(self, value):
        """Set the altitude offset in degrees. The offset must be between -90 and 90 degrees."""
        value = float(value)
        if not -90.0 <= value <= 90.0:
            raise ValueError(f"Altitude offset must be between -90 and 90 degrees. Offset provided: {value}")

        self.imu_device.alt_offset = value
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def az_offset(self) -> float:
        return self.imu_device.az_offset

    @az_offset.setter
    def az_offset(self, value):
        """Set the azimuth offset in degrees. The offset is normalized to be between 0 and 360 degrees."""
        self.imu_device.az_offset = float(value) % 360.0
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    def _angle_for_vector(self, imu_data: IMUData, vector_name: str, valid_vectors: set[str]):
        """ Return the angle for the given vector name from the IMUData object.
            If the vector name is not valid, log an error and return None.
            If the IMUData object is None or does not contain angle data, return None.
        """
        if vector_name not in valid_vectors:
            logging.error("Invalid IMU angle vector: %s. Must be one of %s.", vector_name, ", ".join(sorted(valid_vectors)))
            return None

        if imu_data is None or imu_data.angle is None:
            return None

        return imu_data.angle[ANGLE_INDEX[vector_name]]

    def get_altitude(self, imu_data=None) -> tuple:
        """ Returns a tuple containing the current altitude angle in degrees, adjusted by the configured offset.
            Also returns a boolean indicating whether the azimuth should be flipped by 180 degrees due to the altitude 
            being outside the normal range.

            Returns None if the IMU is not connected or if the altitude cannot be determined.
        """
        if imu_data is None:
            imu_data = self.imu_data

        angle = self._angle_for_vector(imu_data, self.imu_device.alt_vector, {"roll", "pitch"})
        if angle is None:
            return None

        return angle_to_altitude(angle, self.imu_device.alt_offset)

    def get_azimuth(self, imu_data=None, flip_az=False) -> float:
        """ Return the current azimuth angle in degrees, adjusted by the configured offset.
            Returns None if the IMU is not connected or if the azimuth cannot be determined.
        """
        if imu_data is None:
            imu_data = self.imu_data

        angle = self._angle_for_vector(imu_data, self.imu_device.az_vector, {"roll", "yaw"})
        if angle is None:
            return None

        return yaw_to_azimuth(angle, self.imu_device.az_offset, flip_az)

    def get_altaz(self, imu_data=None) -> tuple:
        """ Return the current altitude and azimuth angles as a tuple (altitude, azimuth).
            Returns (None, None) if the IMU is not connected or if the angles cannot be determined.
        """
        if imu_data is None:
            imu_data = self.imu_data

        alt_result = self.get_altitude(imu_data)
        if alt_result is None:
            return None, None

        alt, flip_az = alt_result
        az = self.get_azimuth(imu_data, flip_az=flip_az)

        if az is None:
            return None, None

        return alt, az

    def get_roll(self):
        return self._angle_for_vector(self.imu_data, "roll", {"roll"})

    def get_pitch(self):
        return self._angle_for_vector(self.imu_data, "pitch", {"pitch"})

    def get_yaw(self):
        return self._angle_for_vector(self.imu_data, "yaw", {"yaw"})

    def get_temperature(self):
        imu_data = self.imu_data
        return imu_data.temp_celsius if imu_data is not None else None

    @property
    def imu_data(self) -> IMUData:
        """ Return the most recent IMUData sample from the driver.
            Returns None if the IMU is not connected or if no data is available.
        """
        if not self.connected:
            logging.warning("IMU cannot get IMU data while not connected.")
            return None

        return self.driver.get_imu_data()

    def get_imu_data(self) -> IMUData:
        """Return the most recent IMUData sample from the driver."""
        return self.imu_data

    def disconnect(self):
        """ Disconnect from the IMU device and stop receiving data. """
        driver = getattr(self, "driver", None)
        if driver is not None:
            driver.disconnect()
            logging.info("IMU disconnected.")

    def __str__(self):
        return (f"IMU {self.imu_id} (connected={self.connected}, driver_type={self.imu_device.driver_type.name}, \
                altitude={self.altitude}, azimuth={self.azimuth})")

    def __del__(self):
        """ Destructor to ensure the IMU driver is disconnected when the IMU object is deleted. """
        try:
            driver = getattr(self, "driver", None)
            if driver is not None:
                driver.disconnect()
        except Exception:
            pass

    def callback(self, imu_data):
        """ Callback function to process new IMUData samples from the driver.
            This function is called by the driver whenever new IMU data is available.
            It updates the internal angle history and computes the current altitude and azimuth angles.
        """

        try:

            if not isinstance(imu_data, IMUData):
                raise TypeError(f"IMU callback expected IMUData, got {type(imu_data).__name__}")

            # Angle is a list of [roll, pitch, yaw] in degrees. If angle is None, we cannot compute altitude and azimuth.
            if imu_data.angle is None:
                return

            alt, az = self.get_altaz(imu_data)

            # Update angle history by obtaining the current thread lock first
            # Numpy arrays (angle_hist) are not inherently thread-safe
            timestamp = imu_data.last_update.timestamp() if isinstance(imu_data.last_update, datetime.datetime) else time.time()
            with self._lock:
                self.angle_hist = np.roll(self.angle_hist, shift=-1, axis=0)
                self.angle_hist[-1] = (timestamp, imu_data.angle[0], imu_data.angle[1], imu_data.angle[2], alt, az)

        except Exception as e:
            logging.error(f"Error processing IMU message: {e}")

def get_profile_config_dir(profile="default"):
    return Path("config") / profile

def load_imu_device_list(profile="default"):
    """ Load the IMUDeviceList from disk for the given profile.
        If the file does not exist, return an empty IMUDeviceList.
    """
    config_dir = get_profile_config_dir(profile)
    try:
        imu_list = IMUDeviceList.load_from_disk(input_dir=str(config_dir), filename="IMUDeviceList.json")
        logger.info("Loaded IMUDeviceList.json from profile directory %s", config_dir)
        return imu_list
    except FileNotFoundError:
        logger.warning("IMUDeviceList.json not found in %s, using an empty IMU device list.", config_dir)
        return IMUDeviceList(list_id=profile)

def main():
    from imu.calibrate import main as calibrate_main

    calibrate_main()

if __name__ == "__main__":
    main()
