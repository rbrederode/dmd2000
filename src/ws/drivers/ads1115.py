from datetime import datetime, timezone
import logging
from typing import Optional

from schema import Schema, And, Or

from models.base import BaseModel
from models.ws import WeatherStationModel
from ws.drivers.driver import WeatherStationDriver
from ws.wind import ADS1115VoltageReader

logger = logging.getLogger(__name__)


class ADS1115Config(BaseModel):
    """Configuration for an ADS1115-backed analogue weather station."""

    schema = Schema({
        "_type": And(str, lambda v: v == "ADS1115Config"),
        "adc_backend": And(str, lambda v: v in ["ads1115", "mock"]),
        "adc_channel": And(int, lambda v: 0 <= v <= 3),
        "adc_address": And(int, lambda v: 0 <= v <= 0x7F),
        "voltage_min": And(Or(int, float), lambda v: v >= 0.0),
        "voltage_max": And(Or(int, float), lambda v: v > 0.0),
        "speed_min": And(Or(int, float), lambda v: v >= 0.0),
        "speed_max": And(Or(int, float), lambda v: v >= 0.0),
        "mock_voltage": And(Or(int, float), lambda v: v >= 0.0),
        "precipitation_channel": Or(None, And(int, lambda v: 0 <= v <= 3)),
        "precipitation_default": And(Or(int, float), lambda v: v >= 0.0),
        "precipitation_voltage_min": And(Or(int, float), lambda v: v >= 0.0),
        "precipitation_voltage_max": And(Or(int, float), lambda v: v > 0.0),
        "precipitation_min": And(Or(int, float), lambda v: v >= 0.0),
        "precipitation_max": And(Or(int, float), lambda v: v >= 0.0),
        "mock_precipitation_voltage": And(Or(int, float), lambda v: v >= 0.0),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "ADS1115Config",
            "adc_backend": "ads1115",
            "adc_channel": 0,
            "adc_address": 0x48,
            "voltage_min": 0.0,
            "voltage_max": 3.3,
            "speed_min": 0.0,
            "speed_max": 30.0,
            "mock_voltage": 1.65,
            "precipitation_channel": None,
            "precipitation_default": 0.0,
            "precipitation_voltage_min": 0.0,
            "precipitation_voltage_max": 3.3,
            "precipitation_min": 0.0,
            "precipitation_max": 20.0,
            "mock_precipitation_voltage": 0.0,
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            kwargs.setdefault(key, value)

        super().__init__(**kwargs)


class FixedVoltageReader:
    """Development reader used when adc_backend is mock."""

    def __init__(self, voltage: float):
        self.voltage = voltage

    def read_voltage(self) -> float:
        return float(self.voltage)

    def close(self) -> None:
        """The mock reader owns no external resources."""


class ADS1115WeatherStationDriver(WeatherStationDriver):
    """Weather station driver for ADS1115 analogue voltage inputs."""

    def __init__(self, ws_model: WeatherStationModel = None):
        super().__init__(ws_model=ws_model)

        if ws_model.driver_config is None:
            ws_model.driver_config = ADS1115Config()
        if not isinstance(ws_model.driver_config, ADS1115Config):
            raise TypeError(
                f"ADS1115WeatherStationDriver requires ADS1115Config, got "
                f"{type(ws_model.driver_config).__name__}"
            )

        self.config: ADS1115Config = ws_model.driver_config
        self.wind_reader = self._build_voltage_reader(
            channel=self.config.adc_channel,
            mock_voltage=self.config.mock_voltage,
        )
        self.precipitation_reader = None
        if self.config.precipitation_channel is not None:
            self.precipitation_reader = self._build_voltage_reader(
                channel=self.config.precipitation_channel,
                mock_voltage=self.config.mock_precipitation_voltage,
            )

    def _close(self) -> None:
        """Close each voltage reader and its associated I2C resource."""
        readers = (self.wind_reader, self.precipitation_reader)
        self.wind_reader = None
        self.precipitation_reader = None

        first_error = None
        closed_reader_ids = set()
        for reader in readers:
            if reader is None or id(reader) in closed_reader_ids:
                continue

            closed_reader_ids.add(id(reader))
            close = getattr(reader, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:
                    logger.exception("Failed to close ADS1115 weather station voltage reader")
                    if first_error is None:
                        first_error = exc

        if first_error is not None:
            raise first_error

        logger.info("ADS1115 weather station %s closed.", self.ws_model.id)

    def _build_voltage_reader(self, channel: int, mock_voltage: float):
        if self.config.adc_backend == "mock":
            return FixedVoltageReader(mock_voltage)

        return ADS1115VoltageReader(channel=channel, address=self.config.adc_address)

    def _get_wind_speed(self) -> float:
        voltage = self.wind_reader.read_voltage()
        return _linear_map(
            value=voltage,
            in_min=self.config.voltage_min,
            in_max=self.config.voltage_max,
            out_min=self.config.speed_min,
            out_max=self.config.speed_max,
        )

    def _get_precipitation(self) -> float:
        if self.precipitation_reader is None:
            return float(self.config.precipitation_default)

        voltage = self.precipitation_reader.read_voltage()
        return _linear_map(
            value=voltage,
            in_min=self.config.precipitation_voltage_min,
            in_max=self.config.precipitation_voltage_max,
            out_min=self.config.precipitation_min,
            out_max=self.config.precipitation_max,
        )

    def _get_wind_direction(self) -> Optional[float]:
        return None


def _linear_map(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    if in_max <= in_min:
        raise ValueError(f"Input range max must be greater than min, got {in_max} <= {in_min}")

    clamped = min(max(float(value), float(in_min)), float(in_max))
    fraction = (clamped - float(in_min)) / (float(in_max) - float(in_min))
    return float(out_min) + fraction * (float(out_max) - float(out_min))
