#!/usr/bin/env python3
import logging

logging.basicConfig(level=logging.INFO)

from models.ws import WeatherStationModel
from util.registry import WEATHER_STATION_DRIVER_NAMESPACE, get, load_class_from_path

# Import example external driver which registers itself on import
import ws.drivers.example_external as example  # noqa: F401


def main():
    ws1 = WeatherStationModel(id="ws_test")

    ctor = get(WEATHER_STATION_DRIVER_NAMESPACE, "example_external")
    if ctor is None:
        raise SystemExit("Registered driver 'example_external' not found")

    inst = ctor(ws1)
    print("registered_instantiated:", type(inst).__name__)

    # Dynamic import path
    cls = load_class_from_path("ws.drivers.example_external:ExampleExternalDriver")
    inst2 = cls(WeatherStationModel(id="ws_test2"))
    print("dynamic_instantiated:", type(inst2).__name__)


if __name__ == '__main__':
    main()
