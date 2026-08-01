import threading
from types import SimpleNamespace

from dsh.dm import DM
from env.events import TimerEvent
from models.comms import CommunicationStatus
from models.dsh import DriverType, PointingState
from models.health import HealthState
from models.target import PointingType


class SlewingMD01Driver:
    def __init__(self):
        self.dsh_model = SimpleNamespace(
            driver_type=DriverType.MD01,
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


def test_md01_drift_scan_reports_ready_once_after_slew():
    dish_driver = SlewingMD01Driver()
    dish_manager = DM.__new__(DM)
    dish_manager.stop = lambda: None
    dish_manager.dish_drivers = {"dish002": dish_driver}
    dish_manager._get_dish_lock = lambda _dish_id: threading.RLock()
    dish_manager.dm_model = SimpleNamespace(
        tm_connected=CommunicationStatus.ESTABLISHED,
        weather_store=SimpleNamespace(is_ws_monitoring_enabled=lambda: False),
    )
    ready_updates = []

    def record_status(action, target_id, target, status, message):
        ready_updates.append(
            {
                "target_id": target_id,
                "target": target,
                "status": status,
                "message": message,
            }
        )
        return action

    dish_manager._send_status_adv_to_tm = record_status
    event = TimerEvent(
        id="md01-poll",
        name="driver_timer_dish002_SlewingMD01Driver",
    )

    dish_manager.process_timer_event(event)
    dish_manager.process_timer_event(event)

    assert len(ready_updates) == 1
    assert ready_updates[0]["target_id"] == "obs-md01-drift-0"
    assert ready_updates[0]["status"] == "success"
