from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from env.events import ObsEvent, TimerEvent
from ipc.action import Action
from models.app import AppModel
from models.comms import CommunicationStatus
from models.dsh import Capability, DishMode
from models.health import HealthState
from models.obs import ObsModel, ObsState, ObsTransition
from models.tm import Allocation, AllocationState, ResourceAllocations, ResourceType
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


def make_resource_assignment_oet(owner_state):
    now = datetime.now(timezone.utc)
    owner = ObsModel(
        obs_id="obs-aborted",
        obs_state=owner_state,
        dsh_id="dish001",
        scheduling_block_start=now - timedelta(minutes=2),
        scheduling_block_end=now + timedelta(minutes=10),
    )
    requester = ObsModel(
        obs_id="obs-new",
        obs_state=ObsState.IDLE,
        dsh_id="dish001",
        scheduling_block_start=now - timedelta(minutes=1),
        scheduling_block_end=now + timedelta(minutes=5),
    )
    allocations = ResourceAllocations(
        alloc_list=[
            Allocation(
                resource_type=ResourceType.DISH.value,
                resource_id="dish001",
                allocated_type=ResourceType.OBS.value,
                allocated_id=owner.obs_id,
                state=AllocationState.ACTIVE,
                expires=owner.scheduling_block_end,
            ),
            Allocation(
                resource_type=ResourceType.DIGITISER.value,
                resource_id="dig001",
                allocated_type=ResourceType.OBS.value,
                allocated_id=owner.obs_id,
                state=AllocationState.ACTIVE,
                expires=owner.scheduling_block_end,
            ),
        ]
    )
    dish = SimpleNamespace(
        dsh_id="dish001",
        dig_id="dig001",
        capability=Capability.OPERATE_FULL,
        mode=DishMode.STANDBY_FP,
        latitude=53.0,
        longitude=-2.0,
        height=80.0,
    )
    digitiser = SimpleNamespace(
        dig_id="dig001",
        app=SimpleNamespace(health=HealthState.OK),
    )
    telescope = SimpleNamespace(
        tel_mgr=SimpleNamespace(allocations=allocations),
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
        oda=SimpleNamespace(
            obs_store=SimpleNamespace(
                obs_list=[owner, requester],
                get_obs_by_id=lambda obs_id: next(
                    (obs for obs in (owner, requester) if obs.obs_id == obs_id),
                    None,
                ),
            )
        ),
    )
    oet = ObservationExecutionTool(
        telescope,
        SimpleNamespace(set_last_err=lambda message: message),
    )
    return oet, owner, requester, allocations


def test_new_observation_preempts_resources_retained_by_aborted_observation():
    oet, owner, requester, allocations = make_resource_assignment_oet(ObsState.ABORTED)

    assert oet.assign_resources(requester, Action()) is True

    owner_allocations = allocations.get_allocations(
        allocated_type=ResourceType.OBS.value,
        allocated_id=owner.obs_id,
        include_expired=True,
    )
    assert all(allocation.state == AllocationState.RELEASED for allocation in owner_allocations)
    assert owner.obs_state == ObsState.ABORTED
    assert all(
        allocations.get_active_allocation(resource_type, resource_id).allocated_id
        == requester.obs_id
        for resource_type, resource_id in (
            (ResourceType.DISH.value, "dish001"),
            (ResourceType.DIGITISER.value, "dig001"),
        )
    )


def test_new_observation_does_not_preempt_observation_that_was_reset():
    oet, owner, requester, allocations = make_resource_assignment_oet(ObsState.IDLE)

    assert oet.assign_resources(requester, Action()) is False

    assert all(
        allocations.get_active_allocation(resource_type, resource_id).allocated_id
        == owner.obs_id
        for resource_type, resource_id in (
            (ResourceType.DISH.value, "dish001"),
            (ResourceType.DIGITISER.value, "dig001"),
        )
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
