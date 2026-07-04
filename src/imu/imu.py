import logging
import os
import numpy as np

import time
import datetime
import argparse
from pathlib import Path
import sys
import threading

SCRIPT_DIR = Path(__file__).resolve().parent
SRC_DIR = SCRIPT_DIR.parent

while str(SCRIPT_DIR) in sys.path:
    sys.path.remove(str(SCRIPT_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from models.comms import CommunicationStatus
from models.imu import IMUData, IMUDeviceList, IMUDeviceModel, IMUDriverType
from imu.drivers.driver import create_imu_driver

# IMU = Inertial Measurement Unit

# Default Qt platform to avoid Wayland activation warnings on Pi GUI setups.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Or DEBUG for more verbosity
    format='%(asctime)s %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

MAX_HISTORY = 1000  # Store last X IMU-derived pointing readings

class IMU:
    """ Class to interface with an IMU device.
        The IMU provides acceleration, angular velocity, angle (roll, pitch, yaw), magnetic vector, temperature, and quaternion data.
        The IMU is connected via a serial port (e.g. /dev/ttyUSB0 or COM3).
        The class supports calibration of altitude and azimuth offsets.

        The IMU class subscribes to IMU messages and updates its internal state via a callback function.
    """

    def __init__(
            self,
            imu_device: IMUDeviceModel,
            imu_device_list: IMUDeviceList = None,
            profile="default",
            config_root="config"):
        """ Initialize the IMU class with the given parameters.
        
            Parameters
                imu_device: IMUDeviceModel containing driver, connection and alt/az calibration configuration
                profile: configuration profile under config_root
                config_root: root configuration directory
        """
        global cal_key_press

        self.profile = profile
        self.config_root = Path(config_root)
        self.config_dir = self.config_root / profile

        if imu_device is None:
            raise ValueError("IMU requires an IMUDeviceModel instance.")
        if not isinstance(imu_device, IMUDeviceModel):
            raise TypeError(f"IMU requires IMUDeviceModel, got {type(imu_device).__name__}")

        self.imu_device_list = imu_device_list
        self.imu_device = imu_device
        self.connected = CommunicationStatus.NOT_ESTABLISHED

        # Altitude and azimuth offsets for calibration.
        self.az_offset = max(-180.0, min(180.0, float(self.az_offset))) # Yaw\Roll -180 to 180 deg adjustment
        self.alt_offset = max(-90.0, min(90.0, float(self.alt_offset))) # Roll\Pitch -90 to 90 deg adjustment

        self.imu_data = IMUData(imu_id=self.imu_id)
        self.driver = create_imu_driver(self.imu_device)
        if self.driver is not None:
            self.driver.set_data_callback(self.callback)

        logging.info(
            "Initializing IMU with driver_type=%s, device=%s, baudrate=%s",
            self.driver_type.name,
            self.device,
            self.baudrate,
        )

        self.angle_hist = np.zeros((MAX_HISTORY, 6))  # Store last MAX_HISTORY angle readings (timestamp, roll, pitch, yaw, altitude, azimuth)
        self._lock = threading.Lock()  # Lock for thread-safe access to shared resources (angle_hist numpy array)

    def _is_connected(self):
        return self.connected == CommunicationStatus.ESTABLISHED

    @staticmethod
    def _coerce_driver_type(driver_type):
        if isinstance(driver_type, IMUDriverType):
            return driver_type
        if isinstance(driver_type, str):
            normalized = driver_type.strip().upper()
            return IMUDriverType[normalized]
        return IMUDriverType(driver_type)

    @staticmethod
    def _vector_or_none(value, length):
        if value is None:
            return None
        if len(value) != length:
            raise ValueError(f"Expected vector length {length}, got {len(value)}")
        if all(v is None for v in value):
            return None
        return [float(v) for v in value]

    @staticmethod
    def _empty_vector(value, length):
        return tuple(value) if value is not None else tuple(None for _ in range(length))

    @property
    def imu_id(self):
        return self.imu_device.imu_id

    @property
    def device(self):
        return getattr(self.driver_config, "device", None)

    @device.setter
    def device(self, value):
        if self.driver_config is None or not hasattr(self.driver_config, "device"):
            raise AttributeError(f"IMU driver config does not support device for type {self.driver_type.name}")
        self.driver_config.device = value

    @property
    def baudrate(self):
        return getattr(self.driver_config, "baudrate", None)

    @baudrate.setter
    def baudrate(self, value):
        if self.driver_config is None or not hasattr(self.driver_config, "baudrate"):
            raise AttributeError(f"IMU driver config does not support baudrate for type {self.driver_type.name}")
        self.driver_config.baudrate = int(value)

    @property
    def refresh_rate(self):
        return getattr(self.driver_config, "refresh_rate", None)

    @refresh_rate.setter
    def refresh_rate(self, value):
        if self.driver_config is None or not hasattr(self.driver_config, "refresh_rate"):
            raise AttributeError(f"IMU driver config does not support refresh_rate for type {self.driver_type.name}")
        self.driver_config.refresh_rate = int(value)

    @property
    def connected(self):
        return self.imu_device.imu_connected

    @connected.setter
    def connected(self, value):
        self.imu_device.imu_connected = value
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def driver_type(self):
        return self.imu_device.driver_type

    @driver_type.setter
    def driver_type(self, value):
        self.imu_device.driver_type = self._coerce_driver_type(value)
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def driver_config(self):
        return self.imu_device.driver_config

    @driver_config.setter
    def driver_config(self, value):
        self.imu_device.driver_config = value
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def az_vector(self):
        return self.imu_device.az_vector

    @az_vector.setter
    def az_vector(self, value):
        self.imu_device.az_vector = value
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def alt_vector(self):
        return self.imu_device.alt_vector

    @alt_vector.setter
    def alt_vector(self, value):
        self.imu_device.alt_vector = value
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def az_offset(self):
        return self.imu_device.az_offset

    @az_offset.setter
    def az_offset(self, value):
        self.imu_device.az_offset = float(value)
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def alt_offset(self):
        return self.imu_device.alt_offset

    @alt_offset.setter
    def alt_offset(self, value):
        self.imu_device.alt_offset = float(value)
        self.imu_device.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def acceleration(self):
        return self._empty_vector(self.imu_data.acceleration, 3)

    @acceleration.setter
    def acceleration(self, value):
        self.imu_data.acceleration = self._vector_or_none(value, 3)
        self.imu_data.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def angular_vel(self):
        return self._empty_vector(self.imu_data.angular_vel, 3)

    @angular_vel.setter
    def angular_vel(self, value):
        self.imu_data.angular_vel = self._vector_or_none(value, 3)
        self.imu_data.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def angle(self):
        return self._empty_vector(self.imu_data.angle, 3)

    @angle.setter
    def angle(self, value):
        self.imu_data.angle = self._vector_or_none(value, 3)
        self.imu_data.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def magnetic_vector(self):
        return self._empty_vector(self.imu_data.magnetic_vector, 3)

    @magnetic_vector.setter
    def magnetic_vector(self, value):
        self.imu_data.magnetic_vector = self._vector_or_none(value, 3)
        self.imu_data.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def temp_celsius(self):
        return self.imu_data.temp_celsius

    @temp_celsius.setter
    def temp_celsius(self, value):
        self.imu_data.temp_celsius = None if value is None else float(value)
        self.imu_data.last_update = datetime.datetime.now(datetime.timezone.utc)

    @property
    def timestamp(self):
        return self.imu_data.last_update

    @timestamp.setter
    def timestamp(self, value):
        self.imu_data.last_update = value

    @property
    def quaternion(self):
        return self._empty_vector(self.imu_data.quaternion, 4)

    @quaternion.setter
    def quaternion(self, value):
        self.imu_data.quaternion = self._vector_or_none(value, 4)
        self.imu_data.last_update = datetime.datetime.now(datetime.timezone.utc)

    def connect(self):
        """ Connect to the IMU device and start receiving data.
            Returns True if connection is successful, False otherwise.
        """

        if self._is_connected():
            logging.warning("IMU is already connected.")
            return True

        if self.driver is None:
            logging.error("No IMU driver configured.")
            return False

        try:
            self.driver.connect()
            self.imu_data = self.driver.get_imu_data()
            logging.info("Connected to IMU %s using %s driver.", self.imu_id, self.driver_type.name)
        except Exception as e:
            logging.error(f"Failed to connect to IMU: {e}")
            self.connected = CommunicationStatus.NOT_ESTABLISHED

        return self._is_connected()

    def get_acceleration(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.acceleration

    def get_angular_velocity(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.angular_vel

    def get_angle(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.angle

    def get_roll(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.angle[0] 

    def get_pitch(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.angle[1] 

    def get_yaw(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.angle[2] 

    def _get_altitude(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None

        if self.alt_vector == "roll":
            angle = self.angle[0]
        elif self.alt_vector == "pitch":
            angle = self.angle[1]
        else:
            logging.error(f"Invalid alt_vector: {self.alt_vector}. Must be 'roll' or 'pitch'.")
            return None

        return angle_to_altitude(angle, self.alt_offset)

    def _get_azimuth(self, flip_az=False):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None

        if self.az_vector == "yaw":
            angle = self.angle[2]
        elif self.az_vector == "roll":
            angle = self.angle[0]
        else:
            logging.error(f"Invalid az_vector: {self.az_vector}. Must be 'roll' or 'yaw'.")
            return None

        return yaw_to_azimuth(angle, self.az_offset, flip_az)

    def get_altaz(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None, None

        alt_result = self._get_altitude()
        if alt_result is None:
            return None, None

        alt, flip_az = alt_result
        az = self._get_azimuth(flip_az)

        if az is None:
            return None, None

        return alt, az

    def get_magnetic_vector(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.magnetic_vector

    def get_temperature(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.temp_celsius    

    def get_timestamp(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.timestamp

    def get_quaternion(self):
        if not self._is_connected():
            logging.warning("IMU not connected.")
            return None
        return self.quaternion

    def get_connected(self):
        return self._is_connected()

    def disconnect(self):
        if self.driver is not None:
            self.driver.disconnect()
            logging.info("Disconnected from IMU.")

    def __str__(self):
        return (f"\nIMU(device={self.device}, baudrate={self.baudrate},\n"
                f"timestamp={self.timestamp},\n"
                f"acceleration=X{self.acceleration[0]}, Y{self.acceleration[1]}, Z{self.acceleration[2]},\n"
                f"angular_velocity=X{self.angular_vel[0]}, Y{self.angular_vel[1]}, Z{self.angular_vel[2]},\n"
                f"angle=roll{self.angle[0]}, pitch{self.angle[1]}, yaw{self.angle[2]},\n"
                f"altaz=altitude={self._get_altitude()}, azimuth={self._get_azimuth()},\n"
                f"magnetic_vector=X{self.magnetic_vector[0]}, Y{self.magnetic_vector[1]}, Z{self.magnetic_vector[2]},\n"
                f"temperature={self.temp_celsius},\n"
                f"quaternion=X{self.quaternion[0]}, Y{self.quaternion[1]}, Z{self.quaternion[2]}, W{self.quaternion[3]})")

    def __del__(self):
        self.disconnect() 
        logging.info("IMU resources released.")

    def on_key_press(self, event):
        """ Callback function to handle a key press event during a calibration procedure. """
        global cal_key_press
        cal_key_press = True

    def calibrate(self):

        if not self._is_connected():
            logging.warning("IMU not connected.")
            return

        global cal_key_press
        cal_key_press = False  # Flag to indicate if the calibration procedure should continue

        # Plot angle history
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=(12, 6))
        ax = plt.subplot(111, polar=False)

        # Connect the key press event of the calibration plot to a callback function
        fig.canvas.mpl_connect('key_press_event', self.on_key_press)

        # Ask the user to point the IMU device vertically at the Zenith
        logger.info(f"Please point the IMU device vertically at the Zenith, press a key when ready")

        while (not cal_key_press):

            # Find first valid index where timestamp is not zero
            try:
                ax.cla()

                ax.set_xlabel('Time')
                ax.set_ylabel('Angle (degrees)')
                ax.set_title('IMU Angle History')
                ax.grid()

                first_idx = np.where(self.angle_hist[:,0]!=0)[0][0]
                ax.set_xlim(self.angle_hist[first_idx, 0], self.angle_hist[-1, 0])

                with self._lock:
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 1], label=f'Roll {self.get_roll():.2f}')
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 2], label=f'Pitch {self.get_pitch():.2f}')
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 3], label=f'Yaw {self.get_yaw():.2f}')
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 4], label=f'Altitude {self._get_altitude()[0]:.2f}')
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 5], label=f'Azimuth {self._get_azimuth():.2f}')

                ax.legend(loc='upper right')

            except IndexError:
                pass

            plt.pause(0.1)  # Pause to allow the plot to update

        alt_angle = self.get_roll() if self.alt_vector == "roll" else self.get_pitch()
        if alt_angle is None:
            logger.error("Cannot calibrate altitude offset, vector angle is None.")
            return
        self.alt_offset = 90 - alt_angle
        logger.info(f"Calibrating altitude offset to {self.alt_offset} degrees")
        
        cal_key_press = False

        # Connect the key press event of the calibration plot to a callback function
        fig.canvas.mpl_connect('key_press_event', self.on_key_press)

        # Ask the user to point the IMU device horizontally at True North
        logger.info(f"Please point the IMU device horizontally at True North, press a key when ready")

        while (not cal_key_press):

            # Find first valid index where timestamp is not zero
            try:
                ax.cla()

                ax.set_xlabel('Time')
                ax.set_ylabel('Angle (degrees)')
                ax.set_title('IMU Angle History')
                ax.grid()

                first_idx = np.where(self.angle_hist[:,0]!=0)[0][0]
                ax.set_xlim(self.angle_hist[first_idx, 0], self.angle_hist[-1, 0])

                with self._lock:
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 1], label=f'Roll {self.get_roll():.2f}')
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 2], label=f'Pitch {self.get_pitch():.2f}')
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 3], label=f'Yaw {self.get_yaw():.2f}')
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 4], label=f'Altitude {self._get_altitude()[0]:.2f}')
                    ax.plot(self.angle_hist[:, 0], self.angle_hist[:, 5], label=f'Azimuth {self._get_azimuth():.2f}')

                ax.legend(loc='upper right')

            except IndexError:
                pass
            plt.pause(0.1)  # Pause to allow the plot to update

        az_angle = self.get_yaw() if self.az_vector == "yaw" else self.get_roll()
        if az_angle is None:
            logger.error("Cannot calibrate azimuth offset, vector angle is None.")
            return

        self.az_offset = az_angle % 360
        logger.info(f"Calibrating azimuth offset to {self.az_offset} degrees")
        plt.close('all')

        self.save_imu_device_list()

    def save_imu_device_list(self):
        if self.imu_device_list is None:
            self.imu_device_list = IMUDeviceList(list_id=self.profile, imu_list=[self.imu_device])
        self.imu_device_list.last_update = datetime.datetime.now(datetime.timezone.utc)
        self.imu_device_list.save_to_disk(output_dir=str(self.config_dir), filename="IMUDeviceList.json")
        logger.info("Saved IMUDeviceList.json to profile directory %s", self.config_dir)

    def callback(self, imu_data):

        try:

            if not isinstance(imu_data, IMUData):
                raise TypeError(f"IMU callback expected IMUData, got {type(imu_data).__name__}")

            self.imu_data = imu_data

            alt, az = self.get_altaz()

            # Update angle history by obtaining the current thread lock first
            # Numpy arrays (angle_hist) are not inherently thread-safe
            with self._lock:
                self.angle_hist = np.roll(self.angle_hist, shift=-1, axis=0)
                self.angle_hist[-1] = (self.timestamp.timestamp(), self.angle[0], self.angle[1], self.angle[2], alt, az)

        except Exception as e:
            logging.error(f"Error processing IMU message: {e}")

def yaw_to_azimuth(yaw, az_offset=0.0, flip_az=False):
    """ Convert yaw angle to azimuth angle.
        Yaw is the angle of rotation around the vertical axis.
        Yaw is positive in the counter-clockwise direction 0-180 deg.
        Yaw is negative in the clockwise direction 0-180 deg.
        Azimuth is positive 0-360 degrees measured clockwise from true north.
    """
    if yaw is None:
        return None

    # Ensure azimuth offset is within 0 to 360 degrees
    az_offset = 0.0 if az_offset is None else az_offset % 360.0 

    # If yaw is outside its normal range
    if yaw > 180.0 or yaw < -180.0:
        yaw = (yaw % 180.0) - 180.0 # Ensure yaw is within -180 to 180 degrees

    # Convert yaw to azimuth
    azimuth = 360.0 - yaw if yaw > 0.0 else -yaw
    # Adjust azimuth with offset
    azimuth += az_offset 

    return (azimuth + 180.0) % 360.0 if flip_az else azimuth % 360.0

def angle_to_altitude(angle, alt_offset=0.0):
    """ Convert pitch or roll angle to altitude angle.
        Roll is the angle of rotation around the front-to-back axis.
        Pitch is the angle of rotation around the side-to-side axis.
        Roll/Pitch is positive in the counter-clockwise direction 0-180 deg.
        Roll/Pitch is negative in the clockwise direction 0-180 deg.
        Altitude ranges between -90 and 90 degrees.
    """
       
    if angle is None:
        return None

    # Ensure altitude offset is within -90 to 90 degrees
    alt_offset = 0.0 if alt_offset is None else alt_offset if alt_offset >= -90.0 and alt_offset <= 90.0 else None

    if alt_offset is None:
        raise ValueError(f"Altitude offset must be between -90 and 90 degrees. Offset provided: {alt_offset}")
    else:
        angle += alt_offset

    # If angle is outside its normal range
    if angle > 180.0 or angle < -180.0:
        angle = (angle % 180.0) - 180.0 # Ensure angle is within -180 to 180 degrees

    flip_az = False

    # Convert angle to altitude
    if angle > 90.0:
        angle = 180.0 - angle # azimuth must flip 180 degrees
        flip_az = True
    elif angle < -90.0:
        angle = -180.0 - angle # azimuth must flip 180 degrees
        flip_az = True

    return max(-90.0, min(90.0, angle)), flip_az

def get_profile_config_dir(profile="default", config_root="config"):
    return Path(config_root) / profile

def load_imu_device_list(profile="default", config_root="config"):
    config_dir = get_profile_config_dir(profile, config_root)
    try:
        imu_list = IMUDeviceList.load_from_disk(input_dir=str(config_dir), filename="IMUDeviceList.json")
        logger.info("Loaded IMUDeviceList.json from profile directory %s", config_dir)
        return imu_list
    except FileNotFoundError:
        logger.warning("IMUDeviceList.json not found in %s, using an empty IMU device list.", config_dir)
        return IMUDeviceList(list_id=profile)

def main():

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Inertial Motion Unit (IMU)")
    parser.add_argument('--imu-id', type=str, default='imu001', help='Unique IMU model identifier.')
    parser.add_argument("--profile", type=str, default="default", help="Configuration profile under ./config, e.g. default or jodrell.")
    parser.add_argument("--config-root", type=str, default="config", help="Root directory containing configuration profiles.")
    parser.add_argument('--qt-platform', type=str, default=os.environ.get("QT_QPA_PLATFORM", "xcb"), help='Qt platform plugin (e.g. xcb, wayland, offscreen).')
    args = parser.parse_args()

    if args.qt_platform:
        os.environ["QT_QPA_PLATFORM"] = args.qt_platform

    imu_device_list = load_imu_device_list(profile=args.profile, config_root=args.config_root)
    imu_device = imu_device_list.get_imu_by_id(args.imu_id)
    if imu_device is None:
        logging.error("IMU %s not found in profile %s IMUDeviceList.json.", args.imu_id, args.profile)
        return

    imu = IMU(
        imu_device=imu_device,
        imu_device_list=imu_device_list,
        profile=args.profile,
        config_root=args.config_root,
    )
    if not imu.connect():
        logging.error("Failed to connect to IMU, exiting...")
        return

    imu.calibrate()

    try:
        while True:

            alt, az = imu.get_altaz()
            temp = imu.get_temperature()
            logging.info(f"Altitude: {alt}, Azimuth: {az}")
            logging.info(f"Roll: {imu.get_roll()}, Pitch: {imu.get_pitch()}, Yaw: {imu.get_yaw()}")
            logging.info(f"Temperature: {temp}")
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt...")
    finally:
        imu.disconnect()

    # Find first valid index where timestamp is not zero
    first_idx = np.where(imu.angle_hist[:,0]!=0)[0][0]

    # Plot angle history
    import matplotlib.pyplot as plt

    plt.figure(figsize=(12, 6))
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 1], label='Roll')
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 2], label='Pitch')
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 3], label='Yaw')
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 4], label='Altitude')
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 5], label='Azimuth')
    plt.xlabel('Time')
    plt.ylabel('Angle (degrees)')
    # Limit x axis
    plt.xlim(imu.angle_hist[first_idx, 0], imu.angle_hist[-1, 0])
    plt.title('IMU Angle History')
    plt.legend()
    plt.grid()
    plt.pause(0.1)

if __name__ == "__main__":
    main()
