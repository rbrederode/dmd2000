import logging
from types import MethodType, SimpleNamespace

import pytest

from api import protocol as protocol
from api import tm_dig, tm_sdp
from ipc.action import Action
from models.app import AppModel
from models.comms import CommunicationStatus
from models.dig import DigitiserModel
from models.obs import ObsModel, ObsState, ObsTransition
from models.scan import ScanModel
from models.target import TargetScanSet
from tm.tm import TelescopeManager


def make_manager():
    digitiser = DigitiserModel(
        dig_id="dig001", gain=20.0, tm_connected=CommunicationStatus.ESTABLISHED,
    )
    scan = ScanModel(obs_id="obs001", tgt_idx=0, gain=0.0)
    obs = ObsModel(
        obs_id="obs001", obs_state=ObsState.CONFIGURING, tgt_idx=0,
        target_scans=[TargetScanSet(tgt_idx=0, scans=[scan])],
    )
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = MethodType(lambda self: None, manager)
    manager.app_model = AppModel(app_name="tm")
    manager.dig_api = tm_dig.TM_DIG()
    manager._pending_auto_gain_requests = {}
    manager.telmodel = SimpleNamespace(
        dig_store=SimpleNamespace(last_update=None, get_dig_by_id=lambda _: digitiser),
        tel_mgr=SimpleNamespace(app=manager.app_model),
        oda=SimpleNamespace(obs_store=SimpleNamespace(
            get_obs_by_id=lambda obs_id: obs if obs_id == obs.obs_id else None,
        )),
        sdp=None,
    )
    return manager, digitiser, obs


def request_configuration(manager, token="AUTO1"):
    return manager.update_dig_configuration(
        {}, {"load_active": True, "gain": token, "dig_id": "dig001", "obs_id": "obs001"}, Action(),
    ).msgs_to_remote


def respond(manager, digitiser, request, value, status=protocol.STATUS_SUCCESS):
    call = dict(request.get_api_call())
    call.update(msg_type=protocol.MSG_TYPE_RSP, status=status, value=value)
    return manager.process_dig_entity_msg(
        event=None, api_msg={"entity": "dig001", "timestamp": request.get_timestamp()},
        api_call=call, payload=bytearray(), entity=digitiser,
    )


@pytest.mark.parametrize("token", ["AUTO", "AUTO1"])
@pytest.mark.parametrize("measured_gain", [20.0, 43.0])
def test_load_response_leaves_gain_pending_until_matching_calibration_result(token, measured_gain, caplog):
    manager, digitiser, obs = make_manager()
    load_request, gain_request = request_configuration(manager, token)
    caplog.set_level(logging.INFO, logger="tm.tm")

    assert manager._pending_auto_gain_requests["dig001"] == gain_request.get_timestamp()
    assert digitiser.gain == 20.0
    action = respond(manager, digitiser, load_request, True)

    assert action.obs_transitions == []
    assert "calibration completed" not in caplog.text
    assert manager._pending_auto_gain_requests["dig001"] == gain_request.get_timestamp()
    assert obs.auto_gain_cache == {}
    assert obs.target_scans[0].scans[0].gain == 0.0

    # Periodic hardware status can still contain the old gain during calibration.
    status_action = manager.process_dig_entity_msg(
        event=None, api_msg={"entity": "dig001"}, api_call={
            "msg_type": protocol.MSG_TYPE_ADV, "action_code": protocol.ACTION_CODE_SET,
            "property": protocol.PROPERTY_STATUS, "value": digitiser.to_dict(),
        }, payload=bytearray(), entity=digitiser,
    )
    assert "dig001" in manager._pending_auto_gain_requests
    assert status_action.obs_transitions == []

    result = respond(manager, digitiser, gain_request, measured_gain)

    assert manager._pending_auto_gain_requests == {}
    assert digitiser.gain == measured_gain
    assert obs.target_scans[0].scans[0].gain == measured_gain
    assert obs.auto_gain_cache == ({token: measured_gain} if token != "AUTO" else {})
    assert [t.get_transition() for t in result.obs_transitions] == [ObsTransition.CONFIGURE_RESOURCES]


def test_old_calibration_response_cannot_resolve_new_request():
    manager, digitiser, obs = make_manager()
    _, first = request_configuration(manager)
    _, second = request_configuration(manager)

    stale = respond(manager, digitiser, first, 30.0)

    assert stale.obs_transitions == []
    assert digitiser.gain == 20.0
    assert obs.auto_gain_cache == {}
    assert manager._pending_auto_gain_requests["dig001"] == second.get_timestamp()

    respond(manager, digitiser, second, 43.0)
    assert digitiser.gain == 43.0
    assert manager._pending_auto_gain_requests == {}


def test_failed_calibration_clears_pending_and_aborts_without_using_old_gain():
    manager, digitiser, obs = make_manager()
    _, request = request_configuration(manager)

    result = respond(manager, digitiser, request, None, protocol.STATUS_ERROR)

    assert manager._pending_auto_gain_requests == {}
    assert digitiser.gain == 20.0
    assert obs.auto_gain_cache == {}
    assert [t.get_transition() for t in result.obs_transitions] == [ObsTransition.ABORT]


def test_final_timeout_clears_pending_and_aborts():
    manager, digitiser, obs = make_manager()
    _, request = request_configuration(manager)

    result = manager.process_timer_event(SimpleNamespace(
        name=f"dig001_req_timer_final:{request.get_timestamp()}", user_ref=request,
    ))

    assert manager._pending_auto_gain_requests == {}
    assert digitiser.gain == 20.0
    assert obs.auto_gain_cache == {}
    assert [t.get_transition() for t in result.obs_transitions] == [ObsTransition.ABORT]


@pytest.mark.parametrize("sdp_gain", [20.0, 45.0])
def test_gain_result_rechecks_stale_load_state_unless_sdp_ack_is_pending(sdp_gain):
    manager, digitiser, obs = make_manager()
    manager.sdp_api = tm_sdp.TM_SDP()
    manager.telmodel.tel_mgr.sdp_connected = CommunicationStatus.ESTABLISHED
    manager.telmodel.sdp = SimpleNamespace(
        sdp_id="sdp001",
        dig_store=SimpleNamespace(get_dig_by_id=lambda _: SimpleNamespace(gain=sdp_gain)),
    )
    load_request, gain_request = request_configuration(manager)
    respond(manager, digitiser, load_request, True)
    # Reproduce TM's last status snapshot, taken during the calibration.
    digitiser.load_active = False

    result = respond(manager, digitiser, gain_request, 45.0)

    assert digitiser.gain == 45.0
    assert obs.auto_gain_cache == {"AUTO1": 45.0}
    assert digitiser.load_active is False
    if sdp_gain == 45.0:
        assert result.msgs_to_remote == []
        assert [t.get_transition() for t in result.obs_transitions] == [ObsTransition.CONFIGURE_RESOURCES]
        assert result.obs_transitions[0].get_obs() is obs
    else:
        assert result.obs_transitions == []
        assert len(result.msgs_to_remote) == 1
        assert result.msgs_to_remote[0].get_api_call()["value"]["gain"] == 45.0


def test_auto_gain_request_allows_forty_seconds_without_changing_property_timeout():
    manager, _, _ = make_manager()
    load_request, gain_request = request_configuration(manager)

    assert manager._get_request_timeout_ms(gain_request) == 40_000
    assert manager._get_request_timeout_ms(load_request) == manager.app_model.msg_timeout_ms
