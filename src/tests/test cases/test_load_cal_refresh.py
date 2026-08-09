from datetime import datetime, timezone
from queue import Queue

import numpy as np

from models.pipeline import StepConfig, StepType
from models.scan import ScanModel, ScanState, ScanType
from obs.scan import Scan
from sdp.channel_mask import empty_channel_flags
from sdp.pipeline.steps.load import LoadCal
from sdp.sdp import SDP


def make_scan(scan_type, *, obs_id, tgt_idx, synthesised=False):
    scan = Scan(
        scan_model=ScanModel(
            obs_id=obs_id,
            tgt_idx=tgt_idx,
            freq_scan=0,
            scan_type=scan_type,
            dig_id="dig001",
            created=datetime.now(timezone.utc),
            start_idx=0,
            duration=2,
            sample_rate=2,
            spectral_resolution=2,
            center_freq=1420.0,
            gain=34.0,
            load=scan_type == ScanType.LOAD,
            status=ScanState.COMPLETE if scan_type == ScanType.LOAD else ScanState.WIP,
            synthesised=synthesised,
            last_update=datetime.now(timezone.utc),
        )
    )
    scan.mpr = np.ones(2, dtype=np.float64)
    return scan


def test_load_cal_refreshes_when_real_load_replaces_synthetic_without_queue_size_change():
    obs_id = "obs-load-refresh"
    sky_scan = make_scan(ScanType.SKY, obs_id=obs_id, tgt_idx=2)
    synthetic_load = make_scan(ScanType.LOAD, obs_id=obs_id, tgt_idx=0, synthesised=True)
    real_load = make_scan(ScanType.LOAD, obs_id=obs_id, tgt_idx=1)
    real_load.mpr = np.array([2.0, 4.0], dtype=np.float64)

    cal_q = Queue()
    cal_q.put(synthetic_load)
    step = LoadCal(StepConfig(step=StepType.LOAD, params={"scan": sky_scan, "cal_q": cal_q}))

    cal_q.put(real_load)
    cal_q.queue.remove(synthetic_load)

    result = step.process(
        context={"pipeline": "cal", "channel_flags": empty_channel_flags(2)},
        signal=np.array([8.0, 8.0], dtype=np.float64),
    )

    np.testing.assert_allclose(result, np.array([4.0, 2.0]))
    assert step.load_scan is real_load
    assert sky_scan.scan_model.load_scan_id == real_load.scan_model.scan_id


def test_newest_equivalent_load_scan_prefers_real_load_over_synthetic():
    obs_id = "obs-load-select"
    sky_scan = make_scan(ScanType.SKY, obs_id=obs_id, tgt_idx=2)
    synthetic_load = make_scan(ScanType.LOAD, obs_id=obs_id, tgt_idx=0, synthesised=True)
    real_load = make_scan(ScanType.LOAD, obs_id=obs_id, tgt_idx=1)

    cal_q = Queue()
    cal_q.put(synthetic_load)
    cal_q.put(real_load)

    assert SDP._newest_equivalent_load_scan(sky_scan, cal_q) is real_load
