from queue import Queue
import pytest
from types import SimpleNamespace

from models.comms import CommunicationStatus
from models.dig import DigitiserList, DigitiserModel
from models.sdp import ScienceDataProcessorModel
from sdp.sdp import SDP


def _build_sdp(tmp_path, *, connected_id=None, connection_state=None):
    digitiser = DigitiserModel(
        dig_id="dig001",
        sdp_connected=(
            connection_state
            if connection_state is not None
            else CommunicationStatus.NOT_ESTABLISHED
        ),
    )
    sdp = SDP.__new__(SDP)
    sdp.sdp_model = ScienceDataProcessorModel(
        dig_store=DigitiserList(dig_list=[digitiser])
    )
    sdp.app_model = sdp.sdp_model.app
    sdp.entity_connection_map = (
        {connected_id: (object(), ("127.0.0.1", 12345))}
        if connected_id is not None
        else {}
    )
    sdp.stop = lambda: None
    sdp._dig_locks = {}
    sdp.sky_q = Queue()
    sdp.cal_q = Queue()
    sdp.get_args = lambda: SimpleNamespace(scan_store_dir=str(tmp_path))
    return sdp, digitiser


def _scan_config():
    return {
        "obs_id": "obs001",
        "dig_id": "dig001",
        "center_freq": 1_420_000_000.0,
        "bandwidth": 1_000_000.0,
        "sample_rate": 2_048_000.0,
        "gain": 37.0,
        "spectral_resolution": 2048,
        "scan_duration": 15,
        "scanning": {"obs_id": "obs001", "tgt_idx": 0, "freq_scan": 0},
        "load": True,
    }


def test_scan_config_rejects_configured_but_disconnected_digitiser(tmp_path):
    sdp, digitiser = _build_sdp(tmp_path)

    assert sdp.set_scan_config(_scan_config()) is False
    assert digitiser.scanning is False
    assert "dig001" in sdp.get_last_err_msg()
    assert "not connected" in sdp.get_last_err_msg()


def test_scan_config_rejects_connection_resolved_to_another_entity(tmp_path):
    sdp, digitiser = _build_sdp(
        tmp_path,
        connected_id="dig003",
        connection_state=CommunicationStatus.ESTABLISHED,
    )

    assert sdp.set_scan_config(_scan_config()) is False
    assert digitiser.scanning is False


def test_scan_config_accepts_matching_connected_entity(tmp_path):
    sdp, digitiser = _build_sdp(
        tmp_path,
        connected_id="dig001",
        connection_state=CommunicationStatus.ESTABLISHED,
    )

    assert sdp.set_scan_config(_scan_config()) is True
    assert digitiser.scanning == {
        "obs_id": "obs001",
        "tgt_idx": 0,
        "freq_scan": 0,
    }


@pytest.mark.parametrize('scan_state', [False, {'obs_id': 'USR-existing', 'tgt_idx': 0, 'freq_scan': 0}])
@pytest.mark.parametrize('include_null', [False, True])
def test_partial_gain_preserves_scan_state_and_serializable_status(tmp_path, scan_state, include_null):
    sdp, digitiser = _build_sdp(tmp_path, connected_id='dig001', connection_state=CommunicationStatus.ESTABLISHED)
    digitiser.scanning = scan_state
    value = {'dig_id': 'dig001', 'obs_id': 'USR-gain-update', 'gain': 25.0}
    if include_null:
        value['scanning'] = None
    assert sdp.set_scan_config(value) is True
    assert digitiser.gain == 25.0
    assert digitiser.scanning == scan_state
    received = ScienceDataProcessorModel.from_dict(sdp.sdp_model.to_dict())
    assert received.dig_store.get_dig_by_id('dig001').scanning == scan_state
