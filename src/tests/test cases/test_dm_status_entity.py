from datetime import datetime, timezone
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api import protocol as dmd_protocol
from api import tm_dm
from dsh.dm import DM
from dsh.drivers.driver import DishDriver
from ipc.message import APIMessage
from models.comms import CommunicationStatus
from models.dsh import Capability, DishMode, DishModel, PointingState
from models.target import PointingType, TargetModel
from util.xbase import XSoftwareFailure


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


@pytest.mark.parametrize(
    "error, handled",
    [
        (
            ValueError(
                "interpolating from IERS_Auto using predictive values that are more\n"
                "than 30.0 days old."
            ),
            True,
        ),
        (XSoftwareFailure("coordinate conversion failed"), True),
        (ValueError("unexpected coordinate error"), False),
        (RuntimeError("unexpected driver failure"), False),
    ],
)
def test_target_failure_handles_known_errors_and_propagates_unexpected_errors(error, handled):
    dish = DishModel(
        dsh_id="dish001",
        mode=DishMode.CONFIG,
        capability=Capability.OPERATE_FULL,
        pointing_state=PointingState.READY,
    )
    driver = DishDriver.__new__(DishDriver)
    driver.dsh_model = dish
    driver._rlock = threading.RLock()
    driver.reset_pec_hist = Mock()
    driver._set_operate_mode = Mock()
    driver._calculate_desired_altaz = Mock(side_effect=error)
    driver.slew = Mock()
    manager = make_dish_manager()
    manager.dish_drivers = {dish.dsh_id: driver}
    manager._get_dish_lock = lambda _dish_id: threading.RLock()
    target = TargetModel(obs_id="obs-failed", tgt_idx=1, id="Galactic target")
    api_call = {
        "msg_type": dmd_protocol.MSG_TYPE_REQ,
        "action_code": dmd_protocol.ACTION_CODE_SET,
        "property": tm_dm.PROPERTY_TARGET,
        "value": target.to_dict(),
        "obs_data": {"obs_id": target.obs_id, "target_id": "obs-failed-1"},
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

    if not handled:
        with pytest.raises(type(error)) as caught:
            manager.process_tm_msg(
                event=None,
                api_msg=request.get_json_api_header(),
                api_call=api_call,
                payload=bytearray(),
            )
        assert caught.value is error
        assert dish.mode == DishMode.UNKNOWN
        driver.slew.assert_not_called()
        return

    responses = []
    for _ in range(2):
        action = manager.process_tm_msg(
            event=None,
            api_msg=request.get_json_api_header(),
            api_call=api_call,
            payload=bytearray(),
        )
        assert len(action.msgs_to_remote) == 1
        response = action.msgs_to_remote[0]
        responses.append(response.get_api_call())
        assert responses[-1]["status"] == dmd_protocol.STATUS_ERROR
        assert responses[-1]["obs_data"] == api_call["obs_data"]
        assert response.get_entity() == dish.dsh_id
        assert response.get_json_api_header()["timestamp"] == request.get_json_api_header()["timestamp"]
        tm_dm.TM_DM().validate(response.get_json_api_header())
        response.to_data()
        assert dish.target is None
        assert dish.tgt_id is None
        assert dish.mode == DishMode.UNKNOWN
        assert dish.pointing_state == PointingState.UNKNOWN

    assert str(error) in responses[0]["message"]
    driver.slew.assert_not_called()
    driver._calculate_desired_altaz.assert_called_once()


def test_stale_iers_failure_preserves_original_exception_as_cause():
    driver = DishDriver.__new__(DishDriver)
    driver.dsh_model = DishModel(dsh_id="dish001")
    error = ValueError(
        "interpolating from IERS_Auto using predictive values that are more\n"
        "than 30.0 days old."
    )
    driver._calculate_desired_altaz = Mock(side_effect=error)

    with pytest.raises(XSoftwareFailure, match="IERS Earth-orientation data are out of date") as caught:
        driver.get_desired_altaz(TargetModel(obs_id="obs-failed", tgt_idx=1))

    assert caught.value.__cause__ is error


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

        def estimate_slew_duration(self):
            return 0

    manager = make_dish_manager()
    manager.dm_model.tm_connected = CommunicationStatus.ESTABLISHED
    manager.dm_model.to_dict = lambda: {
        "dish_store": {"dish_list": [dish.to_dict()]},
        "weather_store": {"weather_data": []},
    }
    manager.dish_drivers = {dish.dsh_id: AlreadyOnTargetDriver()}
    driver = manager.dish_drivers[dish.dsh_id]
    driver.set_target_tuple = Mock(wraps=driver.set_target_tuple)
    driver.set_dish_mode = Mock(wraps=driver.set_dish_mode)
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

    response_started_at = datetime.now(timezone.utc)
    action = manager.process_tm_msg(
        event=None,
        api_msg=request.get_json_api_header(),
        api_call=api_call,
        payload=bytearray(),
    )
    response_completed_at = datetime.now(timezone.utc)

    assert len(action.msgs_to_remote) == 2
    target_response = action.msgs_to_remote[0].get_api_call()
    status_call = action.msgs_to_remote[1].get_api_call()
    acquired_dish = status_call["value"]["dish_store"]["dish_list"][0]
    assert status_call["property"] == dmd_protocol.PROPERTY_STATUS
    assert status_call["obs_data"] == {
        "obs_id": target.obs_id,
        "target_id": "obs001-0",
    }
    assert acquired_dish["tgt_acq_dt"]["value"] == acquired_at.isoformat()
    assert acquired_dish["pointing_altaz"] == {"alt": 80.0, "az": 10.0}
    assert target_response["slew"]["estimated_duration"] == 0
    estimated_tgt_acq_dt = datetime.fromisoformat(
        target_response["slew"]["estimated_tgt_acq_dt"]
    )
    assert response_started_at <= estimated_tgt_acq_dt <= response_completed_at
    assert dish.tgt_acq_dt == acquired_at
    tm_dm.TM_DM().validate(action.msgs_to_remote[0].get_json_api_header())
    action.msgs_to_remote[0].to_data()

    # Retrying a genuinely accepted target remains idempotent.
    retry_action = manager.process_tm_msg(
        event=None,
        api_msg=request.get_json_api_header(),
        api_call=api_call,
        payload=bytearray(),
    )
    assert retry_action.msgs_to_remote[0].get_api_call()["status"] == dmd_protocol.STATUS_SUCCESS
    driver.set_target_tuple.assert_called_once()
    driver.set_dish_mode.assert_called_once_with(DishMode.OPERATE)


def test_slew_estimate_is_persisted_in_dish_status_model():
    dish = DishModel(
        dsh_id="dish003",
        mode=DishMode.CONFIG,
        pointing_state=PointingState.READY,
        pointing_altaz={"alt": 0.0, "az": 359.0},
    )

    class SlewingDriver:
        dsh_model = dish

        def set_target_tuple(self, target_id, target):
            self.dsh_model.tgt_id = target_id
            self.dsh_model.target = target
            self.dsh_model.tgt_acq_dt = None

        def set_dish_mode(self, mode):
            self.dsh_model.mode = mode
            self.dsh_model.pointing_state = PointingState.SLEW

        def get_pointing_state(self):
            return self.dsh_model.pointing_state

        def estimate_slew_duration(self):
            return 144

    manager = make_dish_manager()
    manager.dm_model.to_dict = lambda: {
        "dish_store": {"dish_list": [dish.to_dict()]},
        "weather_store": {"weather_data": []},
    }
    manager.dish_drivers = {dish.dsh_id: SlewingDriver()}
    manager._get_dish_lock = lambda _dish_id: threading.RLock()

    target = TargetModel(
        obs_id="obs002",
        tgt_idx=8,
        id="Polaris",
        pointing=PointingType.SIDEREAL_TRACK,
        altaz={"alt": 52.8, "az": 0.8},
    )
    api_call = {
        "msg_type": dmd_protocol.MSG_TYPE_REQ,
        "action_code": dmd_protocol.ACTION_CODE_SET,
        "property": tm_dm.PROPERTY_TARGET,
        "value": target.to_dict(),
        "obs_data": {"obs_id": target.obs_id, "target_id": "obs002-8"},
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

    assert len(action.msgs_to_remote) == 1
    target_response = action.msgs_to_remote[0].get_api_call()
    response_estimate = datetime.fromisoformat(
        target_response["slew"]["estimated_tgt_acq_dt"]
    )
    status_dish = manager.dm_model.to_dict()["dish_store"]["dish_list"][0]

    assert target_response["slew"]["estimated_duration"] == 144
    assert dish.tgt_acq_dt == response_estimate
    assert status_dish["tgt_acq_dt"]["value"] == response_estimate.isoformat()
