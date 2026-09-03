from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from api import protocol as dmd_protocol
from api import tm_dm
from models.dsh import DishModel, DishMode, PointingState
from models.obs import ObsState, ObsTransition
from models.target import PointingType, TargetModel
from tm.tm import TelescopeManager


def make_telescope_manager(dish, observation=None):
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    dish_manager = SimpleNamespace(
        get_dish_by_id=lambda dish_id: dish if dish_id == dish.dsh_id else None,
        last_update=None,
    )
    manager.telmodel = SimpleNamespace(
        dsh_mgr=dish_manager,
        oda=SimpleNamespace(
            obs_store=SimpleNamespace(
                get_obs_by_id=lambda obs_id: (
                    observation
                    if observation is not None and obs_id == observation.obs_id
                    else None
                )
            ),
        ),
    )
    manager.set_last_err = lambda message: message
    return manager


def target_response(target, obs_data=None):
    api_call = {
        "msg_type": dmd_protocol.MSG_TYPE_RSP,
        "action_code": dmd_protocol.ACTION_CODE_SET,
        "property": tm_dm.PROPERTY_TARGET,
        "status": dmd_protocol.STATUS_SUCCESS,
        "value": target.to_dict(),
    }
    if obs_data is not None:
        api_call["obs_data"] = obs_data
    return api_call


@pytest.mark.parametrize(
    "initial_state",
    [PointingState.READY, PointingState.TRACK, PointingState.SCAN],
)
def test_target_response_marks_pointing_unknown_until_acquisition_is_reported(
    initial_state,
):
    dish = DishModel(
        dsh_id="dish001",
        mode=DishMode.CONFIG,
        pointing_state=initial_state,
    )
    target = TargetModel(
        obs_id="obs001",
        tgt_idx=0,
        id="Sun",
        pointing=PointingType.DRIFT_SCAN,
        altaz={"alt": 45.0, "az": 180.0},
    )
    manager = make_telescope_manager(dish)

    manager.process_dm_msg(
        event=None,
        api_msg={
            "entity": dish.dsh_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        api_call=target_response(target),
        payload=bytearray(),
    )

    assert dish.tgt_id == "obs001-0"
    assert dish.pointing_state == PointingState.UNKNOWN


def test_target_response_preserves_ready_when_acquisition_is_immediate():
    dish = DishModel(
        dsh_id="dish001",
        mode=DishMode.CONFIG,
        pointing_state=PointingState.UNKNOWN,
    )
    target = TargetModel(
        obs_id="obs001",
        tgt_idx=0,
        id="Sun",
        pointing=PointingType.DRIFT_SCAN,
        altaz={"alt": 45.0, "az": 180.0},
    )
    manager = make_telescope_manager(dish)

    manager.process_dm_msg(
        event=None,
        api_msg={
            "entity": dish.dsh_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        api_call=target_response(
            target,
            obs_data={"obs_id": target.obs_id, "target_id": "obs001-0"},
        ),
        payload=bytearray(),
    )

    assert dish.pointing_state == PointingState.READY


def test_target_response_without_obs_data_wakes_configuring_observation():
    dish = DishModel(
        dsh_id="dish001",
        mode=DishMode.CONFIG,
        pointing_state=PointingState.SCAN,
    )
    target = TargetModel(
        obs_id="obs001",
        tgt_idx=4,
        id="Sun",
        pointing=PointingType.OFFSET_SCAN,
    )
    observation = SimpleNamespace(obs_id=target.obs_id, obs_state=ObsState.CONFIGURING)
    manager = make_telescope_manager(dish, observation)

    action = manager.process_dm_msg(
        event=None,
        api_msg={
            "entity": dish.dsh_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        api_call=target_response(target),
        payload=bytearray(),
    )

    transitions = [item.get_transition() for item in action.obs_transitions]
    assert transitions == [ObsTransition.CONFIGURE_RESOURCES]
    assert dish.pointing_state == PointingState.UNKNOWN
