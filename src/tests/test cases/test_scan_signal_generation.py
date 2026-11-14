"""Completed-scan preview generation uses saved spectra, not acquisition/IQ."""
from datetime import datetime, timezone
from concurrent.futures import Future
import json
from pathlib import Path

import numpy as np
import pytest

from models.scan import ScanModel, ScanState, ScanType
from obs.scan import Scan
from obs import scan_signal


def saved_scan(folder, kind=ScanType.SKY, target=1):
    model = ScanModel(obs_id='test-obs', tgt_idx=target, freq_scan=0, scan_iter=0,
                      scan_type=kind, dig_id='dig001', duration=2, sample_rate=64000,
                      spectral_resolution=64, center_freq=1420000000., gain=10.,
                      read_start=datetime(2026, 11, 14, 12, tzinfo=timezone.utc),
                      status=ScanState.COMPLETE, load=kind == ScanType.LOAD)
    model.sdp_config_profile = 'local'
    scan = Scan(model)
    scan.spr[:] = np.linspace(10, 20, 64)
    assert scan.save_to_disk(str(folder))
    return scan


def catalogue(folder, scan):
    (folder / 'test-obs-obs.json').write_text(json.dumps({'_type': 'ObsModel',
        'obs_id': 'test-obs', 'target_scans': [{'scans': [scan.scan_model.to_dict()]}]}))


def test_real_worker_renders_one_scan_and_reuses_atomic_png(tmp_path):
    load = saved_scan(tmp_path, ScanType.LOAD, 0)
    scan = saved_scan(tmp_path)
    scan.scan_model.load_scan_id = load.scan_model.scan_id
    assert scan.save_to_disk(str(tmp_path))
    catalogue(tmp_path, scan)
    request = {'root': str(tmp_path), 'spr': str(tmp_path / (scan.scan_model.files_prefix + '-spr.csv')),
               'pipeline': {'_type': 'PipelineConfig', 'steps_map': {'default': []}}, 'profile': 'local'}
    original = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    scan_signal._run_worker(request)
    png = tmp_path / (scan.scan_model.files_prefix + '-sigfig.png')
    assert png.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    for name, content in original.items():
        assert (tmp_path / name).read_bytes() == content
    timestamp = png.stat().st_mtime_ns
    scan_signal._run_worker(request)
    assert png.stat().st_mtime_ns == timestamp
    assert not list(tmp_path.glob('.signal-*'))
    result = scan_signal.signal_product(str(tmp_path), 'test-obs', 1, 0, 0, '.', None)
    assert result.status == 'success' and result.payload['path'] == str(png)


@pytest.mark.parametrize('state', [ScanState.EMPTY, ScanState.WIP, ScanState.ABORTED])
def test_noncompleted_state_rejected_even_with_saved_spectra(tmp_path, state):
    scan = saved_scan(tmp_path)
    scan.scan_model.status = state
    catalogue(tmp_path, scan)
    result = scan_signal.signal_product(str(tmp_path), 'test-obs', 1, 0, 0, '.', None, generate=True)
    assert result.code == 'scan_incomplete'
    assert not list(tmp_path.glob('*sigfig.png'))


def test_missing_png_offers_generation_and_deduplicates_running_job(tmp_path, monkeypatch):
    scan = saved_scan(tmp_path)
    catalogue(tmp_path, scan)
    args = (str(tmp_path), 'test-obs', 1, 0, 0, '.', None)
    assert scan_signal.signal_product(*args).code == 'generation_available'
    future = Future()
    submissions = []
    monkeypatch.setattr(scan_signal._executor, 'submit', lambda *args: submissions.append(args) or future)
    for _ in range(2):
        assert scan_signal.signal_product(*args, generate=True).code == 'generating'
    assert len(submissions) == 1
    assert scan_signal.signal_product(*args).code == 'generating'
    future.set_exception(RuntimeError('Test generation failed'))
    assert scan_signal.signal_product(*args).code == 'generation_failed'
    # Explicit retry is allowed; GET never restarts failed processing.
    assert len(submissions) == 1


def test_saved_sdp_config_profile_wins_without_consulting_current_ui_or_tm(tmp_path):
    scan = saved_scan(tmp_path)
    catalogue(tmp_path, scan)
    def unavailable():
        raise AssertionError('Recorded settings must take precedence over current launch settings')
    result = scan_signal.signal_product(str(tmp_path), 'test-obs', 1, 0, 0, '.', unavailable)
    assert result.code == 'generation_available'
    assert result.message == "Signal display image not available. Generate it using the SDP's current config profile 'local'?"
    assert result.payload['profile_source'] == 'recorded with this scan'
    restored = ScanModel.from_dict(json.loads((tmp_path / (scan.scan_model.files_prefix + '-meta.json')).read_text()))
    assert restored.sdp_config_profile == 'local'
    assert 'sdp_pipeline_config' not in restored.to_dict()


def test_legacy_scan_uses_sdp_app_profile_not_ui_profile(tmp_path):
    scan = saved_scan(tmp_path)
    scan.scan_model.sdp_config_profile = None
    assert scan.save_to_disk(str(tmp_path))
    catalogue(tmp_path, scan)
    args = (str(tmp_path), 'test-obs', 1, 0, 0, '.')
    assert scan_signal.signal_product(*args).code == 'sdp_profile_unavailable'
    payload = {'telescope_model': {'sdp': {'app': {'arguments': {'profile': 'rotation'}},
                  'pipeline_config': {'_type': 'PipelineConfig', 'steps_map': {'default': []}}}}, 'stale': False}
    result = scan_signal.signal_product(*args, lambda: scan_signal.sdp_settings_from_snapshot(payload))
    assert result.code == 'generation_available'
    assert "current config profile 'rotation'" in result.message
    assert 'not recorded' in result.payload['profile_source']
    payload['stale'] = True
    assert scan_signal.sdp_settings_from_snapshot(payload) == {}


def test_previous_pipeline_snapshot_is_ignored_and_not_written_again(tmp_path):
    scan = saved_scan(tmp_path)
    metadata = scan.scan_model.to_dict()
    metadata['sdp_pipeline_config'] = {'_type': 'PipelineConfig', 'steps_map': {'default': []}}
    restored = ScanModel.from_dict(metadata)
    assert restored.sdp_config_profile == 'local'
    assert 'sdp_pipeline_config' not in restored.to_dict()

