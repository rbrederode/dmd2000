from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from env.events import TimerEvent
from models.app import AppModel
from models.obs import ObsModel, ObsState, ObsTransition
from tm.tm import TelescopeManager


def test_configuring_timeout_returns_abort_action_and_records_error():
    telescope_manager = TelescopeManager.__new__(TelescopeManager)
    telescope_manager.app_model = AppModel(app_name="tm")
    telescope_manager.stop = lambda: None
    observation = ObsModel(
        obs_id="obs-config-timeout",
        obs_state=ObsState.CONFIGURING,
    )
    event = TimerEvent(
        id="config-timeout",
        name=f"obs_configuring_timer:{observation.obs_id}",
        user_ref=observation,
    )

    action = telescope_manager.process_timer_event(event)

    transitions = [item.get_transition() for item in action.obs_transitions]
    assert transitions == [ObsTransition.ABORT]
    assert telescope_manager.get_last_err_msg() == (
        "Telescope Manager observation obs-config-timeout configuration timeout "
        "occurred, aborting observation"
    )


def test_configuring_timeout_waits_for_dish_target_acquisition_estimate():
    telescope_manager = TelescopeManager.__new__(TelescopeManager)
    telescope_manager.app_model = AppModel(app_name="tm")
    telescope_manager.stop = lambda: None

    estimated_tgt_acq_dt = datetime.now(timezone.utc) + timedelta(seconds=30)
    dish = SimpleNamespace(tgt_acq_dt=estimated_tgt_acq_dt)
    telescope_manager.telmodel = SimpleNamespace(
        dsh_mgr=SimpleNamespace(get_dish_by_id=lambda dsh_id: dish)
    )
    observation = ObsModel(
        obs_id="obs-config-slewing",
        obs_state=ObsState.CONFIGURING,
        dsh_id="dish001",
        timeout_ms_config=10_000,
    )
    event = TimerEvent(
        id="config-timeout",
        name=f"obs_configuring_timer:{observation.obs_id}",
        user_ref=observation,
    )

    action = telescope_manager.process_timer_event(event)

    assert action.obs_transitions == []
    assert len(action.timer_actions) == 1
    timer = action.timer_actions[0]
    assert timer.get_name() == f"obs_configuring_timer:{observation.obs_id}"
    assert timer.get_echo_data() is observation
    # Processing time can reduce the estimate slightly before it is converted
    # to milliseconds, so allow a small scheduling margin.
    assert 39_000 <= timer.get_timer_action() <= 40_000
