import threading
from datetime import datetime, timezone
from types import MethodType, SimpleNamespace

from api import protocol as dmd_protocol
from api import tm_dig
from dig.dig import Digitiser
from models.comms import CommunicationStatus
from models.dig import DigitiserModel
from models.obs import ObsModel, ObsState, ObsTransition
from tm.tm import TelescopeManager


def _digitiser_with_failed_sdr_read():
    digitiser = Digitiser.__new__(Digitiser)
    digitiser.stop = MethodType(lambda self: None, digitiser)
    digitiser.dig_model = DigitiserModel(
        dig_id="dig001",
        scanning={"obs_id": "obs001", "tgt_idx": 2, "freq_scan": 0},
        sdr_connected=CommunicationStatus.NOT_ESTABLISHED,
        tm_connected=CommunicationStatus.ESTABLISHED,
        sdp_connected=CommunicationStatus.ESTABLISHED,
    )
    digitiser.tm_api = tm_dig.TM_DIG()
    digitiser._scan_samples_generation = 1
    digitiser._scan_samples_generation_lock = threading.Lock()
    digitiser._sdr_disconnect_advice_sent = False
    digitiser._sdr_disconnect_advice_lock = threading.Lock()
    digitiser.set_last_err = MethodType(lambda self, message: message, digitiser)
    digitiser.handle_method_call = MethodType(
        lambda self, api_call: (
            dmd_protocol.STATUS_ERROR,
            "SDR device disconnected while reading samples",
            None,
            None,
        ),
        digitiser,
    )
    return digitiser


def test_failed_scan_read_sends_one_error_status_advice_with_observation_data():
    digitiser = _digitiser_with_failed_sdr_read()
    event = SimpleNamespace(name="scan_samples_1", user_ref=1)

    first_action = digitiser.process_timer_event(event)
    second_action = digitiser.process_timer_event(event)

    assert len(first_action.msgs_to_remote) == 1
    assert second_action.msgs_to_remote == []

    api_call = first_action.msgs_to_remote[0].get_api_call()
    assert api_call["msg_type"] == dmd_protocol.MSG_TYPE_ADV
    assert api_call["action_code"] == dmd_protocol.ACTION_CODE_SET
    assert api_call["property"] == dmd_protocol.PROPERTY_STATUS
    assert api_call["status"] == dmd_protocol.STATUS_ERROR
    assert api_call["obs_data"] == {
        "obs_id": "obs001",
        "tgt_idx": 2,
        "freq_scan": 0,
    }
    assert "disconnected" in api_call["message"]


def test_sdr_disconnect_advice_is_not_sent_without_an_active_observation():
    digitiser = _digitiser_with_failed_sdr_read()
    digitiser.dig_model.scanning = False

    action = digitiser.process_timer_event(
        SimpleNamespace(name="scan_samples_1", user_ref=1)
    )

    assert action.msgs_to_remote == []


def test_stale_scan_read_does_not_abort_the_current_observation():
    digitiser = _digitiser_with_failed_sdr_read()

    action = digitiser.process_timer_event(
        SimpleNamespace(name="scan_samples_1", user_ref=0)
    )

    assert action.msgs_to_remote == []
    assert digitiser._sdr_disconnect_advice_sent is False


def test_tm_applies_error_status_model_and_aborts_related_observation():
    obs = ObsModel(obs_id="obs001", obs_state=ObsState.SCANNING)
    failure_dt = datetime(2026, 8, 25, 19, 57, 34, tzinfo=timezone.utc)
    stored_digitiser = DigitiserModel(
        dig_id="dig001",
        sample_rate=2_048_000.0,
        sdr_connected=CommunicationStatus.ESTABLISHED,
    )
    failed_digitiser = DigitiserModel(
        dig_id="dig001",
        sample_rate=2_048_000.0,
        scanning={"obs_id": "obs001", "tgt_idx": 2, "freq_scan": 0},
        sdr_connected=CommunicationStatus.NOT_ESTABLISHED,
    )
    failed_digitiser.app.last_err_msg = "SDR device disconnected while reading samples"
    failed_digitiser.app.last_err_dt = failure_dt
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = MethodType(lambda self: None, manager)
    manager.set_last_err = MethodType(lambda self, message: message, manager)
    manager.telmodel = SimpleNamespace(
        dig_store=SimpleNamespace(last_update=None),
        oda=SimpleNamespace(
            obs_store=SimpleNamespace(
                get_obs_by_id=lambda obs_id: obs if obs_id == obs.obs_id else None
            )
        ),
    )
    timestamp = datetime(2026, 8, 25, 19, 57, 35, tzinfo=timezone.utc)

    action = manager.process_dig_entity_msg(
        event=None,
        api_msg={"entity": "dig001", "timestamp": timestamp.isoformat()},
        api_call={
            "msg_type": dmd_protocol.MSG_TYPE_ADV,
            "action_code": dmd_protocol.ACTION_CODE_SET,
            "property": dmd_protocol.PROPERTY_STATUS,
            "status": dmd_protocol.STATUS_ERROR,
            "value": failed_digitiser.to_dict(),
            "message": "SDR device disconnected while reading samples",
            "obs_data": {"obs_id": "obs001", "tgt_idx": 2, "freq_scan": 0},
        },
        payload=bytearray(),
        entity=stored_digitiser,
    )

    assert stored_digitiser.sdr_connected == CommunicationStatus.NOT_ESTABLISHED
    assert stored_digitiser.scanning == {
        "obs_id": "obs001",
        "tgt_idx": 2,
        "freq_scan": 0,
    }
    assert stored_digitiser.app.last_err_msg == "SDR device disconnected while reading samples"
    assert stored_digitiser.app.last_err_dt == failure_dt
    assert len(action.obs_transitions) == 1
    assert action.obs_transitions[0].get_obs() is obs
    assert action.obs_transitions[0].get_transition() == ObsTransition.ABORT
