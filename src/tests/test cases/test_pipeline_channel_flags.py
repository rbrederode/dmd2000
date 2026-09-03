from datetime import datetime, timezone
from queue import Queue

import numpy as np

from models.pipeline import StepConfig, StepType
from models.scan import ScanModel, ScanState, ScanType
from obs.scan import Scan
from sdp.channel_mask import ChannelFlag, empty_channel_flags
from sdp.pipeline.steps.qa import QA
from sdp.pipeline.steps.rfi import RFIFlag
from sdp.pipeline.steps.dc_spike import DCSpike
from sdp.pipeline.steps.load import LoadCal


def _scan(channels: int) -> Scan:
    scan = Scan(
        ScanModel(
            obs_id="obs001",
            tgt_idx=0,
            freq_scan=0,
            scan_type=ScanType.SKY,
            dig_id="dig001",
            created=datetime.now(timezone.utc),
            start_idx=0,
            duration=1,
            sample_rate=channels,
            spectral_resolution=channels,
            center_freq=1420.4e6,
            gain=10.0,
            load=False,
            status=ScanState.EMPTY,
            last_update=datetime.now(timezone.utc),
        )
    )
    scan.init_qa()
    return scan


def test_rfi_sets_flags_without_changing_values_and_ignores_existing_exclusions():
    scan = _scan(9)
    step = RFIFlag(
        StepConfig(
            step=StepType.RFI_FLAG,
            params={"context": "cal", "threshold": 5, "window_size": 5, "scan": scan},
        )
    )
    signal = np.array([1e9, 1.0, 1.0, 1.0, 20.0, 1.0, 1.0, 1.0, 1.0])
    original = signal.copy()
    flags = empty_channel_flags(signal.shape)
    flags[0] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    flags[2] |= int(ChannelFlag.RFI_DETECTED)  # Stale result from an earlier pass.
    flags[8] |= int(ChannelFlag.USER_EXCLUDED)

    result = step.process(
        {"pipeline": "cal", "sec": 1, "channel_flags": flags},
        signal,
    )

    assert result is signal
    np.testing.assert_array_equal(signal, original)
    np.testing.assert_array_equal(
        (flags & int(ChannelFlag.RFI_DETECTED)) != 0,
        [False, False, False, False, True, False, False, False, False],
    )
    assert flags[0] & int(ChannelFlag.BANDPASS_EXCLUDED)
    assert flags[8] & int(ChannelFlag.USER_EXCLUDED)
    assert scan.scan_qa.getQA("cal", 0).rfi_fraction == 1 / 7


def test_rfi_reprocessing_clears_only_its_previous_decisions():
    scan = _scan(7)
    step = RFIFlag(
        StepConfig(
            step=StepType.RFI_FLAG,
            params={"context": "cal", "threshold": 5, "window_size": 5, "scan": scan},
        )
    )
    flags = empty_channel_flags(7)
    flags[3] |= int(ChannelFlag.RFI_DETECTED)
    flags[6] |= int(ChannelFlag.USER_EXCLUDED)

    step.process(
        {"pipeline": "cal", "sec": 1, "channel_flags": flags},
        np.ones(7),
    )

    assert not np.any(flags & int(ChannelFlag.RFI_DETECTED))
    assert flags[6] & int(ChannelFlag.USER_EXCLUDED)


def test_qa_ignores_flagged_channels_and_preserves_original_channel_indices():
    scan = _scan(11)
    step = QA(
        StepConfig(
            step=StepType.QA,
            params={"context": "mpr", "window_frac": 0.2, "smooth_window": 1, "scan": scan},
        )
    )
    flags = empty_channel_flags(11)
    flags[[0, 10]] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    flags[1] |= int(ChannelFlag.RFI_DETECTED)
    signal = np.array([1e9, 1e8, 1.0, 2.0, 2.0, 10.0, 2.0, 2.0, 1.0, 2.0, 1e9])
    original = signal.copy()

    result = step.process(
        {"pipeline": "mpr", "sec": 1, "channel_flags": flags},
        signal,
    )

    qa = scan.scan_qa.getQA("mpr", 0)
    assert result is signal
    np.testing.assert_array_equal(signal, original)
    assert qa.baseline == 2.0
    assert qa.signal_start == 5
    assert qa.signal_end == 6
    assert qa.fwhm == 1.0
    assert np.isclose(qa.signal_db, 10 * np.log10(8.0))
    assert np.isclose(qa.signal_pwr_db, 10 * np.log10(8.0))


def test_qa_leaves_metrics_unset_when_too_few_channels_are_usable():
    scan = _scan(5)
    step = QA(
        StepConfig(
            step=StepType.QA,
            params={"context": "mpr", "scan": scan},
        )
    )
    qa = scan.scan_qa.getQA("mpr", 0)
    qa.baseline = 123.0
    qa.rfi_fraction = 0.5
    flags = empty_channel_flags(5)
    flags[:3] |= int(ChannelFlag.BANDPASS_EXCLUDED)

    step.process(
        {"pipeline": "mpr", "sec": 1, "channel_flags": flags},
        np.arange(5, dtype=np.float64),
    )

    assert qa.baseline is None
    assert qa.signal_start is None
    assert qa.signal_end is None
    assert qa.rfi_fraction == 0.5


def test_dc_spike_is_explicitly_replaced_not_merely_flagged():
    scan = _scan(7)
    step = DCSpike(StepConfig(step=StepType.DC_SPIKE, params={"context": "spr", "scan": scan}))
    signal = np.array([5.0, 5.0, 1.0, 100.0, 1.0, 5.0, 5.0])
    flags = empty_channel_flags(7)

    result = step.process(
        {"pipeline": "spr", "sec": 1, "channel_flags": flags},
        signal,
    )

    assert result is signal
    np.testing.assert_array_equal(signal, [5.0, 5.0, 1.0, 1.0, 1.0, 5.0, 5.0])
    assert not np.any(flags)


def test_load_calibration_propagates_load_flags_and_marks_invalid_results():
    sky_scan = _scan(4)
    load_scan = _scan(4)
    load_scan.scan_model.scan_type = ScanType.LOAD
    load_scan.scan_model.load = True
    load_scan.scan_model.status = ScanState.COMPLETE
    load_scan.mpr = np.array([2.0, 0.0, np.nan, 4.0])
    load_scan.mpr_flags[0] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    cal_q = Queue()
    cal_q.put(load_scan)
    step = LoadCal(
        StepConfig(
            step=StepType.LOAD,
            params={"context": "cal", "scan": sky_scan, "cal_q": cal_q},
        )
    )
    flags = empty_channel_flags(4)

    result = step.process(
        {"pipeline": "cal", "sec": 1, "channel_flags": flags},
        np.array([8.0, 8.0, 8.0, np.inf]),
    )

    assert result[0] == 4.0
    assert flags[0] & int(ChannelFlag.BANDPASS_EXCLUDED)
    assert flags[1] & int(ChannelFlag.CALIBRATION_INVALID)
    assert flags[2] & int(ChannelFlag.CALIBRATION_INVALID)
    assert np.all((flags[1:] & int(ChannelFlag.NONFINITE)) != 0)
