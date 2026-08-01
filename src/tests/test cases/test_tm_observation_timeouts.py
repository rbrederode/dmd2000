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
