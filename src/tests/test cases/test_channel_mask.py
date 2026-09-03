from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from models.scan import ScanModel, ScanState, ScanType
from obs.scan import Scan
from sdp.channel_mask import (
    CHANNEL_FLAG_DTYPE,
    ChannelFlag,
    channels_with_flag,
    contiguous_regions,
    empty_channel_flags,
    masked_values,
    masked_mean,
    masked_sum,
    valid_channels,
    reconstructed_total_power,
)


def _build_scan() -> Scan:
    return Scan(
        ScanModel(
            obs_id="obs001",
            tgt_idx=0,
            freq_scan=0,
            scan_type=ScanType.SKY,
            dig_id="dig001",
            created=datetime.now(timezone.utc),
            start_idx=0,
            duration=2,
            sample_rate=8,
            spectral_resolution=4,
            center_freq=1420.4e6,
            gain=10.0,
            load=False,
            status=ScanState.EMPTY,
            last_update=datetime.now(timezone.utc),
        )
    )


def test_scan_initialises_channel_flags_and_mpr_counts():
    scan = _build_scan()

    assert scan.spr_flags.shape == scan.spr.shape == (2, 4)
    assert scan.cal_flags.shape == scan.cal.shape == (2, 4)
    assert scan.mpr_flags.shape == scan.mpr.shape == (4,)
    assert scan.spr_flags.dtype == CHANNEL_FLAG_DTYPE
    assert scan.cal_flags.dtype == CHANNEL_FLAG_DTYPE
    assert scan.mpr_flags.dtype == CHANNEL_FLAG_DTYPE
    assert scan.mpr_valid_counts.shape == (4,)
    assert scan.mpr_valid_counts.dtype == np.uint16
    assert not np.any(scan.spr_flags)
    assert not np.any(scan.cal_flags)
    assert not np.any(scan.mpr_flags)
    assert not np.any(scan.mpr_valid_counts)


def test_channel_flags_can_record_multiple_reasons():
    flags = empty_channel_flags(3)
    flags[1] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    flags[1] |= int(ChannelFlag.RFI_DETECTED)

    assert flags[1] & int(ChannelFlag.BANDPASS_EXCLUDED)
    assert flags[1] & int(ChannelFlag.RFI_DETECTED)


def test_valid_channels_excludes_flags_and_nonfinite_values():
    values = np.array([1.0, 2.0, np.nan, 4.0])
    flags = empty_channel_flags(values.shape)
    flags[1] |= int(ChannelFlag.USER_EXCLUDED)

    np.testing.assert_array_equal(
        valid_channels(values, flags),
        np.array([True, False, False, True]),
    )


def test_masked_reductions_return_counts_and_nan_when_all_excluded():
    values = np.array([[1.0, 2.0, 3.0], [10.0, 20.0, 30.0]])
    flags = empty_channel_flags(values.shape)
    flags[:, 1] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    flags[1, :] |= int(ChannelFlag.RFI_DETECTED)

    totals, total_counts = masked_sum(values, flags, axis=1)
    means, mean_counts = masked_mean(values, flags, axis=1)

    np.testing.assert_allclose(totals[0], 4.0)
    np.testing.assert_allclose(means[0], 2.0)
    assert total_counts.tolist() == [2, 0]
    assert mean_counts.tolist() == [2, 0]
    assert np.isnan(totals[1])
    assert np.isnan(means[1])


def test_valid_channels_rejects_mismatched_shapes():
    with pytest.raises(ValueError, match="same shape"):
        valid_channels(np.ones(4), empty_channel_flags(3))


def test_display_helpers_mask_values_and_find_flag_regions():
    values = np.arange(6, dtype=np.float64)
    flags = empty_channel_flags(6)
    flags[1:3] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    flags[4] |= int(ChannelFlag.RFI_DETECTED)

    masked = masked_values(values, flags)

    np.testing.assert_array_equal(np.ma.getmaskarray(masked), [False, True, True, False, True, False])
    np.testing.assert_array_equal(
        channels_with_flag(flags, ChannelFlag.BANDPASS_EXCLUDED),
        [False, True, True, False, False, False],
    )
    assert contiguous_regions(channels_with_flag(flags, ChannelFlag.BANDPASS_EXCLUDED)) == [(1, 3)]


def test_total_power_linearly_reconstructs_contiguous_rfi_without_modifying_inputs():
    values = np.array([1.0, 2.0, 100.0, 200.0, 5.0, 6.0])
    original = values.copy()
    flags = empty_channel_flags(values.shape)
    flags[2:4] |= int(ChannelFlag.RFI_DETECTED)

    total, measured_count, filled_count = reconstructed_total_power(values, flags)

    # Side means are 1.5 and 5.5; the two replacements are 2.833... and 4.166...
    assert np.isclose(total, 21.0)
    assert int(measured_count) == 4
    assert int(filled_count) == 2
    np.testing.assert_array_equal(values, original)
    np.testing.assert_array_equal(
        channels_with_flag(flags, ChannelFlag.RFI_DETECTED),
        [False, False, True, True, False, False],
    )


def test_total_power_reconstructs_rfi_from_one_side_but_not_other_exclusion_reasons():
    values = np.array([500.0, 100.0, 3.0, 4.0, 900.0])
    flags = empty_channel_flags(values.shape)
    flags[0] |= int(ChannelFlag.BANDPASS_EXCLUDED)
    flags[1] |= int(ChannelFlag.RFI_DETECTED)
    flags[4] |= int(ChannelFlag.USER_EXCLUDED)

    total, measured_count, filled_count = reconstructed_total_power(values, flags)

    # The RFI channel has clean neighbours only on its right, whose mean is 3.5.
    assert np.isclose(total, 10.5)
    assert int(measured_count) == 2
    assert int(filled_count) == 1


def test_total_power_reconstructs_each_temporal_row_independently():
    values = np.array([[1.0, 100.0, 3.0], [4.0, 200.0, 6.0]])
    flags = empty_channel_flags(values.shape)
    flags[:, 1] |= int(ChannelFlag.RFI_DETECTED)

    totals, measured_counts, filled_counts = reconstructed_total_power(values, flags)

    np.testing.assert_allclose(totals, [6.0, 15.0])
    np.testing.assert_array_equal(measured_counts, [2, 2])
    np.testing.assert_array_equal(filled_counts, [1, 1])


class _FlaggingPipeline:
    def __init__(self):
        self.contexts = []

    def process(self, context, signal):
        self.contexts.append(context)
        flag = {
            "spr": ChannelFlag.USER_EXCLUDED,
            "cal": ChannelFlag.CALIBRATION_INVALID,
            "mpr": ChannelFlag.RFI_DETECTED,
        }[context["pipeline"]]
        context["channel_flags"][0] |= int(flag)
        return signal


def test_load_samples_passes_writable_flag_views_with_one_based_second():
    scan = _build_scan()
    pipeline = _FlaggingPipeline()
    scan.set_pipeline(pipeline)
    iq = np.arange(8, dtype=np.float32).astype(np.complex64)
    read_start = datetime.now(timezone.utc)

    assert scan.load_samples(
        sec=1,
        iq=iq,
        read_start=read_start,
        read_end=read_start + timedelta(microseconds=1),
    )

    assert [context["pipeline"] for context in pipeline.contexts] == ["spr", "cal", "mpr"]
    assert all(context["sec"] == 1 for context in pipeline.contexts)
    assert np.shares_memory(pipeline.contexts[0]["channel_flags"], scan.spr_flags[0])
    assert np.shares_memory(pipeline.contexts[1]["channel_flags"], scan.cal_flags[0])
    assert np.shares_memory(pipeline.contexts[2]["channel_flags"], scan.mpr_flags)
    assert scan.cal_flags[0, 0] & int(ChannelFlag.USER_EXCLUDED)
    assert scan.cal_flags[0, 0] & int(ChannelFlag.CALIBRATION_INVALID)
    assert scan.mpr_flags[0] & int(ChannelFlag.RFI_DETECTED)


def test_load_spr_maps_second_two_to_flag_row_one():
    scan = _build_scan()
    pipeline = _FlaggingPipeline()
    scan.set_pipeline(pipeline)

    assert scan.load_spr(sec=2, spr=np.arange(4, dtype=np.float64))

    assert [context["pipeline"] for context in pipeline.contexts] == ["cal", "mpr"]
    assert all(context["sec"] == 2 for context in pipeline.contexts)
    assert np.shares_memory(pipeline.contexts[0]["channel_flags"], scan.cal_flags[1])
    assert not np.any(scan.cal_flags[0])
    assert scan.cal_flags[1, 0] & int(ChannelFlag.CALIBRATION_INVALID)


def test_mpr_averages_only_usable_temporal_samples():
    scan = _build_scan()
    assert scan.load_spr(sec=1, spr=np.array([10.0, 20.0, 30.0, 40.0]))
    scan.cal_flags[0, 0] |= int(ChannelFlag.RFI_DETECTED)

    assert scan.load_spr(sec=2, spr=np.array([30.0, 40.0, 50.0, 60.0]))

    np.testing.assert_allclose(scan.mpr, [30.0, 30.0, 40.0, 50.0])
    np.testing.assert_array_equal(scan.mpr_valid_counts, [1, 2, 2, 2])
    assert not scan.mpr_flags[0] & int(ChannelFlag.RFI_DETECTED)
