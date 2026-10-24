from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock
import time
import zipfile

import pytest

from env.app import App
from env.events import TimerEvent
from models.comms import CommunicationStatus
from models.health import HealthState
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
    station._weather_available = True
    station.weather_driver.get_poll_interval_ms.return_value = 1500
    station._poll_deadline = 100.0
    # Each synthetic event fires at its scheduled deadline unless a test
    # explicitly supplies a delayed clock or sensor read.
    monkeypatch.setattr("ws.ws.time.monotonic", lambda: station._poll_deadline)
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


def poll_event():
    return TimerEvent(id="test-poll", name="weather_polling_timer")


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
        action = station.process_timer_event(poll_event())
        assert len(action.msgs_to_remote) == int(connected)
        assert action.timer_actions[0].timer_action == 1500

    assert len(rows(tmp_path)) == 2
    assert all(row[2] == ("OK" if connected else "DEGRADED") for row in rows(tmp_path))
    if sim_mode == "off":
        assert station.weather_driver.get_weather_data.call_count == 2
        station._generate_weather.assert_not_called()
    else:
        assert station._generate_weather.call_count == 2
        station.weather_driver.get_weather_data.assert_not_called()


def test_failed_poll_and_unsupported_measurements_do_not_reuse_old_data(station, tmp_path):
    station.weather_driver.get_weather_data.side_effect = [sample(), RuntimeError("no answer")]
    station._construct_dm_advice_message = mock.Mock(return_value=object())
    station.process_timer_event(poll_event())
    action = station.process_timer_event(poll_event())
    assert rows(tmp_path)[1][3:] == ["None"] * 5
    assert action.msgs_to_remote == []
    assert len(action.timer_actions) == 1

    partial = sample()
    partial.wind_direction = partial.temperature = partial.humidity = partial.pressure = None
    station._log_weather(partial)
    assert rows(tmp_path)[2][3:8] == ["3.700000", "None", "None", "None", "None"]


@pytest.mark.parametrize("tm_connected, dm_connected", [
    (False, False), (False, True), (True, False), (True, True),
])
def test_health_startup_failure_and_recovery_for_all_manager_connections(
    station, tmp_path, tm_connected, dm_connected
):
    station.ws_model.tm_connected = (
        CommunicationStatus.ESTABLISHED if tm_connected else CommunicationStatus.NOT_ESTABLISHED
    )
    station.ws_model.dm_connected = (
        CommunicationStatus.ESTABLISHED if dm_connected else CommunicationStatus.NOT_ESTABLISHED
    )
    station._weather_available = None
    assert station.get_health_state() == HealthState.UNKNOWN
    station.weather_driver.get_weather_data.side_effect = [sample(), RuntimeError("anemometer disconnected"), sample()]
    station._construct_dm_advice_message = mock.Mock(return_value=object())
    healthy = HealthState.OK if tm_connected and dm_connected else HealthState.DEGRADED

    for expected in (healthy, HealthState.FAILED, healthy):
        station.process_timer_event(poll_event())
        assert station.get_health_state() == expected

    assert [row[2] for row in rows(tmp_path)] == [healthy.name, "FAILED", healthy.name]
    # Immediate failure does not depend on the driver's old failure threshold.
    assert station.ws_model.driver_failures == 0


@pytest.mark.parametrize("wind_speed", [None, float("nan"), float("inf"), -1.0])
def test_missing_or_invalid_wind_is_failed_even_when_other_measurements_exist(station, wind_speed):
    # Guard against malformed samples from a third-party driver as well as
    # missing values accepted by WeatherData itself.
    weather = SimpleNamespace(wind_speed=wind_speed, temperature=15.5)
    station.weather_driver.get_weather_data.return_value = weather
    station._construct_dm_advice_message = mock.Mock(return_value=object())
    station.process_timer_event(poll_event())
    assert station.get_health_state() == HealthState.FAILED


def test_missing_driver_is_failed_after_first_poll(station, tmp_path):
    station._weather_available = None
    station.weather_driver = None
    assert station.get_health_state() == HealthState.UNKNOWN
    station.process_timer_event(poll_event())
    assert station.get_health_state() == HealthState.FAILED
    assert rows(tmp_path)[0][2] == "FAILED"


def test_driver_replacement_returns_health_to_unknown_until_next_poll(station):
    station.ws_model.sim_mode = "calm"
    station._replace_weather_driver()
    assert station.get_health_state() == HealthState.UNKNOWN
    station._generate_weather = mock.Mock(return_value=sample())
    station._construct_dm_advice_message = mock.Mock(return_value=object())
    station.process_timer_event(poll_event())
    assert station.get_health_state() == HealthState.OK


@pytest.mark.parametrize("event", [
    None,
    TimerEvent(id="retry", name="TCPClient-dm", user_callback=lambda _: None),
    TimerEvent(id="other", name="unrelated-timer"),
])
def test_other_timers_do_not_read_log_or_reschedule_weather(station, tmp_path, event):
    station._generate_weather = mock.Mock()
    action = station.process_timer_event(event)
    station.weather_driver.get_weather_data.assert_not_called()
    station._generate_weather.assert_not_called()
    assert rows(tmp_path) == []
    assert action.timer_actions == []
    assert action.msgs_to_remote == []
    assert station._poll_deadline == 100.0


@pytest.mark.parametrize("interval_ms", [1000, 1500])
def test_poll_processing_time_does_not_accumulate(station, monkeypatch, interval_ms):
    station.weather_driver.get_poll_interval_ms.return_value = interval_ms
    clock = [100.0]
    monkeypatch.setattr("ws.ws.time.monotonic", lambda: clock[0])
    station._construct_dm_advice_message = mock.Mock(return_value=object())

    def read():
        clock[0] += 0.125
        return sample()

    station.weather_driver.get_weather_data.side_effect = read
    for index in range(20):
        clock[0] = 100.0 + index * interval_ms / 1000
        action = station.process_timer_event(poll_event())
        assert action.timer_actions[0].timer_action == interval_ms - 125
        assert station._poll_deadline == 100.0 + (index + 1) * interval_ms / 1000


def test_failed_sensor_read_still_preserves_poll_cadence(station, monkeypatch, tmp_path):
    station.weather_driver.get_poll_interval_ms.return_value = 1000
    clock = [100.0]
    monkeypatch.setattr("ws.ws.time.monotonic", lambda: clock[0])

    def read():
        clock[0] += 0.25
        raise RuntimeError("sensor unavailable")

    station.weather_driver.get_weather_data.side_effect = read
    action = station.process_timer_event(poll_event())
    assert action.timer_actions[0].timer_action == 750
    assert station._poll_deadline == 101.0
    assert rows(tmp_path)[0][3:] == ["None"] * 5


@pytest.mark.parametrize("start, read_duration, delay_ms, skipped", [
    (100.75, 0.125, 125, 0),  # Event spent time waiting in the queue.
    (100.0, 2.25, 750, 2),  # Slow sensor read crosses two deadlines.
    (102.25, 0.0, 750, 2),  # Event handling itself was delayed.
    (100.0, 1.0, 1000, 1),  # Finishing exactly at a deadline skips that slot.
])
def test_late_polls_skip_elapsed_deadlines_without_catch_up_bursts(
    station, monkeypatch, caplog, start, read_duration, delay_ms, skipped
):
    station.weather_driver.get_poll_interval_ms.return_value = 1000
    clock = [start]
    monkeypatch.setattr("ws.ws.time.monotonic", lambda: clock[0])
    station._construct_dm_advice_message = mock.Mock(return_value=object())

    def read():
        clock[0] += read_duration
        return sample()

    station.weather_driver.get_weather_data.side_effect = read
    with caplog.at_level("WARNING", logger="ws.ws"):
        action = station.process_timer_event(poll_event())
    assert station.weather_driver.get_weather_data.call_count == 1
    assert len(action.timer_actions) == 1
    assert action.timer_actions[0].timer_action == delay_ms
    assert station._poll_deadline == 101.0 + skipped
    if skipped:
        assert f"skipped {skipped} elapsed polling deadline(s)" in caplog.text
    else:
        assert "skipped" not in caplog.text


def test_fractional_millisecond_delay_rounds_up(station, monkeypatch):
    station.weather_driver.get_poll_interval_ms.return_value = 1000
    station._poll_deadline = 128.0
    monkeypatch.setattr("ws.ws.time.monotonic", lambda: 128.10025)
    assert station._next_poll_timer().timer_action == 900


def test_resync_resets_deadline_to_new_configured_interval(station, monkeypatch):
    station._load_model_from_profile = mock.Mock()
    station._replace_weather_driver = mock.Mock()
    station.get_args = mock.Mock(return_value=SimpleNamespace(entity_id="<undefined>", sim=None))
    station.weather_driver.get_poll_interval_ms.return_value = 250
    monkeypatch.setattr("ws.ws.time.monotonic", lambda: 200.0)
    action = station.process_resync()
    assert station._poll_deadline == 200.25
    assert action.timer_actions[0].name == "weather_polling_timer"
    assert action.timer_actions[0].timer_action == 250
    station.weather_driver.get_weather_data.assert_not_called()


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
