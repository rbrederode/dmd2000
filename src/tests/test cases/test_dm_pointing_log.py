import threading
from pathlib import Path
from types import SimpleNamespace

from dsh.dm import DM
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
