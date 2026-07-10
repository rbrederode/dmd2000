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

from imu.imu import IMU, load_imu_device_list
from models.imu import IMUDeviceList
from util.format import fmt_angle


logger = logging.getLogger(__name__)


def _plot_angle_history(imu):
    first_idx = np.where(imu.angle_hist[:, 0] != 0)[0][0]

    import matplotlib.pyplot as plt

    plt.figure(figsize=(12, 6))
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 1], label='Roll')
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 2], label='Pitch')
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 3], label='Yaw')
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 4], label='Altitude')
    plt.plot(imu.angle_hist[:, 0], imu.angle_hist[:, 5], label='Azimuth')
    plt.xlabel('Time')
    plt.ylabel('Angle (degrees)')
    plt.xlim(imu.angle_hist[first_idx, 0], imu.angle_hist[-1, 0])
    plt.title('IMU Angle History')
    plt.legend()
    plt.grid()
    plt.pause(0.1)


def _plot_live_angle_history(imu, ax):
    ax.cla()
    ax.set_xlabel('Time')
    ax.set_ylabel('Angle (degrees)')
    ax.set_title('IMU Angle History')
    ax.grid()

    with imu._lock:
        first_idx = np.where(imu.angle_hist[:, 0] != 0)[0][0]
        angle_hist = imu.angle_hist.copy()

    ax.set_xlim(angle_hist[first_idx, 0], angle_hist[-1, 0])
    alt, az = imu.get_altaz()
    roll = imu.get_roll()
    pitch = imu.get_pitch()
    yaw = imu.get_yaw()
    ax.plot(angle_hist[:, 0], angle_hist[:, 1], label=f'Roll {fmt_angle(roll)}')
    ax.plot(angle_hist[:, 0], angle_hist[:, 2], label=f'Pitch {fmt_angle(pitch)}')
    ax.plot(angle_hist[:, 0], angle_hist[:, 3], label=f'Yaw {fmt_angle(yaw)}')
    ax.plot(angle_hist[:, 0], angle_hist[:, 4], label=f'Altitude {fmt_angle(alt)}')
    ax.plot(angle_hist[:, 0], angle_hist[:, 5], label=f'Azimuth {fmt_angle(az)}')
    ax.legend(loc='upper right')


def _wait_for_calibration_keypress(imu, fig, ax, plt, message):
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
                _plot_live_angle_history(imu, ax)
            except (IndexError, TypeError):
                pass
            plt.pause(0.1)
    finally:
        fig.canvas.mpl_disconnect(connection_id)
        _restore_terminal(old_terminal_settings)


def _set_terminal_cbreak():
    if not sys.stdin.isatty():
        return None

    old_settings = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())
    return old_settings


def _restore_terminal(old_settings):
    if old_settings is not None:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def _terminal_key_pressed():
    if not sys.stdin.isatty():
        return False

    readable, _, _ = select.select([sys.stdin], [], [], 0)
    if not readable:
        return False

    sys.stdin.read(1)
    return True


def _calibration_angle(imu, vector_name, valid_vectors):
    angle = imu._angle_for_vector(imu.imu_data, vector_name, valid_vectors)
    if angle is None:
        logger.error("Cannot calibrate offset, vector angle is None.")
    return angle


def save_imu_device_list(imu, imu_device_list=None, profile="default"):
    config_dir = Path("config") / profile
    if imu_device_list is None:
        imu_device_list = IMUDeviceList(list_id=profile, imu_list=[imu.imu_device])
    imu_device_list.last_update = datetime.now(timezone.utc)
    imu_device_list.save_to_disk(output_dir=str(config_dir), filename="IMUDeviceList.json")
    logger.info("Saved IMUDeviceList.json to profile directory %s", config_dir)


def calibrate_imu(imu, imu_device_list=None, profile="default"):
    if not imu.connected:
        return None, None

    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(12, 6))
    ax = plt.subplot(111, polar=False)

    _wait_for_calibration_keypress(
        imu,
        fig,
        ax,
        plt,
        "Please point the IMU device vertically at the Zenith, press a key when ready",
    )

    alt_angle = _calibration_angle(imu, imu.alt_vector, {"roll", "pitch"})
    if alt_angle is None:
        return fig, ax
    imu.alt_offset = 90 - alt_angle
    logger.info("Calibrating altitude offset to %s degrees", imu.alt_offset)

    _wait_for_calibration_keypress(
        imu,
        fig,
        ax,
        plt,
        "Please point the IMU device horizontally at True North, press a key when ready",
    )

    az_angle = _calibration_angle(imu, imu.az_vector, {"roll", "yaw"})
    if az_angle is None:
        return fig, ax

    imu.az_offset = az_angle % 360
    logger.info("Calibrating azimuth offset to %s degrees", imu.az_offset)

    save_imu_device_list(imu, imu_device_list, profile=profile)
    return fig, ax


def main():
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
        logger.error("Failed to connect to IMU, exiting...")
        return

    try:
        fig, ax = calibrate_imu(imu, imu_device_list, profile=args.profile)
        while True:
            alt, az = imu.get_altaz()
            temp = imu.get_temperature()
            logger.info("Altitude: %s, Azimuth: %s", alt, az)
            logger.info("Roll: %s, Pitch: %s, Yaw: %s", imu.get_roll(), imu.get_pitch(), imu.get_yaw())
            logger.info("Temperature: %s", temp)
            if fig is not None and ax is not None:
                try:
                    _plot_live_angle_history(imu, ax)
                    fig.canvas.draw_idle()
                    fig.canvas.flush_events()
                except (IndexError, TypeError):
                    pass
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt...")
    finally:
        imu.disconnect()


if __name__ == "__main__":
    main()
