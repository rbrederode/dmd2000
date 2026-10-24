import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from api import protocol as dmd_protocol
from api import tm_dm
from dsh.dm import DM
from dsh.drivers.driver import DishDriver
from env.app import App
from models.dsh import Capability, DishMode, DishModel, PointingState
from models.health import HealthState
from util import log
from util.format import fmt_pointing_value


def test_dedicated_logs_use_repository_log_directory():
    assert App.logs_dir == Path(log.repo_logs_dir)


def test_pointing_logger_writes_expected_daily_rotating_row(tmp_path, monkeypatch):
    monkeypatch.setattr(App, "logs_dir", tmp_path)

    dish_manager = DM.__new__(DM)
    dish_manager.stop = lambda: None
    dish_manager.app_model = SimpleNamespace(app_name="dm")
    dish_manager.pointing_logger = dish_manager.get_pointing_logger()
    dish = DishModel(
        dsh_id="dish002",
        capability=Capability.OPERATE_FULL,
        mode=DishMode.OPERATE,
        pointing_state=PointingState.SCAN,
        health=HealthState.OK,
        pointing_altaz={"az": 182.5, "alt": 35.25},
        desired_altaz={"az": 183.0, "alt": 35.5},
    )

    dish_manager._log_pointing(dish)
    handler = dish_manager.pointing_logger.handlers[0]
    handler.flush()

    line = (tmp_path / "pointing" / "dm.log").read_text().strip()
    fields = [field.strip() for field in line.split("|")]

    assert len(fields) == 10
    assert fields[0].endswith("UTC")
    assert fields[1:] == [
        "dish002",
        "OPERATE_FULL",
        "OPERATE",
        "SCAN",
        "OK",
        "182.500000",
        "35.250000",
        "183.000000",
        "35.500000",
    ]
    assert handler.when == "MIDNIGHT"
    assert handler.utc is True
    assert handler.backupCount == 30
    assert dish_manager.pointing_logger.propagate is False

    dish_manager.pointing_logger.handlers.clear()
    handler.close()


def test_fmt_pointing_value():
    assert fmt_pointing_value(None) == "None"
    assert fmt_pointing_value(1.25) == "1.250000"


def test_pointing_logger_serialises_concurrent_dish_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(App, "logs_dir", tmp_path)
    dish_manager = DM.__new__(DM)
    dish_manager.stop = lambda: None
    dish_manager.app_model = SimpleNamespace(app_name="dm")
    dish_manager.pointing_logger = dish_manager.get_pointing_logger()

    dishes = [
        DishModel(
            dsh_id=f"dish{index:03d}",
            pointing_altaz={"az": 180.0 + index, "alt": 35.0 + index},
        )
        for index in range(1, 5)
    ]
    workers = [
        threading.Thread(target=dish_manager._log_pointing, args=(dish,))
        for dish in dishes
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    handler = dish_manager.pointing_logger.handlers[0]
    handler.flush()
    lines = (tmp_path / "pointing" / "dm.log").read_text().splitlines()

    assert len(lines) == len(dishes)
    assert all(len(line.split("|")) == 10 for line in lines)

    dish_manager.pointing_logger.handlers.clear()
    handler.close()


def _pointing_driver(tmp_path, dish_id="dish002"):
    driver = DishDriver(
        DishModel(
            dsh_id=dish_id,
            latitude=53.0,
            longitude=-2.0,
            height=80.0,
        )
    )
    driver.set_pointing_log_dir(tmp_path / "pointing")
    (tmp_path / "pointing").mkdir()
    return driver


def test_get_pointing_for_time_range_filters_dish_and_rotated_logs(tmp_path):
    driver = _pointing_driver(tmp_path)
    log_dir = tmp_path / "pointing"
    (log_dir / "dm.log.2026-09-19").write_text(
        "2026-09-19 23:59:59,500 UTC | dish002 | OPERATE_FULL | OPERATE | SCAN | OK | 180.000000 | 35.000000 | 181.000000 | 36.000000\n"
        "2026-09-19 23:59:59,600 UTC | dish001 | OPERATE_FULL | OPERATE | SCAN | OK | 10.000000 | 20.000000 | 11.000000 | 21.000000\n",
        encoding="utf-8",
    )
    (log_dir / "dm.log").write_text(
        "2026-09-20 00:00:00,500 UTC | dish002 | OPERATE_FULL | OPERATE | SCAN | OK | 182.000000 | 37.000000 | None | None\n"
        "incomplete row\n",
        encoding="utf-8",
    )

    result = driver.get_pointing_for_time_range(
        {
            "from": "2026-09-19T23:59:59Z",
            "to": {"_type": "datetime", "value": "2026-09-20T00:00:01+00:00"},
        }
    )

    assert [row["datetime"] for row in result] == [
        "2026-09-19T23:59:59.500000+00:00",
        "2026-09-20T00:00:00.500000+00:00",
    ]
    assert all(row["dsh_id"] == "dish002" for row in result)
    assert result[0]["pointing_altaz"] == {"az": 180.0, "alt": 35.0}
    assert result[1]["desired_altaz"] == {"az": None, "alt": None}


def test_get_pointing_for_datetime_list_returns_nearest_samples(tmp_path):
    driver = _pointing_driver(tmp_path)
    (tmp_path / "pointing" / "dm.log").write_text(
        "2026-09-20 10:00:00,000 UTC | dish002 | OPERATE_FULL | OPERATE | TRACK | OK | 180.000000 | 35.000000 | 181.000000 | 36.000000\n"
        "2026-09-20 10:00:02,000 UTC | dish002 | OPERATE_FULL | OPERATE | TRACK | OK | 182.000000 | 37.000000 | 183.000000 | 38.000000\n",
        encoding="utf-8",
    )

    result = driver.get_pointing_for_time_range(
        [
            datetime(2026, 9, 20, 10, 0, 0, 250000, tzinfo=timezone.utc),
            "2026-09-20T10:00:01.750Z",
        ]
    )

    assert [row["pointing_altaz"]["az"] for row in result] == [180.0, 182.0]
    assert [row["offset_seconds"] for row in result] == [-0.25, 0.25]
    assert result[0]["requested_datetime"] == "2026-09-20T10:00:00.250000+00:00"


def test_pointing_lookup_reads_only_active_and_one_rollover_log(tmp_path, monkeypatch):
    driver = _pointing_driver(tmp_path)
    log_dir = tmp_path / "pointing"
    row = (
        "{timestamp} UTC | dish002 | OPERATE_FULL | OPERATE | TRACK | OK | "
        "{az:.6f} | 35.000000 | 181.000000 | 36.000000\n"
    )
    (log_dir / "dm.log.2026-09-19").write_text(
        row.format(timestamp="2026-09-19 23:59:59,900", az=179.0),
        encoding="utf-8",
    )
    (log_dir / "dm.log").write_text(
        row.format(timestamp="2026-09-20 00:00:00,100", az=180.0),
        encoding="utf-8",
    )
    # These retained logs must not be opened for a scan crossing the 19/20
    # rollover.
    (log_dir / "dm.log.2026-09-18").write_text("not relevant\n", encoding="utf-8")
    (log_dir / "dm.log.2026-09-17").write_text("not relevant\n", encoding="utf-8")

    opened = []
    original_reader = driver._read_log_lines_reverse

    def track_opened(path, block_size=64 * 1024):
        opened.append(path.name)
        yield from original_reader(path, block_size)

    monkeypatch.setattr(driver, "_read_log_lines_reverse", track_opened)

    result = driver.get_pointing_for_time_range(
        ["2026-09-19T23:59:59.950Z", "2026-09-20T00:00:00.050Z"]
    )

    assert opened == ["dm.log", "dm.log.2026-09-19"]
    assert [sample["pointing_altaz"]["az"] for sample in result] == [179.0, 180.0]


def test_recent_pointing_lookup_stops_after_bracketing_tail_record(tmp_path, monkeypatch):
    driver = _pointing_driver(tmp_path)
    log_path = tmp_path / "pointing" / "dm.log"
    old_rows = "".join(
        f"2026-09-20 09:00:{second % 60:02d},000 UTC | dish002 | OPERATE_FULL | OPERATE | TRACK | OK | 100.000000 | 30.000000 | 101.000000 | 31.000000\n"
        for second in range(5000)
    )
    recent_rows = (
        "2026-09-20 10:00:00,000 UTC | dish002 | OPERATE_FULL | OPERATE | TRACK | OK | 180.000000 | 35.000000 | 181.000000 | 36.000000\n"
        "2026-09-20 10:00:01,000 UTC | dish002 | OPERATE_FULL | OPERATE | TRACK | OK | 182.000000 | 37.000000 | 183.000000 | 38.000000\n"
        "2026-09-20 10:00:02,000 UTC | dish002 | OPERATE_FULL | OPERATE | TRACK | OK | 184.000000 | 39.000000 | 185.000000 | 40.000000\n"
    )
    log_path.write_text(old_rows + recent_rows, encoding="utf-8")

    parsed_lines = 0
    original_parser = driver._parse_pointing_log_line

    def count_parsed(line):
        nonlocal parsed_lines
        parsed_lines += 1
        return original_parser(line)

    monkeypatch.setattr(driver, "_parse_pointing_log_line", count_parsed)

    result = driver.get_pointing_for_time_range(
        ["2026-09-20T10:00:00.100Z", "2026-09-20T10:00:01.900Z"]
    )

    assert [sample["pointing_altaz"]["az"] for sample in result] == [180.0, 184.0]
    assert parsed_lines == 3


def test_pointing_response_does_not_require_obs_data_and_preserves_echo():
    dish_manager = DM.__new__(DM)
    dish_manager.stop = lambda: None
    dish_manager.tm_api = tm_dm.TM_DM()
    timestamp = datetime.now(timezone.utc)
    api_call = {
        "msg_type": dmd_protocol.MSG_TYPE_REQ,
        "action_code": dmd_protocol.ACTION_CODE_GET,
        "property": tm_dm.PROPERTY_POINTING,
        "value": [
            {"_type": "datetime", "value": timestamp.isoformat()},
        ],
        "message": "",
    }
    api_msg = {
        "api_version": dish_manager.tm_api.get_api_version(),
        "timestamp": timestamp.isoformat(),
        "from": "tm",
        "to": "dm",
        "entity": "dish003",
        "api_call": api_call,
        "echo_data": {"scan_id": "obs001-0-0-0"},
    }
    pointing_data = [{
        "datetime": timestamp.isoformat(),
        "pointing_altaz": {"alt": 35.0, "az": 180.0},
    }]

    response = dish_manager._construct_rsp_to_tm(
        status=dmd_protocol.STATUS_SUCCESS,
        message="Pointing data retrieved.",
        value=pointing_data,
        api_msg=api_msg,
        api_call=api_call,
    )

    assert response.get_api_call()["value"] == pointing_data
    assert "obs_data" not in response.get_api_call()
    assert response.get_echo_data() == {"scan_id": "obs001-0-0-0"}
