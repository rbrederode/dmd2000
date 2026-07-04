#!/usr/bin/env python3
"""
Cached temperature, humidity, and pressure readings from a sensor.

Run on the Raspberry Pi with:
    python src/dig/temp/temp.py --bus 3
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Protocol

from models.temp import TempReading
from models.comms import CommunicationStatus
from util.format import fmt_number, fmt_percent

try:
    from .bme280 import BME280Reader, env_i2c_bus, parse_i2c_address
except ImportError:
    from bme280 import BME280Reader, env_i2c_bus, parse_i2c_address

DEFAULT_DEVICE = "bme280"
SUPPORTED_DEVICES = (DEFAULT_DEVICE,)
DEFAULT_LOG_NAME = "dig_temperature"
DEFAULT_LOG_INTERVAL_SECONDS = 30.0

class SensorReader(Protocol):
    """ A protocol for reading sensor data. Implementations of this protocol should provide a `read` method that returns a `TempReading`.
        ... note:: This protocol is used to define the interface for sensor readers, allowing for different sensor implementations to be used interchangeably."""

    def read(self) -> TempReading:
        ...

class Temperature:
    """ Poll a temperature sensor in the background and expose cached readings.

        Getters return None when the sensor is unavailable or the latest successful reading is older than max_age_seconds. """

    def __init__(
        self,
        device: str = DEFAULT_DEVICE,
        sensor_config: dict[str, Any] | None = None,
        interval_seconds: float = 1.0,
        max_age_seconds: float = 5.0,
        log_interval_seconds: float = DEFAULT_LOG_INTERVAL_SECONDS,
        log_dir: str | Path | None = None,
        log_name: str = DEFAULT_LOG_NAME,
        autostart: bool = True,
    ):
        """ Initializes the Temperature object with the specified device, sensor configuration, polling interval, maximum age for readings, and autostart option.
            :param device: The type of temperature sensor device. Default is "bme280".
            :param sensor_config: A dictionary containing configuration parameters for the sensor. Default is None.
            :param interval_seconds: The interval in seconds between sensor polls. Default is 1.0.
            :param max_age_seconds: The maximum age in seconds before cached readings are considered stale. Default is 5.0.
            :param log_interval_seconds: The interval in seconds between temperature telemetry log entries. Default is 30.0.
            :param log_dir: The directory for temperature telemetry logs. Default is logs/temperature under the repository root.
            :param log_name: The log filename stem. Default is "dig_temperature".
            :param autostart: If True, the polling thread will start automatically. Default is True. """

        if interval_seconds <= 0:
            raise ValueError("Temperature interval_seconds must be greater than zero")
        if max_age_seconds <= 0:
            raise ValueError("Temperature max_age_seconds must be greater than zero")
        if log_interval_seconds <= 0:
            raise ValueError("Temperature log_interval_seconds must be greater than zero")
        if device.lower() not in SUPPORTED_DEVICES:
            supported = ", ".join(SUPPORTED_DEVICES)
            raise ValueError(f"Temperature detected an unsupported temperature device: {device}. Supported devices: {supported}")

        self.device = device.lower()
        self.sensor_config = dict(sensor_config or {})
        if self.device == "bme280" and "bus" in self.sensor_config and "bus_number" not in self.sensor_config:
            self.sensor_config["bus_number"] = self.sensor_config["bus"]
        self.interval_seconds = interval_seconds
        self.max_age_seconds = max_age_seconds
        self.log_interval_seconds = log_interval_seconds

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._reader: SensorReader | None = None
        self._last_reading: TempReading | None = None
        self._last_error: Exception | None = None
        self._thread: threading.Thread | None = None
        self._last_log_time: float | None = None
        self._telemetry_logger = self._make_telemetry_logger(log_dir, log_name)

        if autostart:
            self.start()

    def start(self) -> None:
        """ Starts the background polling thread for the temperature sensor. If the thread is already running, this method does nothing. """

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name=f"Temperature-{self.device}", daemon=True,)
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
                self._log_reading_if_due(reading)
            except Exception as err:
                with self._lock:
                    self._reader = None
                    self._last_error = err

            self._stop_event.wait(self.interval_seconds)

    def _make_reader(self) -> SensorReader:
        """ Creates a sensor reader based on the specified device type and configuration. Raises RuntimeError if the device type is unsupported. """
        if self.device == "bme280":
            return BME280Reader(**self.sensor_config)

        raise RuntimeError(f"Temperature unsupported device: {self.device}")

    def _make_telemetry_logger(self, log_dir: str | Path | None, log_name: str) -> logging.Logger:
        """ Creates a dedicated logger for temperature telemetry. """
        if log_dir is None:
            project_root = Path(__file__).resolve().parents[3]
            log_dir = project_root / "logs" / "temperature"

        log_dir = Path(log_dir).expanduser()
        log_dir.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger(f"dig.temperature.{log_name}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.handlers.clear()

        handler = TimedRotatingFileHandler(
            filename=log_dir / f"{log_name}.log",
            when="midnight",
            interval=1,
            backupCount=731,
            encoding="utf-8",
            utc=True,
        )
        handler.suffix = "%Y-%m-%d"
        formatter = logging.Formatter("%(asctime)s UTC | %(message)s")
        formatter.converter = time.gmtime
        handler.setFormatter(formatter)

        logger.addHandler(handler)
        return logger

    def _log_reading_if_due(self, reading: TempReading) -> None:
        """ Logs temperature telemetry when the configured log interval has elapsed. """
        now = time.monotonic()
        if self._last_log_time is not None and now - self._last_log_time < self.log_interval_seconds:
            return

        self._last_log_time = now
        self._telemetry_logger.info(
            "%s | Temperature=%s | Humidity=%s | Pressure=%s",
            self.device.upper(),
            fmt_number(reading.temperature, precision=1),
            fmt_percent(reading.humidity),
            fmt_number(reading.pressure, precision=0),
        )

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
    """ Main function for the temperature sensor script. Parses command-line arguments, initializes the Temperature object, and polls the sensor in a 
        loop until interrupted. Returns an exit code. """

    args = build_arg_parser().parse_args(argv)

    try:
        temp = Temperature(
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
