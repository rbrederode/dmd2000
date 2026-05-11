#!/usr/bin/env python3
import logging

logging.basicConfig(level=logging.INFO)

import numpy as np

from models.dsh import DishModel
from models.pipeline import PipelineConfig, StepConfig
from models.ws import WeatherStationModel
from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory, ProcessingStep
from util.registry import (
    DISH_DRIVER_NAMESPACE,
    PIPELINE_STEP_NAMESPACE,
    WEATHER_STATION_DRIVER_NAMESPACE,
    get,
    register,
)
from ws.drivers.driver import WeatherStationDriver


class CustomWeatherDriver(WeatherStationDriver):
    def _get_wind_speed(self):
        return 1.0

    def _get_precipitation(self):
        return 0.0

    def _get_wind_direction(self):
        return 90.0

    def _get_temperature(self):
        return 20.0

    def _get_humidity(self):
        return 50.0

    def _get_pressure(self):
        return 1013.0


class CustomDishDriver:
    def __init__(self, dsh_model):
        self.dsh_model = dsh_model

    def get_poll_interval_ms(self):
        return 1234


class CustomStep(ProcessingStep):
    def process(self, context, signal):
        return signal

    @classmethod
    def describe(cls):
        return "Custom registry-backed pipeline step."


def main():
    register(WEATHER_STATION_DRIVER_NAMESPACE, "custom_weather", CustomWeatherDriver)
    register(DISH_DRIVER_NAMESPACE, "custom_dish", CustomDishDriver)
    register(PIPELINE_STEP_NAMESPACE, "custom_step", CustomStep)

    weather_ctor = get(WEATHER_STATION_DRIVER_NAMESPACE, "custom_weather")
    dish_ctor = get(DISH_DRIVER_NAMESPACE, "custom_dish")
    step_ctor = get(PIPELINE_STEP_NAMESPACE, "custom_step")

    if weather_ctor is None or dish_ctor is None or step_ctor is None:
        raise SystemExit("Registry lookup failed for one or more custom entries")

    ws_driver = weather_ctor(WeatherStationModel(id="ws_test"))
    dish_driver = dish_ctor(DishModel(dsh_id="dish_test"))

    pipeline_factory = ProcessingPipelineFactory(PipelineConfig())
    step_cfg = StepConfig(step="custom_step", params={"pipeline": "cal"})
    step_cls = pipeline_factory.get_step_class(step_cfg.step)
    step = step_cls(step_cfg)

    signal = np.array([1.0, 2.0, 3.0])
    processed = step.process({"pipeline": "cal"}, signal)

    print("weather_driver:", type(ws_driver).__name__)
    print("dish_driver:", type(dish_driver).__name__)
    print("pipeline_step:", type(step).__name__)
    print("signal_preserved:", bool(np.array_equal(signal, processed)))


if __name__ == "__main__":
    main()
