from datetime import datetime, timezone
import logging
import threading
from typing import Optional

from models.ws import WeatherData, WeatherStationModel, WeatherStationDriverType
from util.registry import (
    WEATHER_STATION_DRIVER_NAMESPACE,
    get as registry_get,
    load_class_from_path as registry_load_class_from_path,
    register as registry_register,
)

logger = logging.getLogger(__name__)


class WeatherStationDriver:
    """Base interface for physical weather station drivers."""

    def __init__(self, ws_model: WeatherStationModel = None):
        if ws_model is None:
            raise ValueError("WeatherStationDriver requires a WeatherStationModel.")

        self.ws_model = ws_model
        self._rlock = threading.RLock()
        self._closed = False

    def close(self) -> None:
        """Release resources owned by this driver.

        The operation is idempotent and waits for any in-progress sensor read
        to finish before invoking the implementation-specific cleanup hook.
        """
        with self._rlock:
            if self._closed:
                return

            try:
                self._close()
            finally:
                self._closed = True

    def _close(self) -> None:
        """Implementation-specific resource cleanup hook."""

    def get_poll_interval_ms(self) -> int:
        return self.ws_model.driver_poll_period or 1000

    def get_weather_data(self) -> WeatherData:
        """Read all supported weather fields and return a WeatherData sample."""
        now = datetime.now(timezone.utc)
        weather = WeatherData(
            obs_time=now,
            last_update=now,
            ws_id=self.ws_model.id,
        )

        weather.wind_speed = self.get_wind_speed()
        weather.precipitation = self.get_precipitation()
        weather.wind_direction = self.get_wind_direction()
        weather.temperature = self.get_temperature()
        weather.humidity = self.get_humidity()
        weather.pressure = self.get_pressure()

        if self.ws_model.driver_failures > 0:
            logger.info(
                "WeatherStationDriver %s reset failure count %s after successful read.",
                self.ws_model.id,
                self.ws_model.driver_failures,
            )
            self.ws_model.reset_failures()

        self.ws_model.last_update = now
        return weather

    def get_wind_speed(self) -> float:
        return self._read_float("_get_wind_speed", required=True)

    def get_precipitation(self) -> float:
        return self._read_float("_get_precipitation", required=True)

    def get_wind_direction(self) -> Optional[float]:
        return self._read_float("_get_wind_direction", required=False)

    def get_temperature(self) -> Optional[float]:
        return self._read_float("_get_temperature", required=False)

    def get_humidity(self) -> Optional[float]:
        return self._read_float("_get_humidity", required=False)

    def get_pressure(self) -> Optional[float]:
        return self._read_float("_get_pressure", required=False)

    def _read_float(self, method_name: str, required: bool) -> Optional[float]:
        try:
            with self._rlock:
                if self._closed:
                    raise RuntimeError(f"{type(self).__name__} is closed")
                value = getattr(self, method_name)()
        except NotImplementedError:
            if required:
                raise
            return None
        except Exception as exc:
            self.ws_model.increment_failures()
            self.ws_model.set_last_err(f"{type(self).__name__}.{method_name} failed: {exc}")
            raise

        if value is None:
            return None

        return float(value)

    def _get_wind_speed(self) -> float:
        raise NotImplementedError

    def _get_precipitation(self) -> float:
        raise NotImplementedError

    def _get_wind_direction(self) -> Optional[float]:
        raise NotImplementedError

    def _get_temperature(self) -> Optional[float]:
        raise NotImplementedError

    def _get_humidity(self) -> Optional[float]:
        raise NotImplementedError

    def _get_pressure(self) -> Optional[float]:
        raise NotImplementedError


def create_ws_driver(ws_model: WeatherStationModel) -> Optional[WeatherStationDriver]:
    """Instantiate the concrete driver selected by WeatherStationModel.

    This factory supports:
    - Built-in enum-backed drivers (`WeatherStationDriverType.ADS1115`, `MODBUS`).
    - Registered drivers via `util.registry.register(WEATHER_STATION_DRIVER_NAMESPACE, "name", ctor)`.
    - Dynamic class paths like "package.module:ClassName" or "package.module.ClassName".

    The factory prefers the enum when known; otherwise it will consult `ws_model.driver_config`.
    For backward compatibility, if nothing is configured and driver_type is UNKNOWN the function
    will return None.
    """
    # Built-in types (keep original behaviour)
    if ws_model.driver_type == WeatherStationDriverType.ADS1115:
        try:
            from ws.drivers.ads1115 import ADS1115WeatherStationDriver
        except ModuleNotFoundError:
            from drivers.ads1115 import ADS1115WeatherStationDriver

        ctor = ADS1115WeatherStationDriver
        registry_register(WEATHER_STATION_DRIVER_NAMESPACE, "ads1115", ctor)
        return ctor(ws_model=ws_model)

    if ws_model.driver_type == WeatherStationDriverType.MODBUS:
        try:
            from ws.drivers.modbus import ModbusWeatherStationDriver
        except ModuleNotFoundError:
            from drivers.modbus import ModbusWeatherStationDriver

        ctor = ModbusWeatherStationDriver
        registry_register(WEATHER_STATION_DRIVER_NAMESPACE, "modbus", ctor)
        return ctor(ws_model=ws_model)

    # If an explicit driver configuration exists, try to use it.
    driver_identifier = None
    if hasattr(ws_model, "driver_config") and ws_model.driver_config is not None:
        # driver_config may be a BaseModel; try common fields
        cfg = ws_model.driver_config
        if isinstance(cfg, str):
            driver_identifier = cfg
        else:
            # Try to read attribute 'driver' or 'name'
            driver_identifier = getattr(cfg, "driver", None) or getattr(cfg, "name", None)

    # Try registry lookup
    if driver_identifier:
        ctor = registry_get(WEATHER_STATION_DRIVER_NAMESPACE, driver_identifier)
        if ctor:
            return ctor(ws_model=ws_model)

        # Try dynamic import by path
        try:
            cls = registry_load_class_from_path(driver_identifier)
            return cls(ws_model=ws_model)
        except Exception:
            logger.exception("Failed to load driver with identifier '%s'", driver_identifier)

    # Nothing configured; be tolerant and keep original behaviour for UNKNOWN
    if ws_model.driver_type == WeatherStationDriverType.UNKNOWN:
        logger.warning("WeatherStation %s has no physical driver configured.", ws_model.id)
        return None

    raise ValueError(f"Unsupported weather station driver type or configuration: {ws_model.driver_type}")
