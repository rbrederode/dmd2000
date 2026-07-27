from datetime import datetime, timezone
from types import SimpleNamespace

from env.events import ObsEvent
from ipc.action import Action
from models.comms import CommunicationStatus
from models.dsh import Capability, DishMode, Feed
from models.health import HealthState
from models.obs import ObsModel, ObsState, ObsTransition
from obs import oet as oet_module
from obs.oet import ObservationExecutionTool


def _configured_resource_fixture():
    observation = SimpleNamespace(
        obs_id="obs-disconnected",
        dsh_id="dish001",
        tgt_idx=0,
        tgt_scan=0,
        obs_state=ObsState.IDLE,
        timeout_ms_config=60_000,
        scheduling_block_start=None,
    )
    target_config = SimpleNamespace(
        feed_type=Feed.H3T_1420,
        bandwidth=1_000_000.0,
        sample_rate=2_048_000.0,
        gain=37.0,
        spectral_resolution=2048,
        filter_bank=None,
    )
    target_scan = SimpleNamespace(
        center_freq=1_420_000_000.0,
        gain=37.0,
        freq_scan=0,
    )
    target_scan_set = SimpleNamespace(scan_duration=60)
    target = SimpleNamespace()

    observation.get_target_config_by_index = lambda _index: target_config
    observation.get_current_tgt_scan_set = lambda: target_scan_set
    observation.get_current_tgt_scan = lambda: target_scan
    observation.get_target_by_index = lambda _index: target

    dish = SimpleNamespace(
        dsh_id="dish001",
        dig_id="dig001",
        tgt_id="obs-disconnected-0",
    )
    digitiser = SimpleNamespace(
        dig_id="dig001",
        tm_connected=CommunicationStatus.NOT_ESTABLISHED,
        load_active=False,
        center_freq=target_scan.center_freq,
        bandwidth=target_config.bandwidth,
        sample_rate=target_config.sample_rate,
        gain=target_config.gain,
        scanning=False,
    )
    sdp_digitiser = SimpleNamespace(
        dig_id="dig001",
        center_freq=target_scan.center_freq,
        bandwidth=target_config.bandwidth,
        sample_rate=target_config.sample_rate,
        gain=target_config.gain,
        channels=target_config.spectral_resolution,
        filter_bank=target_config.filter_bank,
        scan_duration=target_scan_set.scan_duration,
        scanning={
            "obs_id": observation.obs_id,
            "tgt_idx": observation.tgt_idx,
            "freq_scan": target_scan.freq_scan,
        },
        load_active=False,
    )
    telescope = SimpleNamespace(
        dsh_mgr=SimpleNamespace(dish_store=SimpleNamespace(dish_list=[dish])),
        dig_store=SimpleNamespace(dig_list=[digitiser]),
        sdp=SimpleNamespace(
            sdp_id="sdp001",
            dig_store=SimpleNamespace(dig_list=[sdp_digitiser]),
        ),
    )

    oet = ObservationExecutionTool(
        telescope,
        SimpleNamespace(set_last_err=lambda message: message),
    )
    oet.is_on_target = lambda *_args: True
    return oet, observation


def test_disconnected_digitiser_stays_configuring_until_timeout(monkeypatch):
    oet, observation = _configured_resource_fixture()
    monkeypatch.setattr(
        oet_module.Timer,
        "manager",
        SimpleNamespace(get_timers_by_name=lambda _name: []),
    )

    action = oet.process_obs_event(
        ObsEvent(obs=observation, transition=ObsTransition.CONFIGURE_RESOURCES)
    )

    transitions = [item.get_transition() for item in action.obs_transitions]
    config_timers = [
        item
        for item in action.timer_actions
        if item.get_name() == f"obs_configuring_timer:{observation.obs_id}"
    ]

    assert observation.obs_state == ObsState.CONFIGURING
    assert ObsTransition.READY not in transitions
    assert len(config_timers) == 1
    assert config_timers[0].get_timer_action() == observation.timeout_ms_config


def test_disconnected_digitiser_aborts_instead_of_starting_scan():
    dish = SimpleNamespace(dsh_id="dish001", dig_id="dig001")
    digitiser = SimpleNamespace(
        dig_id="dig001",
        tm_connected=CommunicationStatus.NOT_ESTABLISHED,
    )
    telescope = SimpleNamespace(
        dsh_mgr=SimpleNamespace(dish_store=SimpleNamespace(dish_list=[dish])),
        dig_store=SimpleNamespace(dig_list=[digitiser]),
    )
    oet = ObservationExecutionTool(
        telescope,
        SimpleNamespace(set_last_err=lambda message: message),
    )
    observation = ObsModel(
        obs_id="obs-disconnected",
        dsh_id="dish001",
        obs_state=ObsState.READY,
    )

    action = oet.process_obs_event(
        ObsEvent(obs=observation, transition=ObsTransition.READY)
    )

    transitions = [item.get_transition() for item in action.obs_transitions]
    assert transitions == [ObsTransition.ABORT]
    assert ObsTransition.SCAN_STARTED not in transitions


def test_unhealthy_digitiser_updates_telescope_manager_last_error():
    dish = SimpleNamespace(
        dsh_id="dish001",
        dig_id="dig001",
        capability=Capability.OPERATE_FULL,
        mode=DishMode.STANDBY_FP,
    )
    digitiser = SimpleNamespace(
        dig_id="dig001",
        app=SimpleNamespace(health=HealthState.UNKNOWN),
    )
    telescope = SimpleNamespace(
        dsh_mgr=SimpleNamespace(
            dish_store=SimpleNamespace(dish_list=[dish]),
            tm_connected=CommunicationStatus.ESTABLISHED,
            app=SimpleNamespace(health=HealthState.OK),
        ),
        dig_store=SimpleNamespace(dig_list=[digitiser]),
    )
    tm_app = SimpleNamespace(last_err_msg=None, last_err_dt=None)
    tm = SimpleNamespace(app_model=tm_app)

    def set_last_err(message):
        tm.app_model.last_err_msg = message
        tm.app_model.last_err_dt = datetime.now(timezone.utc)
        return message

    tm.set_last_err = set_last_err
    oet = ObservationExecutionTool(telescope, tm)
    observation = SimpleNamespace(obs_id="obs-unhealthy", dsh_id="dish001")

    action = Action()
    assigned = oet.assign_resources(observation, action)

    transitions = [item.get_transition() for item in action.obs_transitions]
    assert assigned is False
    assert transitions == [ObsTransition.ABORT]
    assert tm.app_model.last_err_msg == (
        "Observation Execution Tool found Digitiser dig001, but it is not currently healthy. "
        "Health state UNKNOWN. Cannot assign digitiser to observation obs-unhealthy. "
        "Aborting observation."
    )
    assert tm.app_model.last_err_dt is not None


def test_assign_resources_snapshots_resolved_dish_location():
    dish = SimpleNamespace(
        dsh_id="dish001",
        dig_id="dig001",
        capability=Capability.OPERATE_FULL,
        mode=DishMode.STANDBY_FP,
        latitude=53.23409,
        longitude=-2.305533,
        height=78.0,
    )
    digitiser = SimpleNamespace(
        dig_id="dig001",
        app=SimpleNamespace(health=HealthState.OK),
    )

    class Allocations:
        def request_allocation(self, **_kwargs):
            return SimpleNamespace()

        def get_active_allocation(self, **_kwargs):
            return None

        def handle_resource_allocation(self, **_kwargs):
            return True

    telescope = SimpleNamespace(
        dsh_mgr=SimpleNamespace(
            dish_store=SimpleNamespace(dish_list=[dish]),
            tm_connected=CommunicationStatus.ESTABLISHED,
            app=SimpleNamespace(health=HealthState.OK),
        ),
        dig_store=SimpleNamespace(dig_list=[digitiser]),
        sdp=SimpleNamespace(
            tm_connected=CommunicationStatus.ESTABLISHED,
            app=SimpleNamespace(health=HealthState.OK),
        ),
        tel_mgr=SimpleNamespace(allocations=Allocations()),
    )
    observation = ObsModel(
        obs_id="obs-location",
        dsh_id="dish001",
        latitude=0.0,
        longitude=0.0,
        height=0.0,
    )
    oet = ObservationExecutionTool(telescope, SimpleNamespace())

    assigned = oet.assign_resources(observation, Action())

    assert assigned is True
    assert observation.latitude == dish.latitude
    assert observation.longitude == dish.longitude
    assert observation.height == dish.height
    assert ObsModel.from_dict(observation.to_dict()).height == dish.height
