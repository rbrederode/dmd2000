from queue import Queue
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import numpy as np

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
    sdp.completed_display_q = Queue()
    sdp.signal_displays = {}
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


def test_one_second_scan_completed_between_refreshes_is_displayed_and_saved(monkeypatch, tmp_path):
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from sdp.signal_display import SignalDisplay

    monkeypatch.setattr(Scan, "save_to_disk", lambda *args, **kwargs: None)
    monkeypatch.setattr(SignalDisplay, "is_visible_figure", lambda self: None)
    sdp = _build_sdp()
    sdp.get_args = lambda: SimpleNamespace(scan_store_dir=str(tmp_path), headless=False)
    start = datetime.now(timezone.utc)
    scans = []
    for tgt_idx in (0, 1):
        scan = _build_scan()
        scan.scan_model.tgt_idx = tgt_idx
        scan.set_status(ScanState.EMPTY)
        samples = np.ones(int(scan.scan_model.sample_rate), dtype=np.complex64)
        assert scan.load_samples(1, samples, start, start + timedelta(seconds=1))
        assert scan.get_status() == ScanState.COMPLETE
        sdp.sky_q.put(scan)
        sdp.sdp_model.scans_wip += 1
        sdp._complete_scan(scan)
        scans.append(scan)

    # Both scans have left the processing queue before the GUI gets a turn.
    assert sdp.sky_q.empty()
    assert sdp.signal_displays == {}
    try:
        sdp._refresh_signal_displays()
        display = sdp.signal_displays["dig001"]
        assert display.get_scan() is scans[-1]
        assert display.sec == 1
        np.testing.assert_allclose(display.spr_line.get_ydata(), scans[-1].spr[0])
        assert display.saved_scan_ids == {s.scan_model.scan_id for s in scans}
        assert len(list(tmp_path.glob("*.png"))) == 2
        assert sdp.completed_display_q.empty()
        assert sdp.completed_display_q.unfinished_tasks == 0
        sdp._refresh_signal_displays()
        assert len(list(tmp_path.glob("*.png"))) == 2
    finally:
        plt.close("all")


def test_headless_completed_scan_does_not_wait_for_display(monkeypatch):
    monkeypatch.setattr(Scan, "save_to_disk", lambda *args, **kwargs: None)
    sdp = _build_sdp()
    sdp.get_args = lambda: SimpleNamespace(scan_store_dir="/tmp", headless=True)
    scan = _build_scan()
    sdp.sky_q.put(scan)
    sdp.sdp_model.scans_wip = 1

    sdp._complete_scan(scan)

    assert sdp.sky_q.empty()
    assert sdp.completed_display_q.empty()
