from types import SimpleNamespace
from unittest import mock

from models.ws import WeatherStationDriverType, WeatherStationModel
from ws.drivers.ads1115 import ADS1115Config, ADS1115WeatherStationDriver
from ws.drivers.modbus import ModbusConfig, ModbusWeatherStationDriver
from ws.wind import ADS1115VoltageReader
from ws.ws import WeatherStation


def test_modbus_close_releases_serial_port_once():
    instrument = SimpleNamespace(serial=mock.Mock())
    model = WeatherStationModel(
        id="ws001",
        driver_type=WeatherStationDriverType.MODBUS,
        driver_config=ModbusConfig(port="/dev/test"),
    )

    with mock.patch.object(ModbusWeatherStationDriver, "_build_instrument", return_value=instrument):
        driver = ModbusWeatherStationDriver(model)

    driver.close()
    driver.close()

    instrument.serial.close.assert_called_once_with()
    assert driver.instrument is None


def test_ads1115_driver_closes_each_voltage_reader_once():
    wind_reader = mock.Mock()
    precipitation_reader = mock.Mock()
    model = WeatherStationModel(
        id="ws001",
        driver_type=WeatherStationDriverType.ADS1115,
        driver_config=ADS1115Config(precipitation_channel=1),
    )

    with mock.patch.object(
        ADS1115WeatherStationDriver,
        "_build_voltage_reader",
        side_effect=[wind_reader, precipitation_reader],
    ):
        driver = ADS1115WeatherStationDriver(model)

    driver.close()
    driver.close()

    wind_reader.close.assert_called_once_with()
    precipitation_reader.close.assert_called_once_with()
    assert driver.wind_reader is None
    assert driver.precipitation_reader is None


def test_ads1115_voltage_reader_deinitialises_owned_i2c_bus_once():
    reader = ADS1115VoltageReader.__new__(ADS1115VoltageReader)
    reader.i2c = mock.Mock()
    i2c = reader.i2c
    reader.ads = object()
    reader.chan = object()
    reader._closed = False

    reader.close()
    reader.close()

    i2c.deinit.assert_called_once_with()
    assert reader.i2c is None
    assert reader.ads is None
    assert reader.chan is None


def test_weather_station_closes_current_driver_before_creating_replacement():
    calls = []
    current_driver = mock.Mock()
    current_driver.close.side_effect = lambda: calls.append("close")
    replacement_driver = mock.Mock()
    station = SimpleNamespace(
        weather_driver=current_driver,
        ws_model=WeatherStationModel(id="ws001", sim_mode="off"),
    )

    with mock.patch(
        "ws.ws.create_ws_driver",
        side_effect=lambda model: calls.append("create") or replacement_driver,
    ):
        WeatherStation._replace_weather_driver(station)

    assert calls == ["close", "create"]
    assert station.weather_driver is replacement_driver


def test_weather_station_closes_driver_when_switching_to_simulation():
    current_driver = mock.Mock()
    station = SimpleNamespace(
        weather_driver=current_driver,
        ws_model=WeatherStationModel(id="ws001", sim_mode="calm"),
    )

    with mock.patch("ws.ws.create_ws_driver") as create_driver:
        WeatherStation._replace_weather_driver(station)

    current_driver.close.assert_called_once_with()
    create_driver.assert_not_called()
    assert station.weather_driver is None

