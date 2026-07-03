#!/usr/bin/env python3
"""
Read a BME280 temperature, humidity, and pressure sensor over Raspberry Pi I2C.

Run on the Raspberry Pi with:
    python src/dig/tmp.py

Optional examples:
    python src/dig/tmp.py --address 0x76
    python src/dig/tmp.py --bus 1 --interval 2
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass


DEFAULT_ADDRESS = 0x77
DEFAULT_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class BME280Reading:
    temperature_c: float
    humidity_percent: float
    pressure_hpa: float


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
                "Using --bus requires adafruit-extended-bus. "
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


def make_bme280(address: int = DEFAULT_ADDRESS, bus_number: int | None = None):
    try:
        import adafruit_bme280.advanced as adafruit_bme280
    except ImportError as err:
        raise RuntimeError(
            "Missing BME280 driver. Install it with "
            "`pip install adafruit-circuitpython-bme280` or install requirements.txt."
        ) from err

    i2c = make_i2c_bus(bus_number)
    sensor = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=address)
    sensor.sea_level_pressure = 1013.25
    return sensor


def read_sensor(sensor) -> BME280Reading:
    return BME280Reading(
        temperature_c=float(sensor.temperature),
        humidity_percent=float(sensor.relative_humidity),
        pressure_hpa=float(sensor.pressure),
    )


def print_reading(reading: BME280Reading) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"{timestamp}  "
        f"temperature={reading.temperature_c:6.2f} C  "
        f"humidity={reading.humidity_percent:6.2f} %  "
        f"pressure={reading.pressure_hpa:7.2f} hPa",
        flush=True,
    )


def monitor(
    address: int = DEFAULT_ADDRESS,
    bus_number: int | None = None,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
) -> None:
    sensor = make_bme280(address=address, bus_number=bus_number)
    print(
        f"Reading BME280 on I2C address 0x{address:02x} "
        f"every {interval_seconds:g} second(s). Press Ctrl+C to stop.",
        flush=True,
    )

    while True:
        print_reading(read_sensor(sensor))
        time.sleep(interval_seconds)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        type=parse_i2c_address,
        default=DEFAULT_ADDRESS,
        help="BME280 I2C address, usually 0x76 or 0x77. Default: 0x77.",
    )
    parser.add_argument(
        "--bus",
        type=int,
        default=None,
        help="I2C bus number. Omit this to use the default Raspberry Pi I2C pins.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help="Seconds between readings. Default: 1.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.interval <= 0:
        print("Interval must be greater than zero.", file=sys.stderr)
        return 2

    try:
        monitor(
            address=args.address,
            bus_number=args.bus,
            interval_seconds=args.interval,
        )
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
