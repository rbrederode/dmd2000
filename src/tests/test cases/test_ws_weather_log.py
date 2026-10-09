from datetime import datetime, timedelta, timezone
from unittest import mock
import time
import zipfile

import pytest

from env.app import App
from models.comms import CommunicationStatus
from models.ws import WeatherData, WeatherStationDriverType, WeatherStationModel
from ws.ws import WeatherStation


@pytest.fixture
def station(tmp_path, monkeypatch):
    monkeypatch.setattr(App, "logs_dir", tmp_path)
    station = WeatherStation.__new__(WeatherStation)
    station.stop = lambda: None
    station.ws_model = WeatherStationModel(
        id="ws001", sim_mode="off", driver_type=WeatherStationDriverType.MODBUS,
        dm_connected=CommunicationStatus.ESTABLISHED,
        tm_connected=CommunicationStatus.ESTABLISHED,
        driver_poll_period=1500,
    )
    station.app_model = station.ws_model.app
    station.weather_driver = mock.Mock()
    station.weather_driver.get_poll_interval_ms.return_value = 1500
    station.weather_logger = station.get_weather_logger()
    yield station
    for handler in station.weather_logger.handlers[:]:
        station.weather_logger.removeHandler(handler)
        handler.close()


def sample():
    return WeatherData(
        ws_id="ws001",
        obs_time=datetime.fromisoformat("2026-10-07T14:00:00+01:00"),
        wind_speed=3.7, wind_direction=180.0, temperature=15.5,
        humidity=60.0, pressure=1013.25,
    )


def rows(tmp_path):
    return [line.split(" | ") for line in
            (tmp_path / "weather" / "ws.log").read_text().splitlines()]


def test_weather_row_has_requested_fields_and_only_utc_log_timestamp(station, tmp_path):
    # Fix the recording instant separately from the sensor's observation time.
    with mock.patch("logging.time.time", return_value=1791378001.0):
        station._log_weather(sample())
    row = rows(tmp_path)[0]
    assert row == [
        "2026-10-07 13:00:01,000 UTC", "ws001", "OK",
        "3.700000", "180.000000", "15.500000", "60.000000",
        "1013.250000",
    ]
    handler = station.weather_logger.handlers[0]
    assert handler.utc is True
    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == 0
    assert handler.formatter.converter is time.gmtime
    assert station.weather_logger.propagate is False


@pytest.mark.parametrize("connected", [True, False])
@pytest.mark.parametrize("sim_mode", ["off", "calm"])
def test_every_poll_logs_even_without_dish_manager(station, tmp_path, connected, sim_mode):
    station.ws_model.sim_mode = sim_mode
    station.ws_model.dm_connected = (
        CommunicationStatus.ESTABLISHED if connected
        else CommunicationStatus.NOT_ESTABLISHED
    )
    station.weather_driver.get_weather_data.return_value = sample()
    station._generate_weather = mock.Mock(return_value=sample())
    station._construct_dm_advice_message = mock.Mock(return_value=object())

    for _ in range(2):
        action = station.process_timer_event(None)
        assert len(action.msgs_to_remote) == int(connected)
        assert action.timer_actions[0].timer_action == 1500

    assert len(rows(tmp_path)) == 2
    assert all(row[2] == ("OK" if connected else "FAILED") for row in rows(tmp_path))
    if sim_mode == "off":
        assert station.weather_driver.get_weather_data.call_count == 2
        station._generate_weather.assert_not_called()
    else:
        assert station._generate_weather.call_count == 2
        station.weather_driver.get_weather_data.assert_not_called()


def test_failed_poll_and_unsupported_measurements_do_not_reuse_old_data(station, tmp_path):
    station.weather_driver.get_weather_data.side_effect = [sample(), RuntimeError("no answer")]
    station._construct_dm_advice_message = mock.Mock(return_value=object())
    station.process_timer_event(None)
    action = station.process_timer_event(None)
    assert rows(tmp_path)[1][3:] == ["None"] * 5
    assert action.msgs_to_remote == []
    assert len(action.timer_actions) == 1

    partial = sample()
    partial.wind_direction = partial.temperature = partial.humidity = partial.pressure = None
    station._log_weather(partial)
    assert rows(tmp_path)[2][3:8] == ["3.700000", "None", "None", "None", "None"]


@pytest.mark.parametrize("no_response", [True, False])
def test_read_failure_logs_traceback_only_for_unexpected_errors(station, caplog, no_response):
    minimalmodbus = pytest.importorskip("minimalmodbus")
    message = "No communication with the instrument (no answer)"
    error = minimalmodbus.NoResponseError(message) if no_response else RuntimeError(message)
    station.weather_driver.get_weather_data.side_effect = error

    with caplog.at_level("ERROR", logger="ws.ws"):
        assert station._read_weather() is None

    records = [record for record in caplog.records if record.name == "ws.ws"]
    assert len(records) == 1
    assert records[0].getMessage() == f"WeatherStation ws001 failed to read weather driver: {message}"
    assert (records[0].exc_info is None) == no_response
    assert station.app_model.last_err_msg == records[0].getMessage()


def test_weather_rollover_compresses_and_preserves_all_archives(station, tmp_path):
    handler = station.weather_logger.handlers[0]
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for days in range(2, 34):
        date = (midnight - timedelta(days=days)).date().isoformat()
        with zipfile.ZipFile(tmp_path / "weather" / f"ws.log.{date}.zip", "w") as archive:
            archive.writestr(f"ws.log.{date}", "old record\n")
    station._log_weather(sample())
    handler.rolloverAt = int(midnight.timestamp())
    handler.doRollover()
    station._log_weather(sample())

    backups = sorted((tmp_path / "weather").glob("ws.log.*.zip"))
    assert len(backups) == 33
    assert backups[0].name == f"ws.log.{(midnight - timedelta(days=33)).date()}.zip"
    archive_name = f"ws.log.{(midnight - timedelta(days=1)).date()}"
    with zipfile.ZipFile(tmp_path / "weather" / f"{archive_name}.zip") as archive:
        assert archive.namelist() == [archive_name]
        assert archive.getinfo(archive_name).compress_type == zipfile.ZIP_DEFLATED
        assert "ws001 | OK | 3.700000" in archive.read(archive_name).decode()
        assert archive.testzip() is None
    assert not (tmp_path / "weather" / archive_name).exists()
    assert len(rows(tmp_path)) == 1
