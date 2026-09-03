from datetime import datetime, timezone

import numpy as np
import pytest

from models.pipeline import PipelineConfig, StepConfig, StepType
from models.scan import ScanModel, ScanState, ScanType
from obs.scan import Scan
from sdp.channel_mask import ChannelFlag, empty_channel_flags
from sdp.pipeline.pipeline_factory import ProcessingPipeline, ProcessingPipelineFactory
from sdp.pipeline.steps.bpf import BPF, BandpassFilter


def _step(ranges_pct):
    return BandpassFilter(
        StepConfig(
            step=StepType.BANDPASS_FILTER,
            params={"context": "cal", "ranges_pct": ranges_pct},
        )
    )


def _context(flags, pipeline="cal"):
    return {"pipeline": pipeline, "sec": 1, "channel_flags": flags}


def test_bpf_flags_outer_quarters_without_changing_spectrum():
    signal = np.arange(8, dtype=np.float64)
    original = signal.copy()
    flags = empty_channel_flags(signal.shape)

    result = _step([[25, 75]]).process(_context(flags), signal)

    assert result is signal
    np.testing.assert_array_equal(signal, original)
    np.testing.assert_array_equal(
        (flags & int(ChannelFlag.BANDPASS_EXCLUDED)) != 0,
        [True, True, False, False, False, False, True, True],
    )


def test_bpf_unions_disjoint_ranges_using_channel_centres():
    signal = np.ones(20)
    flags = empty_channel_flags(signal.shape)

    _step([[20, 50], [55, 80]]).process(_context(flags), signal)

    allowed_indices = np.flatnonzero((flags & int(ChannelFlag.BANDPASS_EXCLUDED)) == 0)
    np.testing.assert_array_equal(allowed_indices, [4, 5, 6, 7, 8, 9, 11, 12, 13, 14, 15])


def test_bpf_preserves_other_flags_and_replaces_its_previous_selection():
    signal = np.ones(4)
    flags = empty_channel_flags(signal.shape)
    flags[0] |= int(ChannelFlag.RFI_DETECTED)
    step = _step([[25, 75]])
    step.process(_context(flags), signal)

    step.ranges_pct = ((0.0, 100.0),)
    step.process(_context(flags), signal)

    assert flags[0] & int(ChannelFlag.RFI_DETECTED)
    assert not np.any(flags & int(ChannelFlag.BANDPASS_EXCLUDED))


@pytest.mark.parametrize(
    "ranges_pct, message",
    [
        (None, "non-empty"),
        ([], "non-empty"),
        ([[25]], "exactly"),
        ([["25", 75]], "numeric"),
        ([[np.nan, 75]], "finite"),
        ([[-1, 75]], "0 <= start"),
        ([[75, 25]], "0 <= start"),
        ([[25, 101]], "0 <= start"),
    ],
)
def test_bpf_rejects_invalid_ranges(ranges_pct, message):
    with pytest.raises(ValueError, match=message):
        _step(ranges_pct)


def test_bpf_requires_matching_writable_flag_array():
    signal = np.ones(4)
    step = _step([[25, 75]])

    with pytest.raises(ValueError, match="channel_flags"):
        step.process({"pipeline": "cal", "sec": 1}, signal)
    with pytest.raises(ValueError, match="same shape"):
        step.process(_context(empty_channel_flags(3)), signal)
    with pytest.raises(ValueError, match="unsigned integer"):
        step.process(_context(np.zeros(4, dtype=np.float64)), signal)
    read_only_flags = empty_channel_flags(4)
    read_only_flags.flags.writeable = False
    with pytest.raises(ValueError, match="writable"):
        step.process(_context(read_only_flags), signal)
    with pytest.raises(ValueError, match="at least one channel"):
        step.process(_context(empty_channel_flags(0)), np.array([]))


def test_pipeline_applies_bpf_only_in_its_configured_context():
    signal = np.ones(4)
    flags = empty_channel_flags(signal.shape)
    pipeline = ProcessingPipeline([_step([[25, 75]])])

    pipeline.process(_context(flags, pipeline="spr"), signal)
    assert not np.any(flags)

    pipeline.process(_context(flags, pipeline="cal"), signal)
    assert np.count_nonzero(flags & int(ChannelFlag.BANDPASS_EXCLUDED)) == 2


def test_factory_resolves_enum_and_bpf_alias():
    factory = ProcessingPipelineFactory(PipelineConfig())

    assert factory.get_step_class(StepType.BANDPASS_FILTER) is BandpassFilter
    assert factory.get_step_class("bpf") is BPF


def test_bpf_step_config_round_trips_with_ranges():
    config = StepConfig(
        step=StepType.BANDPASS_FILTER,
        params={"context": "cal", "ranges_pct": [[20, 50], [55, 80]]},
    )

    restored = StepConfig.from_dict(config.to_dict())

    assert restored.step == StepType.BANDPASS_FILTER
    assert restored.params["ranges_pct"] == [[20, 50], [55, 80]]


def test_scan_pipeline_records_bpf_flags_but_preserves_calibrated_values():
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
            sample_rate=8,
            spectral_resolution=8,
            center_freq=1420.4e6,
            gain=10.0,
            load=False,
            status=ScanState.EMPTY,
            last_update=datetime.now(timezone.utc),
        )
    )
    scan.set_pipeline(ProcessingPipeline([_step([[25, 75]])]))
    spectrum = np.arange(8, dtype=np.float64)

    assert scan.load_spr(sec=1, spr=spectrum)

    np.testing.assert_array_equal(scan.cal[0], spectrum)
    np.testing.assert_array_equal(
        (scan.cal_flags[0] & int(ChannelFlag.BANDPASS_EXCLUDED)) != 0,
        [True, True, False, False, False, False, True, True],
    )
    np.testing.assert_array_equal(scan.mpr, spectrum)
    np.testing.assert_array_equal(scan.mpr_valid_counts, [0, 0, 1, 1, 1, 1, 0, 0])
    np.testing.assert_array_equal(
        (scan.mpr_flags & int(ChannelFlag.BANDPASS_EXCLUDED)) != 0,
        [True, True, False, False, False, False, True, True],
    )
