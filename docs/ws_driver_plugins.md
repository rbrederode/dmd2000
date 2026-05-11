Shared registry integration
===========================

Overview
--------
The shared registry lives in `util.registry` and can be used by weather-station drivers, dish drivers, and custom SDP pipeline steps.
The registry is namespace-based, so each subsystem can keep its own names while sharing the same implementation.

Namespaces
----------
- `weather_station_driver`
- `dish_driver`
- `pipeline_step`

Weather-station drivers
-----------------------
Register a driver constructor under a name and set `WeatherStationModel.driver_config` to that name or to a class path string.

    from ws.drivers.registry import register_driver
    from yourpkg.drivers.my_driver import MyDriver

    register_driver("acme_ws", MyDriver)

You can also use a class path such as `yourpkg.drivers.my_driver:MyDriver`.

Dish drivers
------------
`DishModel.driver_config` already accepts a `BaseModel`, so a custom driver package can ship its own config model.
The Dish Manager looks for `driver` or `name` on that config and resolves it through the shared registry before falling back to the built-in `DriverType` enum.

Example:

    from util.registry import register, DISH_DRIVER_NAMESPACE
    from yourpkg.dsh import MyDishDriver

    register(DISH_DRIVER_NAMESPACE, "my_dish", MyDishDriver)

Pipeline steps
--------------
`StepConfig.step` can now be either a built-in `StepType` or a custom string key / class path.
This means a custom step can be registered and then referred to in a pipeline config without changing the enum.

Example:

    from util.registry import register, PIPELINE_STEP_NAMESPACE
    from yourpkg.sdp.steps.custom import CustomStep

    register(PIPELINE_STEP_NAMESPACE, "custom_step", CustomStep)

Then configure a step with:

    StepConfig(step="custom_step", params={"pipeline": "cal"})

Notes
-----
- Built-in drivers and steps still work as before.
- A registry key is the simplest option for external plugins.
- A module path string is useful when you want configuration to point directly at a class.
