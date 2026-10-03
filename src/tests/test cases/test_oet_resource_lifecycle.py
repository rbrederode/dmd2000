from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from env.events import ObsEvent, TimerEvent
from models.app import AppModel
from models.obs import ObsModel, ObsState, ObsTransition
from models.tm import Allocation, AllocationState, ResourceAllocations
from obs.oet import ObservationExecutionTool
from tm.tm import TelescopeManager


def test_observation_fits_save_uses_available_fits_tools(tmp_path):
    observation = ObsModel(obs_id="obs-fits-save")

    assert observation.save_fits_to_disk(tmp_path) is True
    assert (tmp_path / "obs-fits-save-obs.fits").is_file()


@pytest.mark.parametrize("initial_state", [ObsState.READY, ObsState.ABORTED])
def test_release_resources_is_allowed_from_terminal_resource_states(initial_state):
    now = datetime.now(timezone.utc)
    observation = SimpleNamespace(
        obs_id=f"obs-{initial_state.name.lower()}",
        obs_state=initial_state,
        scheduling_block_start=now - timedelta(minutes=1),
        scheduling_block_end=now + timedelta(minutes=4),
        dsh_id="dish001",
        save_to_disk=lambda _output_dir: None,
        save_fits_to_disk=lambda _output_dir: None,
    )
    allocations = ResourceAllocations(
        alloc_list=[
            Allocation(
                resource_type="dish",
                resource_id="dish001",
                allocated_type="observation",
                allocated_id=observation.obs_id,
                state=AllocationState.ACTIVE,
                expires=observation.scheduling_block_end,
            ),
            Allocation(
                resource_type="digitiser",
                resource_id="dig001",
                allocated_type="observation",
                allocated_id=observation.obs_id,
                state=AllocationState.ACTIVE,
                expires=observation.scheduling_block_end,
            ),
        ]
    )
    telescope = SimpleNamespace(
        tel_mgr=SimpleNamespace(allocations=allocations),
        oda=SimpleNamespace(obs_store=SimpleNamespace(obs_list=[observation])),
        get_scan_store_dir=lambda: "/tmp",
    )
    oet = ObservationExecutionTool(
        telescope,
        SimpleNamespace(set_last_err=lambda message: message),
    )

    oet.process_obs_event(
        ObsEvent(obs=observation, transition=ObsTransition.RELEASE_RESOURCES)
    )

    assert observation.obs_state == ObsState.IDLE
    assert all(
        allocation.state == AllocationState.RELEASED
        for allocation in allocations.alloc_list
    )


def test_start_timer_queues_each_due_observation_once_and_schedules_the_next():
    now = datetime.now(timezone.utc)
    due_observation = SimpleNamespace(
        obs_id="obs-due",
        obs_state=ObsState.EMPTY,
        scheduling_block_start=now - timedelta(seconds=1),
        scheduling_block_end=now + timedelta(minutes=5),
    )
    future_observation = SimpleNamespace(
        obs_id="obs-future",
        obs_state=ObsState.EMPTY,
        scheduling_block_start=now + timedelta(minutes=10),
        scheduling_block_end=now + timedelta(minutes=15),
    )
    telescope = SimpleNamespace(
        oda=SimpleNamespace(
            obs_store=SimpleNamespace(
                obs_list=[due_observation, future_observation]
            )
        )
    )
    telescope_manager = TelescopeManager.__new__(TelescopeManager)
    telescope_manager.app_model = AppModel(app_name="tm")
    telescope_manager.stop = lambda: None
    telescope_manager.telmodel = telescope
    telescope_manager.oet = ObservationExecutionTool(telescope, telescope_manager)

    action = telescope_manager.process_timer_event(
        TimerEvent(id="obs_start_timer", name="obs_start_timer")
    )

    start_transitions = [
        transition
        for transition in action.obs_transitions
        if transition.get_transition() == ObsTransition.START
    ]
    assert [transition.get_obs().obs_id for transition in start_transitions] == [
        due_observation.obs_id
    ]
    assert len(action.timer_actions) == 1
    assert action.timer_actions[0].get_echo_data() is future_observation
