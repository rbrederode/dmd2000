from datetime import datetime, timezone
import logging
from typing import Optional

from schema import Schema, And, Or

from models.base import BaseModel
from models.ws import WeatherStationModel

try:
    from ws.drivers.driver import WeatherStationDriver
except ModuleNotFoundError:
    from drivers.driver import WeatherStationDriver

logger = logging.getLogger(__name__)


class ModbusConfig(BaseModel):
    """Configuration for a Modbus RTU weather station over an RS485/USB converter."""

    schema = Schema({
        "_type": And(str, lambda v: v == "ModbusConfig"),
        "port": And(str, lambda v: isinstance(v, str)),
        "slave_address": And(int, lambda v: 1 <= v <= 247),
        "baudrate": And(int, lambda v: v > 0),
        "bytesize": And(int, lambda v: 5 <= v <= 8),
        "parity": And(str, lambda v: v in ["N", "E", "O"]),
        "stopbits": And(Or(int, float), lambda v: v in [1, 1.5, 2]),
        "timeout": And(Or(int, float), lambda v: v > 0.0),
        "mode": And(str, lambda v: v in ["rtu", "ascii"]),
        "function_code": And(int, lambda v: v in [3, 4]),
        "register_decimals": And(int, lambda v: v >= 0),
        "signed": And(bool, lambda v: isinstance(v, bool)),
        "clear_buffers_before_each_transaction": And(bool, lambda v: isinstance(v, bool)),
        "debug": And(bool, lambda v: isinstance(v, bool)),
        "wind_speed_register": And(int, lambda v: v >= 0),
        "wind_speed_scale": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "wind_speed_offset": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "precipitation_register": Or(None, And(int, lambda v: v >= 0)),
        "precipitation_scale": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "precipitation_offset": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "precipitation_default": And(Or(int, float), lambda v: v >= 0.0),
        "wind_direction_register": Or(None, And(int, lambda v: v >= 0)),
        "wind_direction_scale": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "wind_direction_offset": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "temperature_register": Or(None, And(int, lambda v: v >= 0)),
        "temperature_scale": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "temperature_offset": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "humidity_register": Or(None, And(int, lambda v: v >= 0)),
        "humidity_scale": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "humidity_offset": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "pressure_register": Or(None, And(int, lambda v: v >= 0)),
        "pressure_scale": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "pressure_offset": And(Or(int, float), lambda v: isinstance(v, (int, float))),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "ModbusConfig",
            "port": "/dev/ttyUSB0",
            "slave_address": 1,
            "baudrate": 9600,
            "bytesize": 8,
            "parity": "N",
            "stopbits": 1,
            "timeout": 1.0,
            "mode": "rtu",
            "function_code": 3,
            "register_decimals": 0,
            "signed": False,
            "clear_buffers_before_each_transaction": True,
            "debug": False,
            "wind_speed_register": 0,
            "wind_speed_scale": 0.1,
            "wind_speed_offset": 0.0,
            "precipitation_register": None,
            "precipitation_scale": 1.0,
            "precipitation_offset": 0.0,
            "precipitation_default": 0.0,
            "wind_direction_register": None,
            "wind_direction_scale": 1.0,
            "wind_direction_offset": 0.0,
            "temperature_register": None,
            "temperature_scale": 0.1,
            "temperature_offset": 0.0,
            "humidity_register": None,
            "humidity_scale": 0.1,
            "humidity_offset": 0.0,
            "pressure_register": None,
            "pressure_scale": 0.1,
            "pressure_offset": 0.0,
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            kwargs.setdefault(key, value)

        super().__init__(**kwargs)


class ModbusWeatherStationDriver(WeatherStationDriver):
    """Weather station driver for Modbus RTU sensors via RS485/USB."""

    def __init__(self, ws_model: WeatherStationModel = None):
        super().__init__(ws_model=ws_model)

        if ws_model.driver_config is None:
            ws_model.driver_config = ModbusConfig()
        if not isinstance(ws_model.driver_config, ModbusConfig):
            raise TypeError(
                f"ModbusWeatherStationDriver requires ModbusConfig, got "
                f"{type(ws_model.driver_config).__name__}"
            )

        self.config: ModbusConfig = ws_model.driver_config
        self.instrument = self._build_instrument()

    def _build_instrument(self):
        try:
            import minimalmodbus
            import serial
        except ImportError as exc:
            raise ImportError(
                "Modbus weather station driver requires minimalmodbus and pyserial. "
                "Install the packages and ensure the Waveshare RS485-to-USB adapter "
                "appears as a serial port."
            ) from exc

        instrument = minimalmodbus.Instrument(self.config.port, self.config.slave_address)
        instrument.mode = minimalmodbus.MODE_ASCII if self.config.mode == "ascii" else minimalmodbus.MODE_RTU
        instrument.clear_buffers_before_each_transaction = self.config.clear_buffers_before_each_transaction
        instrument.debug = self.config.debug
        instrument.serial.baudrate = self.config.baudrate
        instrument.serial.bytesize = self.config.bytesize
        instrument.serial.parity = {
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
        }[self.config.parity]
        instrument.serial.stopbits = self.config.stopbits
        instrument.serial.timeout = self.config.timeout
        return instrument

    def _get_wind_speed(self) -> float:
        return self._read_scaled_register(
            register=self.config.wind_speed_register,
            scale=self.config.wind_speed_scale,
            offset=self.config.wind_speed_offset,
        )

    def _get_precipitation(self) -> float:
        value = self._read_scaled_register(
            register=self.config.precipitation_register,
            scale=self.config.precipitation_scale,
            offset=self.config.precipitation_offset,
        )
        return float(self.config.precipitation_default) if value is None else value

    def _get_wind_direction(self) -> Optional[float]:
        return self._read_scaled_register(
            register=self.config.wind_direction_register,
            scale=self.config.wind_direction_scale,
            offset=self.config.wind_direction_offset,
        )

    def _get_temperature(self) -> Optional[float]:
        return self._read_scaled_register(
            register=self.config.temperature_register,
            scale=self.config.temperature_scale,
            offset=self.config.temperature_offset,
        )

    def _get_humidity(self) -> Optional[float]:
        return self._read_scaled_register(
            register=self.config.humidity_register,
            scale=self.config.humidity_scale,
            offset=self.config.humidity_offset,
        )

    def _get_pressure(self) -> Optional[float]:
        return self._read_scaled_register(
            register=self.config.pressure_register,
            scale=self.config.pressure_scale,
            offset=self.config.pressure_offset,
        )

    def _read_scaled_register(self, register: Optional[int], scale: float, offset: float) -> Optional[float]:
        if register is None:
            return None

        raw_value = self.instrument.read_register(
            registeraddress=register,
            number_of_decimals=self.config.register_decimals,
            functioncode=self.config.function_code,
            signed=self.config.signed,
        )
        return (float(raw_value) * float(scale)) + float(offset)
