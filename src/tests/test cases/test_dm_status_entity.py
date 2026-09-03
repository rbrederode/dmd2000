from datetime import datetime, timezone
import threading
from types import SimpleNamespace

from api import protocol as dmd_protocol
from api import tm_dm
from dsh.dm import DM
from ipc.message import APIMessage
from models.comms import CommunicationStatus
from models.dsh import DishMode, DishModel, PointingState
from models.target import PointingType, TargetModel


def make_dish_manager():
    manager = DM.__new__(DM)
    manager.stop = lambda: None
    manager.tm_api = SimpleNamespace(get_api_version=lambda: "1.0")
    manager.dm_model = SimpleNamespace(
        app=SimpleNamespace(app_name=dmd_protocol.DM),
        to_dict=lambda: {"weather_store": {"weather_data": []}},
    )
    return manager


def test_dish_specific_status_advice_contains_dish_entity():
    manager = make_dish_manager()

    message = manager._construct_status_adv_to_tm(
        status=dmd_protocol.STATUS_ERROR,
        message="Dish dish002 health state is FAILED",
        dish_id="dish002",
    )

    assert message.get_entity() == "dish002"


def test_manager_wide_status_advice_has_no_dish_entity():
    manager = make_dish_manager()

    message = manager._construct_status_adv_to_tm()

    assert message.get_entity() is None


def test_already_on_target_response_includes_immediate_acquisition_status():
    acquired_at = datetime(2026, 8, 25, 19, 58, 28, tzinfo=timezone.utc)
    dish = DishModel(
        dsh_id="dish001",
        mode=DishMode.CONFIG,
        pointing_state=PointingState.READY,
        pointing_altaz={"alt": 80.0, "az": 10.0},
    )

    class AlreadyOnTargetDriver:
        dsh_model = dish

        def set_target_tuple(self, target_id, target):
            self.dsh_model.tgt_id = target_id
            self.dsh_model.target = target

        def set_dish_mode(self, mode):
            self.dsh_model.mode = mode
            self.dsh_model.pointing_state = PointingState.READY
            self.dsh_model.tgt_acq_dt = acquired_at

        def get_pointing_state(self):
            return self.dsh_model.pointing_state

    manager = make_dish_manager()
    manager.dm_model.tm_connected = CommunicationStatus.ESTABLISHED
    manager.dm_model.to_dict = lambda: {
        "dish_store": {"dish_list": [dish.to_dict()]},
        "weather_store": {"weather_data": []},
    }
    manager.dish_drivers = {dish.dsh_id: AlreadyOnTargetDriver()}
    manager._get_dish_lock = lambda _dish_id: threading.RLock()

    target = TargetModel(
        obs_id="obs001",
        tgt_idx=0,
        id="Sun",
        pointing=PointingType.DRIFT_SCAN,
        altaz={"alt": 80.0, "az": 10.0},
    )
    api_call = {
        "msg_type": dmd_protocol.MSG_TYPE_REQ,
        "action_code": dmd_protocol.ACTION_CODE_SET,
        "property": tm_dm.PROPERTY_TARGET,
        "value": target.to_dict(),
        "obs_data": {"obs_id": target.obs_id, "target_id": "obs001-0"},
    }
    request = APIMessage()
    request.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=dmd_protocol.TM,
        to_system=dmd_protocol.DM,
        entity=dish.dsh_id,
        api_call=api_call,
    )

    action = manager.process_tm_msg(
        event=None,
        api_msg=request.get_json_api_header(),
        api_call=api_call,
        payload=bytearray(),
    )

    assert len(action.msgs_to_remote) == 2
    status_call = action.msgs_to_remote[1].get_api_call()
    acquired_dish = status_call["value"]["dish_store"]["dish_list"][0]
    assert status_call["property"] == dmd_protocol.PROPERTY_STATUS
    assert status_call["obs_data"] == {
        "obs_id": target.obs_id,
        "target_id": "obs001-0",
    }
    assert acquired_dish["tgt_acq_dt"]["value"] == acquired_at.isoformat()
    assert acquired_dish["pointing_altaz"] == {"alt": 80.0, "az": 10.0}
