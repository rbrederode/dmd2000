import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from util.xbase import XSoftwareFailure

from dsh.dm import DM
from env.events import TimerEvent
from models.comms import CommunicationStatus
from models.dsh import DishMode, DriverType, PointingState
from models.health import HealthState
from models.target import PointingType


class SlewingMD01Driver:
    def __init__(self):
        self.dsh_model = SimpleNamespace(
            driver_type=DriverType.MD01,
            mode=DishMode.OPERATE,
            pointing_state=PointingState.SLEW,
        )
        self.target = SimpleNamespace(
            obs_id="obs-md01-drift",
            pointing=PointingType.DRIFT_SCAN,
        )

    def get_target_tuple(self):
        return "obs-md01-drift-0", self.target

    def get_pointing_state(self):
        return self.dsh_model.pointing_state

    def get_current_altaz(self):
        self.dsh_model.pointing_state = PointingState.READY

    def get_health_state(self):
        return HealthState.OK

    def get_poll_interval_ms(self):
        return 1000


def make_polling_manager(dish_driver):
    dish_manager = DM.__new__(DM)
    dish_manager.stop = lambda: None
    dish_manager.dish_drivers = {"dish002": dish_driver}
    dish_manager._get_dish_lock = lambda _dish_id: threading.RLock()
    dish_manager.dm_model = SimpleNamespace(
        tm_connected=CommunicationStatus.ESTABLISHED,
        weather_store=SimpleNamespace(is_ws_monitoring_enabled=lambda: False),
    )
    ready_updates = []

    def record_status(action, target_id, target, status, message, dish_id=None):
        ready_updates.append(
            {
                "target_id": target_id,
                "target": target,
                "status": status,
                "message": message,
                "dish_id": dish_id,
            }
        )
        return action

    dish_manager._send_status_adv_to_tm = record_status
    dish_manager._log_pointing = Mock()
    event = TimerEvent(
        id="md01-poll",
        name="driver_timer_dish002_SlewingMD01Driver",
    )

    return dish_manager, ready_updates, event


def test_md01_drift_scan_reports_ready_once_after_slew():
    dish_manager, ready_updates, event = make_polling_manager(SlewingMD01Driver())
    dish_manager.process_timer_event(event)
    dish_manager.process_timer_event(event)

    assert len(ready_updates) == 1
    assert ready_updates[0]["target_id"] == "obs-md01-drift-0"
    assert ready_updates[0]["status"] == "success"
    assert ready_updates[0]["dish_id"] == "dish002"
    assert dish_manager._log_pointing.call_count == 2


@pytest.mark.parametrize('mode', [DishMode.STANDBY_FP, DishMode.CONFIG, DishMode.UNKNOWN])
@pytest.mark.parametrize('pointing', [PointingType.SIDEREAL_TRACK, PointingType.OFFSET_SCAN, PointingType.FIVE_POINT_SCAN])
def test_ready_poll_does_not_start_retained_target_outside_operate(mode, pointing):
    driver = SlewingMD01Driver()
    driver.dsh_model.mode = mode
    driver.target.pointing = pointing
    driver.target.start_scan = Mock()
    driver.scan = Mock()
    driver.track = Mock()
    manager, updates, event = make_polling_manager(driver)
    for _ in range(3):
        manager.process_timer_event(event)
    driver.scan.assert_not_called()
    driver.track.assert_not_called()
    driver.target.start_scan.assert_not_called()
    assert updates == []
    assert manager._log_pointing.call_count == 3


@pytest.mark.parametrize('pointing,method', [(PointingType.SIDEREAL_TRACK,'track'),
    (PointingType.NON_SIDEREAL_TRACK,'track'),(PointingType.OFFSET_SCAN,'scan'),(PointingType.FIVE_POINT_SCAN,'scan')])
def test_acquisition_failure_advice_contains_actual_error(pointing, method):
    driver = SlewingMD01Driver()
    driver.target.pointing = pointing
    driver.target.start_scan = Mock()
    driver.set_last_err = lambda message: message
    setattr(driver,method,Mock(side_effect=XSoftwareFailure('controller rejected command')))
    manager, updates, event = make_polling_manager(driver)
    manager.process_timer_event(event)
    assert len(updates) == 1
    assert updates[0]['status'] == 'error'
    assert f'failed to {method}' in updates[0]['message']
    assert 'controller rejected command' in updates[0]['message']
    assert 'reached slew target' not in updates[0]['message']
