"""Samples must remain within their observation/target/frequency identity."""
from datetime import datetime, timedelta, timezone
from queue import Queue
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from api import sdp_dig, tm_sdp
from ipc.message import APIMessage
from models.comms import CommunicationStatus
from models.dig import DigitiserList, DigitiserModel
from models.scan import ScanModel, ScanState, ScanType
from models.sdp import ScienceDataProcessorModel
from obs.scan import Scan
from sdp.sdp import SDP


@pytest.fixture
def setup_sdp(tmp_path, monkeypatch):
    monkeypatch.setattr(SDP, 'pipeline_factory', None)
    monkeypatch.setattr(Scan, '_scan_iter_counter', {})
    dig = DigitiserModel(dig_id='dig001', sample_rate=16., gain=10., center_freq=1420000000.,
                         channels=8, scan_duration=3,
                         sdp_connected=CommunicationStatus.ESTABLISHED,
                         scanning={'obs_id':'ODT-test', 'tgt_idx':7, 'freq_scan':0})
    sdp = SDP.__new__(SDP)
    sdp.stop = lambda: None
    sdp.sdp_model = ScienceDataProcessorModel(dig_store=DigitiserList(dig_list=[dig]),
                                            tm_connected=CommunicationStatus.ESTABLISHED)
    sdp.app_model = sdp.sdp_model.app
    sdp.sky_q, sdp.cal_q, sdp.completed_display_q = Queue(), Queue(), Queue()
    sdp._dig_locks = {}
    sdp.entity_connection_map = {'dig001':(object(), ('127.0.0.1', 1))}
    sdp.dig_api, sdp.tm_api = sdp_dig.SDP_DIG(), tm_sdp.TM_SDP()
    sdp.get_args = lambda: SimpleNamespace(scan_store_dir=str(tmp_path), headless=True)
    return sdp, dig


def pending_scan(sdp, *, obs_id='ODT-test', target=6, frequency=0, dig_id='dig001', state=ScanState.WIP, load=False):
    scan = Scan(ScanModel(obs_id=obs_id, tgt_idx=target, freq_scan=frequency, dig_id=dig_id,
                          start_idx=100, duration=3, sample_rate=16., spectral_resolution=8,
                          center_freq=1420000000., gain=10., load=load,
                          scan_type=ScanType.LOAD if load else ScanType.SKY, status=state))
    sdp.sky_q.put(scan)
    return scan


def samples(sdp, dig, counter=101, scanning=None):
    start = datetime(2026, 11, 13, tzinfo=timezone.utc) + timedelta(seconds=counter)
    metadata = dict(center_freq=dig.center_freq, sample_rate=dig.sample_rate, gain=dig.gain,
                    load=dig.load_active, scanning=dig.scanning if scanning is None else scanning,
                    read_counter=counter, read_start=start.isoformat(), read_end=(start+timedelta(seconds=1)).isoformat())
    call = {'msg_type':'adv', 'action_code':'samples',
            'metadata':[{'property':key, 'value':value} for key, value in metadata.items()]}
    message = APIMessage()
    message.set_json_api_header(api_version=sdp_dig.API_VERSION, dt=start, from_system='dig',
                                to_system='sdp', entity=dig.dig_id, api_call=call)
    action = sdp.process_dig_entity_msg(None, message.get_json_api_header(), call,
                                      np.ones(16, dtype=np.complex64).tobytes(), dig)
    assert action.msgs_to_remote[-1].get_api_call()['status'] == 'success'
    return action


@pytest.mark.parametrize('old', [dict(target=6), dict(obs_id='ODT-previous',target=7), dict(target=7,frequency=1)])
@pytest.mark.parametrize('state', [ScanState.EMPTY, ScanState.WIP])
def test_overlapping_old_scan_cannot_receive_new_identity_samples(setup_sdp, old, state):
    sdp, dig = setup_sdp
    stale = pending_scan(sdp, state=state, **old)
    other = pending_scan(sdp, dig_id='dig002')
    completed = pending_scan(sdp, state=ScanState.COMPLETE)
    calibration = pending_scan(sdp, state=ScanState.COMPLETE, load=True)
    sdp._remove_from_queue(calibration)
    sdp.cal_q.put(calibration)
    action = samples(sdp, dig)
    assert stale.get_status() == ScanState.ABORTED
    assert not np.any(stale.loaded_secs)
    assert stale not in sdp.sky_q.queue
    assert other.get_status() == ScanState.WIP
    assert completed in sdp.sky_q.queue and calibration in sdp.cal_q.queue
    current = next(scan for scan in sdp.sky_q.queue if scan.get_dig_id() == 'dig001' and scan.get_status() == ScanState.WIP)
    assert current.scan_model.tgt_idx == 7 and current.scan_model.freq_scan == 0
    assert current.scan_model.obs_id == 'ODT-test' and current.scan_model.start_idx == 101
    assert current.loaded_secs == [True, False, False]
    assert sdp.sdp_model.scans_aborted == 1
    assert sdp.sdp_model.scans_wip == 2  # One pending scan for each digitiser.
    assert sdp.sdp_model.scans_completed == 0
    assert all(msg.get_api_call().get('property') != 'scan_complete' for msg in action.msgs_to_remote)
    # Continuation completes only the new target, with its own recorded samples.
    samples(sdp, dig, 102)
    complete = samples(sdp, dig, 103)
    assert sdp.sdp_model.scans_completed == 1
    notices = [msg.get_api_call() for msg in complete.msgs_to_remote if msg.get_api_call().get('property') == 'scan_complete']
    assert len(notices) == 1 and notices[0]['value']['tgt_idx'] == 7
    assert sdp.sdp_model.scans_wip == 1


def test_same_identity_reuses_pending_scan(setup_sdp):
    sdp, dig = setup_sdp
    current = pending_scan(sdp, target=7)
    samples(sdp, dig)
    assert current.loaded_secs == [False, True, False]
    assert sdp.sdp_model.scans_created == 0 and sdp.sdp_model.scans_aborted == 0


@pytest.mark.parametrize('old', [dict(obs_id='ODT-test',tgt_idx=6,freq_scan=0),
                                  dict(obs_id='ODT-old',tgt_idx=7,freq_scan=0),
                                  dict(obs_id='ODT-test',tgt_idx=7,freq_scan=1)])
def test_late_packet_cannot_abort_or_modify_current_scan(setup_sdp, old):
    sdp, dig = setup_sdp
    current = pending_scan(sdp, target=7)
    samples(sdp, dig, scanning=old)
    assert current.get_status() == ScanState.WIP and not np.any(current.loaded_secs)
    assert sdp.sdp_model.scans_aborted == 0 and sdp.sdp_model.scans_created == 0


@pytest.mark.parametrize('changed', [dict(tgt_idx=7),dict(obs_id='ODT-new'),dict(freq_scan=1)])
def test_configuration_retires_old_identity_before_samples_arrive(setup_sdp, changed):
    sdp, dig = setup_sdp
    dig.scanning = {'obs_id':'ODT-test', 'tgt_idx':6, 'freq_scan':0}
    old = pending_scan(sdp)
    new_identity = {**dig.scanning, **changed}
    assert sdp.set_scan_config({'dig_id':dig.dig_id, 'obs_id':new_identity['obs_id'], 'scanning':new_identity})
    assert old.get_status() == ScanState.ABORTED
    assert sdp.sdp_model.scans_aborted == 1 and sdp.sdp_model.scans_wip == 0
    assert sdp.cal_q.qsize() >= 1


def test_identical_or_partial_configuration_preserves_current_scan(setup_sdp):
    sdp, dig = setup_sdp
    current = pending_scan(sdp, target=7)
    assert sdp.set_scan_config({'dig_id':dig.dig_id,'obs_id':'ODT-test','scanning':dict(dig.scanning)})
    assert sdp.set_scan_config({'dig_id':dig.dig_id,'obs_id':'ODT-test','gain':10.})
    assert current.get_status() == ScanState.WIP and current in sdp.sky_q.queue
    assert sdp.sdp_model.scans_aborted == 0


def test_manual_scan_keeps_legacy_metadata_handling_and_uses_active_identity(setup_sdp):
    sdp, dig = setup_sdp
    dig.scanning = {'obs_id':'USR-manual', 'tgt_idx':0, 'freq_scan':0}
    old = pending_scan(sdp, obs_id='USR-old', target=0)
    samples(sdp, dig, scanning=False)
    assert old.get_status() == ScanState.ABORTED
    current = sdp.sdp_model.processing_scans[0]
    assert current.obs_id == 'USR-manual' and current.tgt_idx == 0


def test_scan_configuration_and_sample_loading_share_digitiser_lock(setup_sdp):
    sdp, dig = setup_sdp
    held = threading.Event()
    finished = threading.Event()
    errors = []
    lock = sdp._get_dig_lock(dig.dig_id)
    def configure():
        held.set()
        try:
            sdp.set_scan_config({'dig_id':dig.dig_id, 'obs_id':'ODT-test', 'scanning':dict(dig.scanning)})
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()
    with lock:
        thread = threading.Thread(target=configure)
        thread.start()
        assert held.wait(1)
        assert not finished.wait(.05)
    thread.join(timeout=3)
    assert finished.is_set() and not errors


def test_new_scan_records_only_sdp_launch_profile(setup_sdp, monkeypatch, tmp_path):
    from models.pipeline import PipelineConfig
    from sdp.pipeline.pipeline_factory import ProcessingPipelineFactory
    sdp, dig = setup_sdp
    config = PipelineConfig(steps_map={'default': []})
    monkeypatch.setattr(SDP, 'pipeline_factory', ProcessingPipelineFactory(config))
    sdp.get_args = lambda: SimpleNamespace(scan_store_dir=str(tmp_path), headless=True, profile='rotation')
    samples(sdp, dig)
    scan = next(iter(sdp.sky_q.queue))
    assert scan.scan_model.sdp_config_profile == 'rotation'
    assert 'sdp_pipeline_config' not in scan.scan_model.to_dict()
    restored = ScanModel.from_dict(scan.scan_model.to_dict())
    assert restored.sdp_config_profile == 'rotation'
    assert 'sdp_pipeline_config' not in restored.to_dict()
