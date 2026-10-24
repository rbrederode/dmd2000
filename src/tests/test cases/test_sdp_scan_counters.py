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
    assert not hasattr(scan, "_display_baseline")


def _add_load(sdp, value):
    load = _build_scan()
    load.scan_model.scan_type = ScanType.LOAD
    load.mpr = np.full(load.scan_model.spectral_resolution, value, dtype=float)
    sdp.cal_q.put(load)
    return load


def test_display_baseline_is_independent_and_fixed_until_next_scan():
    import gc
    import weakref

    sdp = _build_sdp()
    first = _build_scan()
    load = _add_load(sdp, 2.0)
    reference = weakref.ref(load)
    baseline = sdp._display_baseline(first)
    assert not np.shares_memory(baseline.mpr, load.mpr)
    assert not np.shares_memory(baseline.mpr_flags, load.mpr_flags)
    load.mpr[:] = 99.0
    load.mpr_flags[:] = 1
    np.testing.assert_array_equal(baseline.mpr, 2.0)
    np.testing.assert_array_equal(baseline.mpr_flags, 0)
    sdp.cal_q.get_nowait()
    sdp.cal_q.task_done()
    del load
    gc.collect()
    assert reference() is None

    _add_load(sdp, 3.0)
    assert sdp._display_baseline(first) is baseline
    second = _build_scan()
    np.testing.assert_array_equal(sdp._display_baseline(second).mpr, 3.0)


def test_completed_scan_keeps_black_baseline_after_observation_cleanup(monkeypatch, tmp_path):
    from matplotlib import pyplot as plt
    from sdp.signal_display import SignalDisplay

    monkeypatch.setattr(Scan, "save_to_disk", lambda *args, **kwargs: None)
    monkeypatch.setattr(SignalDisplay, "is_visible_figure", lambda self: None)
    sdp = _build_sdp()
    sdp.get_args = lambda: SimpleNamespace(scan_store_dir=str(tmp_path), headless=False)
    scan = _build_scan()
    scan.load_spr(sec=1, spr=np.ones(scan.scan_model.spectral_resolution))
    _add_load(sdp, 2.0)
    sdp.sky_q.put(scan)
    sdp._complete_scan(scan)
    assert not sdp.signal_displays
    sdp.set_obs_complete(scan.get_obs_id())
    assert sdp.cal_q.empty()
    try:
        sdp._refresh_signal_displays()
        display = sdp.signal_displays[scan.get_dig_id()]
        assert display.load_line.get_visible()
        assert display.load_line.get_color() == "black"
        np.testing.assert_allclose(display.load_line.get_ydata(), 2.0)
        assert scan.scan_model.scan_id in display.saved_scan_ids
        assert len(list(tmp_path.glob("*.png"))) == 1
        sdp._refresh_signal_displays()
        np.testing.assert_allclose(display.load_line.get_ydata(), 2.0)
    finally:
        plt.close("all")
