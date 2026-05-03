import random
from datetime import datetime, timezone

from models.ws import WeatherData, WeatherStationModel
from ws.drivers.driver import WeatherStationDriver
from util.registry import WEATHER_STATION_DRIVER_NAMESPACE, register


class ExampleExternalDriver(WeatherStationDriver):
    """A tiny example external driver that could live in a separate package.

    It returns pseudo-random weather values. External packages should register their
    driver on import using `util.registry.register(namespace, name, ctor)`.
    """

    def __init__(self, ws_model: WeatherStationModel):
        super().__init__(ws_model=ws_model)

    def _get_wind_speed(self) -> float:
        return random.uniform(0, 10)

    def _get_precipitation(self) -> float:
        return random.uniform(0, 2)

    def _get_wind_direction(self) -> float:
        return random.uniform(0, 359.9)

    def _get_temperature(self) -> float:
        return random.uniform(-5, 30)

    def _get_humidity(self) -> float:
        return random.uniform(10, 100)

    def _get_pressure(self) -> float:
        return random.uniform(980, 1030)


# Register this example driver under the name 'example_external'
register(WEATHER_STATION_DRIVER_NAMESPACE, "example_external", ExampleExternalDriver)
