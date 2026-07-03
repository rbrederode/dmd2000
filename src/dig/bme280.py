from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from models.temp import TempReading


BME280_ADDRESSES = (0x76, 0x77)


class BME280Reader:
    def __init__(self, bus_number: int | None = None, address: int | None = None, bus: int | None = None):
        if bus_number is None:
            bus_number = bus

        try:
            import adafruit_bme280.advanced as adafruit_bme280
        except ImportError as err:
            raise RuntimeError(
                "Unable to import the BME280 driver module "
                "`adafruit_bme280.advanced`. Install it with "
                "`pip install adafruit-circuitpython-bme280` or install requirements.txt. "
                f"Original import error: {err!r}. Python executable: {sys.executable}."
            ) from err

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
            f"No BME280 detected at {tried}. "
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
    try:
        address = int(value, 0)
    except ValueError as err:
        raise argparse.ArgumentTypeError(f"Invalid I2C address: {value}") from err

    if not 0x03 <= address <= 0x77:
        raise argparse.ArgumentTypeError("I2C address must be between 0x03 and 0x77")
    return address


def make_i2c_bus(bus_number: int | None = None):
    if bus_number is not None:
        try:
            from adafruit_extended_bus import ExtendedI2C
        except ImportError as err:
            raise RuntimeError(
                "Using a numbered bus requires adafruit-extended-bus. "
                "Install project requirements, then try again."
            ) from err

        return ExtendedI2C(bus_number)

    try:
        import board
        import busio
    except ImportError as err:
        raise RuntimeError(
            "Default Raspberry Pi I2C requires adafruit-blinka. "
            "Install project requirements, enable I2C, then try again."
        ) from err

    return busio.I2C(board.SCL, board.SDA)


def env_i2c_bus() -> int | None:
    value = os.environ.get("TEMP_I2C_BUS")
    if not value:
        return None
    return int(value, 0)
