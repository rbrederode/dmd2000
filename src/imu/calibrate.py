import argparse
import logging
import os
import select
import sys
import termios
import time
import tty
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from imu.imu import IMU, load_imu_device_list
from models.imu import IMUDeviceList
from util.format import fmt_angle

logger = logging.getLogger(__name__)

def _plot_angle_history(imu):
    """ Plot the IMU angle history in two subplots: yaw/roll/pitch and altitude/azimuth.
        Parameters:
            imu: IMU object
    """
    # Find the first index where the timestamp is non-zero to avoid plotting uninitialized data.
    first_idx = np.where(imu.angle_hist[:, 0] != 0)[0][0]

    # Create a figure with two subplots for the angle history.
    # Plot 1: Yaw, Roll, Pitch
    # Plot 2: Altitude and Azimuth
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharex=True)
    # Set titles, labels for the subplots and enable grid lines.
    _plot_angle_axes(imu, axes, first_idx)
    fig.tight_layout()
    plt.pause(0.1)

def _plot_angle_axes(imu, axes, first_idx):
    """ Plot the IMU angle history in two subplots: yaw/roll/pitch and altitude/azimuth.
        Parameters:
            imu: IMU object
            axes: list of matplotlib axes objects
            first_idx: index of the first non-zero timestamp in the angle history
    """
    raw_ax, pointing_ax = axes

    with imu._lock:
        angle_hist = imu.angle_hist.copy()

    # Clear the axes and set labels for the subplots.
    for ax in axes:
        ax.cla()
        ax.set_xlabel('Time (s)')
        ax.set_ylabel('Angle (degrees)')
        ax.grid()

    # Set titles for the subplots and adjust the x-axis limits based on the angle history timestamps.
    raw_ax.set_title('Yaw, Roll, Pitch')
    pointing_ax.set_title('Altitude and Azimuth')

    valid_hist = angle_hist[first_idx:]
    elapsed_seconds = valid_hist[:, 0] - valid_hist[0, 0]
    if elapsed_seconds[-1] != elapsed_seconds[0]:
        raw_ax.set_xlim(elapsed_seconds[0], elapsed_seconds[-1])

    alt, az = imu.get_altaz()
    roll = imu.get_roll()
    pitch = imu.get_pitch()
    yaw = imu.get_yaw()

    # Plot the angle history for yaw, roll, pitch, altitude, and azimuth with formatted labels.
    raw_ax.plot(elapsed_seconds, valid_hist[:, 3], label=f'Yaw {fmt_angle(yaw)}')
    raw_ax.plot(elapsed_seconds, valid_hist[:, 1], label=f'Roll {fmt_angle(roll)}')
    raw_ax.plot(elapsed_seconds, valid_hist[:, 2], label=f'Pitch {fmt_angle(pitch)}')
    pointing_ax.plot(elapsed_seconds, valid_hist[:, 4], label=f'Altitude {fmt_angle(alt)}')
    pointing_ax.plot(elapsed_seconds, valid_hist[:, 5], label=f'Azimuth {fmt_angle(az)}')

    raw_ax.legend(loc='upper right')
    pointing_ax.legend(loc='upper right')


def _plot_live_angle_history(imu, axes):
    """ Update the IMU angle history plots in real-time.
        Parameters:
            imu: IMU object
            axes: list of matplotlib axes objects
    """
    with imu._lock:
        first_idx = np.where(imu.angle_hist[:, 0] != 0)[0][0]

    _plot_angle_axes(imu, axes, first_idx)


def _wait_for_calibration_keypress(imu, fig, axes, plt, message):
    """Wait for a key press in the plot window or terminal before proceeding with calibration.
        Parameters:
            imu: IMU object
            fig: matplotlib figure object
            axes: list of matplotlib axes objects
            plt: matplotlib.pyplot module
            message: message to display while waiting for key press
    """
    key_pressed = False

    def on_key_press(event):
        nonlocal key_pressed
        key_pressed = True

    connection_id = fig.canvas.mpl_connect('key_press_event', on_key_press)
    old_terminal_settings = _set_terminal_cbreak()

    try:
        logger.info("%s (plot window or terminal)", message)

        while not key_pressed:
            if _terminal_key_pressed():
                key_pressed = True
                break

            try:
                _plot_live_angle_history(imu, axes)
            except (IndexError, TypeError, ValueError):
                pass
            plt.pause(0.1)
    finally:
        fig.canvas.mpl_disconnect(connection_id)
        _restore_terminal(old_terminal_settings)

def _set_terminal_cbreak():
    """Set the terminal to cbreak mode to detect key presses without waiting for Enter.
        Returns the old terminal settings to restore later.
    """
    # If stdin is not a TTY, return None to indicate that terminal settings cannot be changed.
    if not sys.stdin.isatty():
        return None

    old_settings = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())
    return old_settings

def _restore_terminal(old_settings):
    """Restore the terminal settings to their previous state."""
    if old_settings is not None:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

def _terminal_key_pressed():
    """Check if a key has been pressed in the terminal without blocking."""
    if not sys.stdin.isatty():
        return False

    # Use select to check if there's input available on stdin without blocking.
    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if not readable:
        return False

    sys.stdin.read(1)
    return True

def _calibration_angle(imu, vector_name, valid_vectors):
    """Calculate the angle for the specified vector (altitude or azimuth) during calibration.
        Parameters:
            imu: IMU object
            vector_name: name of the vector to calculate the angle for ('alt_vector' or 'az_vector')
            valid_vectors: set of valid vector names for the calculation
        Returns the calculated angle in degrees, or None if the angle cannot be determined.
    """
    angle = imu._angle_for_vector(imu.imu_data, vector_name, valid_vectors)
    if angle is None:
        logger.error("Cannot calibrate offset, vector angle is None.")
    return angle

def save_imu_device_list(imu, imu_device_list=None, profile="default"):
    """Save the updated IMUDeviceList to disk after calibration."""

    config_dir = Path("config") / profile
    if imu_device_list is None:
        imu_device_list = IMUDeviceList(list_id=profile, imu_list=[imu.imu_device])
    imu_device_list.last_update = datetime.now(timezone.utc)
    imu_device_list.save_to_disk(output_dir=str(config_dir), filename="IMUDeviceList.json")
    logger.info("Saved IMUDeviceList.json to profile directory %s", config_dir)

def calibrate_imu(imu, imu_device_list=None, profile="default"):
    """Calibrate the IMU device by calculating altitude and azimuth offsets based on user input.
        Parameters:
            imu: IMU object to calibrate
            imu_device_list: IMUDeviceList object to update with calibration results
            profile: configuration profile name for saving the updated IMUDeviceList
        Returns a tuple of (fig, axes) for the calibration plots, or (None, None) if calibration cannot proceed.
    """
    if not imu.connected:
        return None, None

    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharex=True)
    fig.suptitle("IMU Calibration")

    _wait_for_calibration_keypress(
        imu,
        fig,
        axes,
        plt,
        "Please point the IMU device vertically at the Zenith, press a key when ready",
    )

    alt_angle = _calibration_angle(imu, imu.alt_vector, {"roll", "pitch"})
    if alt_angle is None:
        return fig, axes

    alt_offset = 90 - alt_angle
    if not -90.0 <= alt_offset <= 90.0:
        logger.error(
            "Calculated altitude offset %s degrees is outside the supported -90 to 90 degree range. "
            "Check the configured altitude vector (%s) and IMU orientation before calibrating again.",
            alt_offset,
            imu.alt_vector,
        )
        return fig, axes

    imu.alt_offset = alt_offset
    logger.info("Calibrating altitude offset to %s degrees", imu.alt_offset)

    _wait_for_calibration_keypress(
        imu,
        fig,
        axes,
        plt,
        "Please point the IMU device horizontally at True North, press a key when ready",
    )

    az_angle = _calibration_angle(imu, imu.az_vector, {"roll", "yaw"})
    if az_angle is None:
        return fig, axes

    imu.az_offset = az_angle % 360
    logger.info("Calibrating azimuth offset to %s degrees", imu.az_offset)

    save_imu_device_list(imu, imu_device_list, profile=profile)
    return fig, axes

def main():
    """Main function to run the IMU calibration script."""

    # Set up logging configuration to display log messages with timestamps and severity levels.
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    parser = argparse.ArgumentParser(description="Inertial Motion Unit (IMU)")
    parser.add_argument('--imu-id', type=str, default='imu001', help='Unique IMU model identifier.')
    parser.add_argument("--profile", type=str, default="default", help="Configuration profile under src/config, e.g. default or jodrell.")
    parser.add_argument('--qt-platform', type=str, default=os.environ.get("QT_QPA_PLATFORM", "xcb"), help='Qt platform plugin (e.g. xcb, wayland, offscreen).')
    args = parser.parse_args()

    # Set the QT_QPA_PLATFORM environment variable to the specified value if provided, allowing the user to choose the Qt platform plugin for rendering.
    if args.qt_platform:
        os.environ["QT_QPA_PLATFORM"] = args.qt_platform

    imu_device_list = load_imu_device_list(profile=args.profile)
    imu_device = imu_device_list.get_imu_by_id(args.imu_id)
    if imu_device is None:
        logger.error("IMU %s not found in profile %s IMUDeviceList.json.", args.imu_id, args.profile)
        return

    imu = IMU(
        imu_device=imu_device,
    )
    if not imu.connect():
        logger.error("Calibration failed to connect to IMU, exiting...")
        return

    try:
        # Calibrate the IMU and plot the angle history in real-time.
        fig, axes = calibrate_imu(imu, imu_device_list, profile=args.profile)
        while True:
            alt, az = imu.get_altaz()
            temp = imu.get_temperature()
            logger.info("Altitude: %s, Azimuth: %s", alt, az)
            logger.info("Roll: %s, Pitch: %s, Yaw: %s", imu.get_roll(), imu.get_pitch(), imu.get_yaw())
            logger.info("Temperature: %s", temp)
            if fig is not None and axes is not None:
                try:
                    _plot_live_angle_history(imu, axes)
                    fig.tight_layout()
                    fig.canvas.draw_idle()
                    fig.canvas.flush_events()
                except (IndexError, TypeError, ValueError):
                    pass
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt...")
    finally:
        imu.disconnect()

if __name__ == "__main__":
    main()
