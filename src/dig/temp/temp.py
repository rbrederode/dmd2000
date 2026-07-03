#!/usr/bin/env python3
"""
Cached temperature, humidity, and pressure readings from a sensor.

Run on the Raspberry Pi with:
    python src/dig/temp/temp.py --bus 3
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Protocol

from models.temp import TempReading
from models.comms import CommunicationStatus

try:
    from .bme280 import BME280Reader, env_i2c_bus, parse_i2c_address
except ImportError:
    from bme280 import BME280Reader, env_i2c_bus, parse_i2c_address

DEFAULT_DEVICE = "bme280"
SUPPORTED_DEVICES = (DEFAULT_DEVICE,)

class SensorReader(Protocol):
    """A protocol for reading sensor data. Implementations of this protocol should provide a `read` method that returns a `TempReading`.
        ... note:: This protocol is used to define the interface for sensor readers, allowing for different sensor implementations to be used interchangeably."""

    def read(self) -> TempReading:
        ...

class Temp:
    """ Poll a temperature sensor in the background and expose cached readings.

        Getters return None when the sensor is unavailable or the latest successful reading is older than max_age_seconds. """

    def __init__(self, device: str = DEFAULT_DEVICE, sensor_config: dict[str, Any] | None = None, interval_seconds: float = 1.0, max_age_seconds: float = 5.0, autostart: bool = True,):
        """ Initializes the Temp object with the specified device, sensor configuration, polling interval, maximum age for readings, and autostart option.
            :param device: The type of temperature sensor device. Default is "bme280".
            :param sensor_config: A dictionary containing configuration parameters for the sensor. Default is None.
            :param interval_seconds: The interval in seconds between sensor polls. Default is 1.0.
            :param max_age_seconds: The maximum age in seconds before cached readings are considered stale. Default is 5.0.
            :param autostart: If True, the polling thread will start automatically. Default is True. """

        if interval_seconds <= 0:
            raise ValueError("Temp interval_seconds must be greater than zero")
        if max_age_seconds <= 0:
            raise ValueError("Temp max_age_seconds must be greater than zero")
        if device.lower() not in SUPPORTED_DEVICES:
            supported = ", ".join(SUPPORTED_DEVICES)
            raise ValueError(f"Temp detected an unsupported temperature device: {device}. Supported devices: {supported}")

        self.device = device.lower()
        self.sensor_config = dict(sensor_config or {})
        if self.device == "bme280" and "bus" in self.sensor_config and "bus_number" not in self.sensor_config:
            self.sensor_config["bus_number"] = self.sensor_config["bus"]
        self.interval_seconds = interval_seconds
        self.max_age_seconds = max_age_seconds

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._reader: SensorReader | None = None
        self._last_reading: TempReading | None = None
        self._last_error: Exception | None = None
        self._thread: threading.Thread | None = None

        if autostart:
            self.start()

    def start(self) -> None:
        """ Starts the background polling thread for the temperature sensor. If the thread is already running, this method does nothing. """

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name=f"Temp-{self.device}", daemon=True,)
        self._thread.start()

    def stop(self, timeout: float | None = 2.0) -> None:
        """ Stops the background polling thread for the temperature sensor. Waits for the thread to finish, with an optional timeout.
            :param timeout: The maximum time in seconds to wait for the thread to finish. Default is 2.0 seconds. If None, waits indefinitely. """

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def get_temp(self) -> float | None:
        """ Returns the temperature in Celsius if the last reading is fresh, otherwise returns None. """
        reading = self._fresh_reading()
        return None if reading is None else reading.temperature

    def get_humidity(self) -> float | None:
        """ Returns the humidity in % if the last reading is fresh, otherwise returns None. """
        reading = self._fresh_reading()
        return None if reading is None else reading.humidity

    def get_pressure(self) -> float | None:
        """ Returns the pressure in hPa if the last reading is fresh, otherwise returns None. """
        reading = self._fresh_reading()
        return None if reading is None else reading.pressure

    def get_reading(self) -> TempReading | None:
        """ Returns the last reading if it is fresh (not older than max_age_seconds), otherwise returns None. """
        return self._fresh_reading()

    def get_last_error(self) -> Exception | None:
        """ Returns the last error encountered during sensor reading, or None if there was no error. """
        with self._lock:
            return self._last_error

    def get_comms_status(self) -> CommunicationStatus:
        """ Returns the communication status of the temperature sensor. If the sensor is not available or there 
            was an error during the last read, returns NOT_ESTABLISHED. Otherwise, returns ESTABLISHED. """

        with self._lock:
            if self._last_error is not None:
                return CommunicationStatus.NOT_ESTABLISHED
            if self._reader is None:
                return CommunicationStatus.NOT_ESTABLISHED
            return CommunicationStatus.ESTABLISHED

    def _fresh_reading(self) -> TempReading | None:
        """ Returns the last reading if it is fresh (not older than max_age_seconds), otherwise returns None. """

        with self._lock:
            reading = self._last_reading

        if reading is None:
            return None
        now = datetime.now(timezone.utc)
        last_update = reading.last_update
        if last_update.tzinfo is None:
            last_update = last_update.replace(tzinfo=timezone.utc)
        if (now - last_update).total_seconds() > self.max_age_seconds:
            return None
        return reading

    def _poll_loop(self) -> None:
        """ Polls the sensor in a loop until the stop event is set. Updates the last reading and last error accordingly. """
        
        while not self._stop_event.is_set():
            try:
                if self._reader is None:
                    self._reader = self._make_reader()

                reading = self._reader.read()
                with self._lock:
                    self._last_reading = reading
                    self._last_error = None
            except Exception as err:
                with self._lock:
                    self._reader = None
                    self._last_error = err

            self._stop_event.wait(self.interval_seconds)

    def _make_reader(self) -> SensorReader:
        """ Creates a sensor reader based on the specified device type and configuration. Raises RuntimeError if the device type is unsupported. """
        if self.device == "bme280":
            return BME280Reader(**self.sensor_config)

        raise RuntimeError(f"Temp unsupported device: {self.device}")

def print_reading(reading: TempReading | None) -> None:
    """ Prints the temperature, humidity, and pressure readings to the console with a timestamp. If the reading is None, prints None for all values. """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    if reading is None:
        print(f"{timestamp}  temperature=None  humidity=None  pressure=None", flush=True)
        return

    print(f"{timestamp} temperature={reading.temperature:6.2f}C humidity={reading.humidity:6.2f}% pressure={reading.pressure:7.2f}hPa", flush=True)

def build_arg_parser() -> argparse.ArgumentParser:
    """ Builds and returns an argument parser for the temperature sensor script. """

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="Temperature sensor device type. Default: bme280.")
    parser.add_argument("--address", type=parse_i2c_address, default=None, help="BME280 I2C address, usually 0x76 or 0x77. Default: auto-detect.")
    parser.add_argument("--bus", type=int, default=env_i2c_bus(), help="I2C bus number. Default: TEMP_I2C_BUS or the standard Pi I2C pins.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between sensor polls. Default: 1.")
    parser.add_argument("--max-age", type=float, default=5.0, help="Seconds before cached readings are considered stale. Default: 5.")

    return parser

def main(argv: list[str] | None = None) -> int:
    """ Main function for the temperature sensor script. Parses command-line arguments, initializes the Temp object, and polls the sensor in a 
        loop until interrupted. Returns an exit code. """

    args = build_arg_parser().parse_args(argv)

    try:
        temp = Temp(
            device=args.device,
            sensor_config={
                "bus_number": args.bus,
                "address": args.address,
            },
            interval_seconds=args.interval,
            max_age_seconds=args.max_age,
        )
    except ValueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 2

    print(f"Polling {args.device} every {args.interval:g} second(s). Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            print_reading(temp.get_reading())
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        temp.stop()

if __name__ == "__main__":
    raise SystemExit(main())
