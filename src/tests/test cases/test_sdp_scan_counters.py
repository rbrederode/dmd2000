from queue import Queue
from types import SimpleNamespace

from models.scan import ScanModel, ScanState, ScanType
from models.sdp import ScienceDataProcessorModel
from obs.scan import Scan
from sdp.sdp import SDP


def _build_scan() -> Scan:
    return Scan(
        scan_model=ScanModel(
            obs_id="obs001",
            tgt_idx=0,
            freq_scan=0,
            scan_type=ScanType.SKY,
            dig_id="dig001",
            start_idx=0,
            duration=1,
            sample_rate=2048000,
            spectral_resolution=2048,
            center_freq=1420000000,
            gain=10.0,
            load=False,
            status=ScanState.COMPLETE,
        )
    )


def _build_sdp() -> SDP:
    sdp = SDP.__new__(SDP)
    sdp.sdp_model = ScienceDataProcessorModel()
    sdp.app_model = sdp.sdp_model.app
    sdp.processors = []
    sdp.status_thread = None
    sdp.queue = Queue()
    sdp.sky_q = Queue()
    sdp.cal_q = Queue()
    sdp.get_args = lambda: SimpleNamespace(scan_store_dir="/tmp")
    sdp.stop_timer_manager = lambda: None
    sdp.stop_processors = lambda: None
    sdp.stop_status_thread = lambda: None
    return sdp


def test_complete_scan_decrements_wip_counter(monkeypatch):
    monkeypatch.setattr(Scan, "save_to_disk", lambda *args, **kwargs: None)
    sdp = _build_sdp()
    scan = _build_scan()
    sdp.sky_q.put(scan)
    sdp.sdp_model.scans_wip = 1

    sdp._complete_scan(scan)

    assert sdp.sdp_model.scans_completed == 1
    assert sdp.sdp_model.scans_wip == 0


def test_complete_scan_does_not_make_wip_counter_negative(monkeypatch):
    monkeypatch.setattr(Scan, "save_to_disk", lambda *args, **kwargs: None)
    sdp = _build_sdp()
    scan = _build_scan()
    sdp.sky_q.put(scan)
    sdp.sdp_model.scans_wip = 0

    sdp._complete_scan(scan)

    assert sdp.sdp_model.scans_completed == 1
    assert sdp.sdp_model.scans_wip == 0
