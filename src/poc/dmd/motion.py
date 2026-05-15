import logging
import os
import subprocess
import numpy as np

import time
import datetime
import sys
import argparse
import inspect
import json
import threading

import witmotion

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

MAX_HISTORY = 1000  # Store last X motion readings

class Motion:
    """ Class to interface with a WitMotion IMU device.
        The IMU provides acceleration, angular velocity, angle (roll, pitch, yaw), magnetic vector, temperature, and quaternion data.
        The IMU is connected via a serial port (e.g. /dev/ttyUSB0 or COM3).
        The class supports calibration of altitude and azimuth offsets.

        The Motion class subscribes to IMU messages and updates its internal state via a callback function.
    """

    def __init__(self, device='auto', baudrate=9600):
        """ Initialize the Motion class with the given parameters.
        
            Parameters
                device: serial device identifier (e.g. /dev/ttyUSB0 or COM3). Use 'auto' to automatically detect the first available IMU device.
                baudrate: baud rate for the serial connection (default: 9600)
        """
        global cal_key_press

        if device == 'auto':
            # Automatically detect and use the first available IMU device
            device = self.auto_detect_imu()
        
        self.device = device
        self.baudrate = baudrate
        self.imu = None
        self.connected = False

        logging.info(f"Initializing Motion with device: {self.device}, baudrate: {self.baudrate}")

        self.acceleration = (None, None, None)
        self.angular_vel = (None, None, None)
        self.angle = (None, None, None)
        self.magnetic_vector = (None, None, None)
        self.temp_celsius = None
        self.timestamp = None
        self.quaternion = (None, None, None, None)

        # Altitude vector can be either "roll" or "pitch" depending on IMU orientation
        # Azimuth vector can be either "yaw" or "roll" depending on IMU orientation
        self.alt_vector = "roll" # or "pitch"
        self.az_vector = "yaw" # or "roll"

         # Default azimuth and altitude offsets
        self.az_offset = 0.0    # degrees
        self.alt_offset = 0.0   # degrees
        
        # Update rate in Hz (how often to read from the IMU)
        self.update_rate = 1.0  # Hz

        # Read a config file called motion_config.json
        try:
            with open("motion_config.json", "r") as f:
                
                config = json.load(f)

                self.az_vector = config.get("az_vector", self.az_vector)
                self.az_offset = config.get("az_offset", self.az_offset)
                self.alt_vector = config.get("alt_vector", self.alt_vector)
                self.alt_offset = config.get("alt_offset", self.alt_offset)
                self.update_rate = config.get("update_rate", self.update_rate)

                logging.info(f"Loaded motion_config.json: "+\
                    f"az_vector={self.az_vector}, " +\
                    f"az_offset={self.az_offset}, " +\
                    f"alt_vector={self.alt_vector}, " +\
                    f"alt_offset={self.alt_offset}, " +\
                    f"update_rate={self.update_rate}")

        except FileNotFoundError:
            logging.warning("motion_config.json not found, using default offsets.")
        except json.JSONDecodeError:
            logging.error("Error decoding motion_config.json, using default offsets.")

        # Altitude and Azimuth offsets for calibration
        self.az_offset = max(-180.0, min(180.0, self.az_offset)) # Yaw\Roll -180 to 180 deg adjustment
        self.alt_offset = max(-90.0, min(90.0, self.alt_offset)) # Roll\Pitch -90 to 90 deg adjustment

        self.angle_hist = np.zeros((MAX_HISTORY, 6))  # Store last MAX_HISTORY angle readings (timestamp, roll, pitch, yaw, altitude, azimuth)
        self._lock = threading.Lock()  # Lock for thread-safe access to shared resources (angle_hist numpy array)

    def connect(self):
        """ Connect to the IMU device and start receiving data.
            Returns True if connection is successful, False otherwise.
        """

        if self.connected:
            logging.warning("IMU is already connected.")
            return True

        try:
            self.imu = witmotion.IMU(self.device, self.baudrate)
            self.connected = True
            
            self.imu.set_update_rate(1.0) # Set update rate to 1 Hz
            self.imu.subscribe(self.callback) # Set callback for incoming messages

            logging.info(f"Connected to IMU on {self.device} at {self.baudrate} baud.")

        except Exception as e:
            logging.error(f"Failed to connect to IMU: {e}")
            self.connected = False

        return self.connected

    def auto_detect_imu(self):
        """ Automatically detect the IMU by listing devices as 'ls /dev/tty* | grep usbserial'
            The first entry in the result set is used as the IMU device.
            Returns the device identifier string if found, None otherwise, e.g. /dev/ttyUSB0
        """
        result = subprocess.run(r"ls /dev/tty* | grep -E '(usbserial|ttyUSB)'", shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            logging.error(f"Error trying to detect IMU device: {result.stderr}")
        else:
            # Parse the output and return the first available device
            devices = result.stdout.splitlines()
            if len(devices) > 0:
                return devices[0]
            else:
                logging.warning("No IMU devices found.")
                return None

    def get_acceleration(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.acceleration

    def get_angular_velocity(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.angular_vel

    def get_angle(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.angle

    def get_roll(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.angle[0] 

    def get_pitch(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.angle[1] 

    def get_yaw(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.angle[2] 

    def _get_altitude(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None

        if self.alt_vector == "roll":
            angle = self.angle[0]
        elif self.alt_vector == "pitch":
            angle = self.angle[1]
        elif self.alt_vector == "yaw":
            angle = self.angle[2]
        else:
            logging.error(f"Invalid alt_vector: {self.alt_vector}. Must be 'roll', 'pitch', or 'yaw'.")
            return None

        return angle_to_altitude(angle, self.alt_offset)

    def _get_azimuth(self, flip_az=False):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None

        if self.az_vector == "yaw":
            angle = self.angle[2]
        elif self.az_vector == "pitch":
            angle = self.angle[1]
        elif self.az_vector == "roll":
            angle = self.angle[0]
        else:
            logging.error(f"Invalid az_vector: {self.az_vector}. Must be 'roll', 'pitch', or 'yaw'.")
            return None

        return yaw_to_azimuth(angle, self.az_offset, flip_az)

    def get_altaz(self):
        if not self.connected:
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
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.magnetic_vector

    def get_temperature(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.temp_celsius    

    def get_timestamp(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.timestamp

    def get_quaternion(self):
        if not self.connected:
            logging.warning("IMU not connected.")
            return None
        return self.quaternion

    def get_connected(self):
        return self.connected

    def disconnect(self):
        if self.imu:
            self.imu.close()
            self.connected = False
            logging.info("Disconnected from IMU.")

    def __str__(self):
        return (f"\nMotion(device={self.device}, baudrate={self.baudrate},\n"
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

        if not self.connected:
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

        alt_angle = self.get_roll() if self.alt_vector == "roll" else self.get_pitch() if self.alt_vector == "pitch" else self.get_yaw()
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

        az_angle = self.get_yaw() if self.az_vector == "yaw" else self.get_roll() if self.az_vector == "roll" else self.get_pitch()
        if az_angle is None:
            logger.error("Cannot calibrate azimuth offset, vector angle is None.")
            return

        self.az_offset = az_angle % 360
        logger.info(f"Calibrating azimuth offset to {self.az_offset} degrees")
        plt.close('all')

        # Write the calibration offsets to the motion_config.json file
        with open('motion_config.json', 'w') as f:
            json.dump({
                "az_vector": self.az_vector,
                "az_offset": self.az_offset,
                "alt_vector": self.alt_vector,
                "alt_offset": self.alt_offset,
                "update_rate": self.update_rate
            }, f)

    def callback(self, msg):

        try:

            self.timestamp = datetime.datetime.now()

            if isinstance(msg, witmotion.protocol.MagneticMessage):
                self.magnetic_vector = msg.mag
            elif isinstance(msg, witmotion.protocol.AccelerationMessage):
                self.acceleration = msg.a
                self.temp_celsius = msg.temp_celsius
            elif isinstance(msg, witmotion.protocol.AngularVelocityMessage):
                self.angular_vel = msg.w
                self.temp_celsius = msg.temp_celsius
            elif isinstance(msg, witmotion.protocol.AngleMessage):
                self.angle = (msg.roll, msg.pitch, msg.yaw)
            elif isinstance(msg, witmotion.protocol.QuaternionMessage):
                self.quaternion = msg.q

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

def main():

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Inertial Motion Unit (IMU)")
    parser.add_argument('-imu', '--imu', type=str, help='IMU device identifier e.g. "/dev/tty.usbserial-1120"', required=True)
    parser.add_argument('-baud', '--baud', type=int, help='Baud rate for the IMU device e.g. 9600', default=9600)
    parser.add_argument('--qt-platform', type=str, default=os.environ.get("QT_QPA_PLATFORM", "xcb"), help='Qt platform plugin (e.g. xcb, wayland, offscreen).')
    args = parser.parse_args()

    if args.qt_platform:
        os.environ["QT_QPA_PLATFORM"] = args.qt_platform

    motion = Motion(args.imu, args.baud)
    if not motion.connect():
        logging.error("Failed to connect to IMU, exiting...")
        return

    motion.calibrate()

    try:
        while True:

            alt, az = motion.get_altaz()
            temp = motion.get_temperature()
            logging.info(f"Altitude: {alt}, Azimuth: {az}")
            logging.info(f"Roll: {motion.get_roll()}, Pitch: {motion.get_pitch()}, Yaw: {motion.get_yaw()}")
            logging.info(f"Temperature: {temp}")
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt...")
    finally:
        motion.disconnect()

    # Find first valid index where timestamp is not zero
    first_idx = np.where(motion.angle_hist[:,0]!=0)[0][0]

    # Plot angle history
    import matplotlib.pyplot as plt

    plt.figure(figsize=(12, 6))
    plt.plot(motion.angle_hist[:, 0], motion.angle_hist[:, 1], label='Roll')
    plt.plot(motion.angle_hist[:, 0], motion.angle_hist[:, 2], label='Pitch')
    plt.plot(motion.angle_hist[:, 0], motion.angle_hist[:, 3], label='Yaw')
    plt.plot(motion.angle_hist[:, 0], motion.angle_hist[:, 4], label='Altitude')
    plt.plot(motion.angle_hist[:, 0], motion.angle_hist[:, 5], label='Azimuth')
    plt.xlabel('Time')
    plt.ylabel('Angle (degrees)')
    # Limit x axis
    plt.xlim(motion.angle_hist[first_idx, 0], motion.angle_hist[-1, 0])
    plt.title('IMU Angle History')
    plt.legend()
    plt.grid()
    plt.pause(0.1)

if __name__ == "__main__":
    main()