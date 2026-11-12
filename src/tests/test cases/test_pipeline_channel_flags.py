from datetime import datetime, timezone
from queue import Queue

import numpy as np
import pytest

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
    assert qa.noise_db == -120.0  # Preserve the legacy QA floor for zero MAD.
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


def test_qa_and_profiler_agree_with_identical_noise_samples_estimators_and_peak():
    from util.lab_profiler import observation_noise_metrics
    for estimator in ('mean_rms', 'median_mad'):
        scan = _scan(21)
        signal = np.tile([1., 3.], 11)[:21]
        signal[8:13] = [4, 8, 12, 8, 4]
        flags = empty_channel_flags(21)
        flags[0] |= int(ChannelFlag.BANDPASS_EXCLUDED)
        flags[3] |= int(ChannelFlag.RFI_DETECTED)
        signal[[0, 3]] = 1e9  # Neither caller may use flagged peaks/noise.
        original, original_flags = signal.copy(), flags.copy()
        step = QA(StepConfig(step=StepType.QA, params=dict(scan=scan, smooth_window=1,
                                                         window_frac=.2, noise_estimator=estimator)))
        returned = step.process(dict(pipeline='mpr', sec=1, channel_flags=flags), signal)
        qa = scan.scan_qa.getQA('mpr', 0)
        profile = np.ma.array(signal, mask=flags != 0)
        measured = observation_noise_metrics(np.arange(21.), profile, 0, 20, [[0, 7], [13, 20]],
                                              noise_estimator=estimator, smooth_window=1)
        assert returned is signal
        np.testing.assert_array_equal(signal, original)
        np.testing.assert_array_equal(flags, original_flags)
        assert measured['noise_sample_count'] == 14
        assert measured['noise_estimator'] == estimator
        assert qa.baseline == measured['noise_level_k']
        assert qa.snr_db == measured['peak_snr_db']
        assert qa.noise_db == 10 * np.log10(measured['noise_rms_k'])
        assert qa.signal_db == 10 * np.log10(measured['peak_above_noise_floor'])


def test_cal_baseline_flows_into_mpr_without_altering_spr():
    from models.scan import ScanDataSource
    from sdp.pipeline.steps.baseline import Baseline
    from sdp.pipeline.pipeline_factory import ProcessingPipeline
    scan = _scan(201)
    x = np.arange(201.)
    line = 10*np.exp(-((x-100)/8)**2)
    power = 4 + .02*x + line
    scan.spr[0] = power
    scan.loaded_secs[0] = True
    scan.data_source = ScanDataSource.SPR
    scan.set_pipeline(ProcessingPipeline([Baseline(StepConfig(step=StepType.BASELINE,
                                        params={"context": "cal", "method": "linear", "scan": scan}))]))
    assert scan.process_pipeline()
    np.testing.assert_array_equal(scan.spr[0], power)
    np.testing.assert_allclose(scan.cal[0], line, atol=1e-10)
    np.testing.assert_allclose(scan.mpr, line, atol=1e-10)


@pytest.mark.parametrize('window_frac,smooth_window', [(.2, 10), (.35, 5)])
def test_qa_profiler_and_both_plot_coordinates_share_default_robust_policy(window_frac, smooth_window):
    from matplotlib import pyplot as plt
    from obs.spectral_annotations import annotate_spectral_metrics
    from sdp.spectral_metrics import observation_noise_metrics, select_qa_channels
    from sdp.channel_mask import masked_values
    scan = _scan(201)
    x = np.arange(201.)
    power = 5 + .2*np.sin(x) + 10*np.exp(-((x-110)/12)**2)
    power[40] = 30  # Isolated raw maximum is not the supported smoothed peak.
    flags = empty_channel_flags(len(x))
    flags[:20] = flags[-15:] = ChannelFlag.BANDPASS_EXCLUDED
    flags[70] = ChannelFlag.RFI_DETECTED
    power[70] = 1e9
    policy = dict(window_frac=window_frac, smooth_window=smooth_window, noise_estimator='median_mad')
    QA(StepConfig(step=StepType.QA, params=dict(scan=scan, **policy))).process(
        dict(pipeline='mpr', sec=1, channel_flags=flags), power)
    qa = scan.scan_qa.getQA('mpr', 0)
    velocity = 100-x
    frequency = 1420.4+.004*(x-100)
    profile = masked_values(power, flags)
    selection = select_qa_channels(profile, window_frac=window_frac, smooth_window=smooth_window)
    assert selection['peak_bin'] != 40
    expected = observation_noise_metrics(velocity, profile, -100, 100, **policy)
    assert expected['noise_sample_count'] == int(selection['noise_mask'].sum())
    assert expected['noise_window_selection'] == 'qa_peak_exclusion'
    assert qa.baseline == expected['noise_level_k']
    assert qa.snr_db == expected['peak_snr_db']
    assert qa.noise_db == 10*np.log10(expected['noise_rms_k'])
    figure, axes = plt.subplots(1, 2)
    try:
        measured = [annotate_spectral_metrics(axes[0], velocity[::-1], profile[::-1], -100, 100,
                                              label='SKY', flags=flags[::-1], qa_settings=policy),
                    annotate_spectral_metrics(axes[1], frequency, profile, frequency.min(), frequency.max(),
                                              label='SKY', flags=flags, velocity=velocity, unit='MHz', qa_settings=policy)]
        for axis, metrics in zip(axes, measured):
            assert metrics['noise_level_k'] == qa.baseline
            assert metrics['peak_snr_db'] == qa.snr_db
            assert metrics['noise_rms_k'] == expected['noise_rms_k']
            baseline = next(line for line in axis.lines if line.get_label() == 'Median noise baseline')
            np.testing.assert_array_equal(baseline.get_ydata(), [qa.baseline, qa.baseline])
            assert 'Robust Noise:' in axis.texts[0].get_text()
            assert 'Median baseline:' in axis.texts[0].get_text()
    finally:
        plt.close(figure)
