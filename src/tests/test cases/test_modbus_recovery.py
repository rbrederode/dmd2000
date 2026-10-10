import errno
from unittest import mock

import minimalmodbus
import pytest
import serial

from models.ws import WeatherStationDriverType, WeatherStationModel
from ws.drivers.modbus import ModbusConfig, ModbusWeatherStationDriver


def make_driver(instrument):
    model = WeatherStationModel(
        id="ws002",
        driver_type=WeatherStationDriverType.MODBUS,
        driver_config=ModbusConfig(port="/dev/serial/by-id/test-adapter"),
    )
    with mock.patch.object(ModbusWeatherStationDriver, "_build_instrument", return_value=instrument):
        return ModbusWeatherStationDriver(model)


@pytest.mark.parametrize("error", [
    OSError(errno.EIO, "Input/output error"),
    serial.SerialException("device disconnected"),
])
def test_serial_error_reopens_on_next_poll(error):
    failed = mock.Mock()
    failed.read_register.side_effect = error
    replacement = mock.Mock()
    replacement.read_register.return_value = 123
    driver = make_driver(failed)

    with mock.patch.object(driver, "_build_instrument", return_value=replacement) as build:
        with pytest.raises(type(error)):
            driver.get_weather_data()
        failed.serial.close.assert_called_once_with()
        assert driver.instrument is None
        build.assert_not_called()
        assert driver.ws_model.driver_failures == 1

        weather = driver.get_weather_data()
        assert weather.wind_speed == pytest.approx(12.3)
        assert driver.ws_model.driver_failures == 0
        build.assert_called_once_with()

    driver.close()
    replacement.serial.close.assert_called_once_with()


def test_posix_buffer_error_recovers_even_if_close_fails():
    termios = pytest.importorskip("termios")
    failed = mock.Mock()
    failed.read_register.side_effect = termios.error(errno.EIO, "Input/output error")
    failed.serial.close.side_effect = OSError(errno.EIO, "close failed")
    driver = make_driver(failed)

    with pytest.raises(termios.error):
        driver.get_weather_data()
    assert driver.instrument is None

    replacement = mock.Mock()
    replacement.read_register.return_value = 50
    with mock.patch.object(driver, "_build_instrument", return_value=replacement):
        assert driver.get_weather_data().wind_speed == 5.0


def test_missing_adapter_is_retried_until_it_returns():
    failed = mock.Mock()
    failed.read_register.side_effect = OSError(errno.EIO, "disconnected")
    driver = make_driver(failed)
    with pytest.raises(OSError):
        driver.get_weather_data()

    replacement = mock.Mock()
    replacement.read_register.return_value = 70
    with mock.patch.object(driver, "_build_instrument", side_effect=[
        serial.SerialException("adapter absent"),
        serial.SerialException("adapter still absent"),
        replacement,
    ]) as build:
        for _ in range(2):
            with pytest.raises(serial.SerialException):
                driver.get_weather_data()
            assert driver.instrument is None
        assert driver.get_weather_data().wind_speed == 7.0
        assert build.call_count == 3
        assert driver.ws_model.driver_failures == 0


def test_sensor_timeout_keeps_serial_connection():
    instrument = mock.Mock()
    instrument.read_register.side_effect = minimalmodbus.NoResponseError("sensor silent")
    driver = make_driver(instrument)
    with pytest.raises(minimalmodbus.NoResponseError):
        driver.get_weather_data()
    assert driver.instrument is instrument
    instrument.serial.close.assert_not_called()


def test_explicitly_closed_driver_does_not_reconnect():
    driver = make_driver(mock.Mock())
    driver.close()
    with mock.patch.object(driver, "_build_instrument") as build:
        with pytest.raises(RuntimeError, match="closed"):
            driver.get_weather_data()
        build.assert_not_called()
