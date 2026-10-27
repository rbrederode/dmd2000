import threading
from types import MethodType, SimpleNamespace

import pytest

from api import protocol as dmd_protocol
from api import tm_dig
from dig.dig import Digitiser
from models.comms import CommunicationStatus
from models.dig import DigitiserModel
from models.obs import ObsModel, ObsState, ObsTransition
from tm.tm import TelescopeManager


def make_digitiser(scanning):
    digitiser = Digitiser.__new__(Digitiser)
    digitiser.stop = MethodType(lambda self: None, digitiser)
    digitiser.dig_model = DigitiserModel(
        dig_id="dig001", scanning=scanning,
        sdp_connected=CommunicationStatus.ESTABLISHED,
        tm_connected=CommunicationStatus.ESTABLISHED,
    )
    digitiser.tm_api = tm_dig.TM_DIG()
    digitiser.sdr = SimpleNamespace(stream_reset=lambda: 0)
    digitiser._scan_samples_generation = 1
    digitiser._scan_samples_generation_lock = threading.Lock()
    digitiser.set_last_err = lambda message: message
    return digitiser


def test_sdp_disconnect_reports_interrupted_observation_and_tm_aborts_it():
    scanning = {"obs_id": "obs001", "tgt_idx": 2, "freq_scan": 1}
    digitiser = make_digitiser(scanning)

    action = digitiser.process_sdp_disconnected(SimpleNamespace(remote_addr=("127.0.0.1", 60000)))

    assert digitiser.dig_model.scanning is False
    assert digitiser._scan_samples_generation == 2
    assert len(action.msgs_to_remote) == 1
    advice = action.msgs_to_remote[0]
    api_call = advice.get_api_call()
    assert api_call["status"] == dmd_protocol.STATUS_ERROR
    assert api_call["obs_data"] == scanning
    assert api_call["obs_data"] is not scanning
    assert api_call["value"]["scanning"] is False
    assert api_call["value"]["sdp_connected"]["value"] == "NOT_ESTABLISHED"
    assert "Science Data Processor disconnect" in api_call["message"]

    obs = ObsModel(obs_id="obs001", obs_state=ObsState.SCANNING)
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = MethodType(lambda self: None, manager)
    manager.telmodel = SimpleNamespace(
        dig_store=SimpleNamespace(last_update=None),
        oda=SimpleNamespace(obs_store=SimpleNamespace(
            get_obs_by_id=lambda obs_id: obs if obs_id == obs.obs_id else None,
        )),
    )
    stored_digitiser = DigitiserModel(dig_id="dig001")
    tm_action = manager.process_dig_entity_msg(
        event=None, api_msg={"entity": "dig001", "timestamp": advice.get_timestamp()},
        api_call=api_call, payload=bytearray(), entity=stored_digitiser,
    )

    assert stored_digitiser.scanning is False
    assert stored_digitiser.sdp_connected == CommunicationStatus.NOT_ESTABLISHED
    assert len(tm_action.obs_transitions) == 1
    assert tm_action.obs_transitions[0].get_obs() is obs
    assert tm_action.obs_transitions[0].get_transition() == ObsTransition.ABORT

    reconnect = digitiser.process_sdp_connected(SimpleNamespace(remote_addr=("127.0.0.1", 60000)))
    assert digitiser.dig_model.scanning is False
    reconnect_call = reconnect.msgs_to_remote[0].get_api_call()
    assert "status" not in reconnect_call
    assert "obs_data" not in reconnect_call
    assert reconnect_call["value"]["sdp_connected"]["value"] == "ESTABLISHED"


@pytest.mark.parametrize("scanning", [False, {}])
def test_idle_sdp_disconnect_sends_ordinary_status(scanning):
    digitiser = make_digitiser(scanning)

    action = digitiser.process_sdp_disconnected(SimpleNamespace(remote_addr=("127.0.0.1", 60000)))

    api_call = action.msgs_to_remote[0].get_api_call()
    assert "status" not in api_call
    assert "obs_data" not in api_call
    assert api_call["value"]["sdp_connected"]["value"] == "NOT_ESTABLISHED"
    assert digitiser._scan_samples_generation == 1


def test_sdp_disconnect_stops_acquisition_when_tm_is_unavailable():
    digitiser = make_digitiser({"obs_id": "obs001", "tgt_idx": 0, "freq_scan": 0})
    digitiser.dig_model.tm_connected = CommunicationStatus.NOT_ESTABLISHED

    action = digitiser.process_sdp_disconnected(SimpleNamespace(remote_addr=("127.0.0.1", 60000)))

    assert digitiser.dig_model.scanning is False
    assert digitiser._scan_samples_generation == 2
    assert action.msgs_to_remote == []
