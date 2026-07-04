from __future__ import annotations

import argparse
import os
import sys
import warnings
from datetime import datetime, timezone

from models.temp import TempReading

BME280_ADDRESSES = (0x76, 0x77)

class BME280Reader:
    """ Reads temperature, humidity, and pressure from a BME280 sensor. 
        References: 
            https://randomnerdtutorials.com/raspberry-pi-bme280-python/
            https://amzn.eu/d/0cfEvFAf
            https://shillehtek.com/blogs/shillehtek-product-manuals/bme280-environmental-sensor-raspberry-pi-arduino-esp32-i2c-humidity-pressure-and-temperature-measurement
    """

    def __init__(self, bus_number: int | None = None, address: int | None = None, bus: int | None = None):
        """ Initializes the BME280Reader with the specified I2C bus number and address.
            If bus_number is None, the default I2C bus will be used.
            If address is None, the default BME280 addresses (0x76 and 0x77) will be probed.

            Use 'i2cdetect -y 1' to find the I2C bus number and address of the BME280 sensor on a Raspberry Pi.
            If the GPIO pins are reconfigured on the PI use 'sudo nano /boot/firmware/config.txt' to view/set the I2C bus number and address.
            Reboot the PI after changing the config.txt file to apply the changes.

        """
        if bus_number is None:
            bus_number = bus

        try:
            import adafruit_bme280.advanced as adafruit_bme280
        except ImportError as err:
            raise RuntimeError(
                "BME280Reader unable to import the BME280 driver module "
                "`adafruit_bme280.advanced`. Install it with "
                "`pip install adafruit-circuitpython-bme280` or install requirements.txt. "
                f"Original import error: {err!r}. Python executable: {sys.executable}."
            ) from err

        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="I2C frequency is not settable in python, ignoring!",
                category=RuntimeWarning,
                module=r"adafruit_blinka\.microcontroller\.generic_linux\.i2c",
            )
            i2c = make_i2c_bus(bus_number)

        addresses = (address,) if address is not None else BME280_ADDRESSES
        probe_errors = []

        for candidate_address in addresses:
            try:
                sensor = adafruit_bme280.Adafruit_BME280_I2C(
                    i2c,
                    address=candidate_address,
                )
            except (OSError, ValueError) as err:
                probe_errors.append(f"0x{candidate_address:02x}: {err}")
                continue

            sensor.sea_level_pressure = 1013.25
            self._sensor = sensor
            self.address = candidate_address
            return

        tried = ", ".join(f"0x{candidate_address:02x}" for candidate_address in addresses)
        details = "; ".join(probe_errors)
        raise RuntimeError(
            f"BME280Reader could not detect bme280 at {tried}. "
            "Check wiring, confirm I2C is enabled, and run `i2cdetect -y <bus>` "
            "on the Raspberry Pi to see which address responds. "
            f"Probe details: {details}"
        )

    def read(self) -> TempReading:
        now = datetime.now(timezone.utc)
        return TempReading(
            temperature=float(self._sensor.temperature),
            humidity=float(self._sensor.relative_humidity),
            pressure=float(self._sensor.pressure),
            last_update=now,
        )

def parse_i2c_address(value: str) -> int:
    """ Parses a string as an I2C address in decimal or hex format. Raises argparse.ArgumentTypeError if invalid. """
    try:
        address = int(value, 0)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"BME280Reader: Invalid I2C address: {value}") from err

    if not 0x03 <= address <= 0x77:
        raise argparse.ArgumentTypeError("BME280Reader: I2C address must be between 0x03 and 0x77")
    return address


def make_i2c_bus(bus_number: int | None = None):
    """ Creates an I2C bus object for the specified bus number. If bus_number is None, the default I2C bus will be used. """
    if bus_number is not None:
        try:
            from adafruit_extended_bus import ExtendedI2C
        except ImportError as err:
            raise RuntimeError(
                "BME280Reader: Using a numbered bus requires adafruit-extended-bus. "
                "Install project requirements, then try again."
            ) from err

        return ExtendedI2C(bus_number)

    try:
        import board
        import busio
    except ImportError as err:
        raise RuntimeError(
            "BME280Reader: Default Raspberry Pi I2C requires adafruit-blinka. "
            "Install project requirements, enable I2C, then try again."
        ) from err

    return busio.I2C(board.SCL, board.SDA)


def env_i2c_bus() -> int | None:
    """ Returns the I2C bus number from the TEMP_I2C_BUS environment variable, or None if not set. """
    value = os.environ.get("TEMP_I2C_BUS")
    if not value:
        return None
    return int(value, 0)
