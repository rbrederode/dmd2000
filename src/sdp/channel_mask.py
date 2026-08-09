"""Channel-quality flags and mask-aware spectrum helpers.

The flags are stored separately from spectrum values so that measured data is
never replaced merely because a channel should be excluded from a calculation.
"""

import enum
from typing import TypeAlias

import numpy as np


class ChannelFlag(enum.IntFlag):
    """Reasons why a spectral channel may need special treatment."""

    NONE = 0
    BANDPASS_EXCLUDED = 1 << 0
    RFI_DETECTED = 1 << 1
    NONFINITE = 1 << 2
    CALIBRATION_INVALID = 1 << 3
    USER_EXCLUDED = 1 << 4


ChannelFlags: TypeAlias = np.ndarray
CHANNEL_FLAG_DTYPE = np.dtype(np.uint16)

DEFAULT_EXCLUDED_FLAGS = (
    ChannelFlag.BANDPASS_EXCLUDED
    | ChannelFlag.RFI_DETECTED
    | ChannelFlag.NONFINITE
    | ChannelFlag.CALIBRATION_INVALID
    | ChannelFlag.USER_EXCLUDED
)

# Total-power reconstruction is intentionally an implementation detail rather
# than an operator setting. Five clean channels per side provides a local
# estimate without allowing a distant part of the passband to dominate.
TOTAL_POWER_NEIGHBOUR_CHANNELS = 5


def empty_channel_flags(shape: int | tuple[int, ...]) -> ChannelFlags:
    """Return a zero-initialised channel-flag array with the standard dtype."""

    return np.zeros(shape, dtype=CHANNEL_FLAG_DTYPE)


def valid_channels(
    values: np.ndarray,
    flags: ChannelFlags,
    excluded_flags: ChannelFlag = DEFAULT_EXCLUDED_FLAGS,
) -> np.ndarray:
    """Return True for finite values that do not contain an excluded flag."""

    values_array = np.asarray(values)
    flags_array = np.asarray(flags)

    if values_array.shape != flags_array.shape:
        raise ValueError(
            "Spectrum values and channel flags must have the same shape; "
            f"got {values_array.shape} and {flags_array.shape}."
        )
    if not np.issubdtype(flags_array.dtype, np.integer):
        raise TypeError(f"Channel flags must use an integer dtype, got {flags_array.dtype}.")

    excluded_bits = int(excluded_flags)
    return np.isfinite(values_array) & ((flags_array & excluded_bits) == 0)


def masked_values(
    values: np.ndarray,
    flags: ChannelFlags,
    excluded_flags: ChannelFlag = DEFAULT_EXCLUDED_FLAGS,
) -> np.ma.MaskedArray:
    """Return values as a masked array with unusable channels hidden."""

    values_array = np.asarray(values)
    return np.ma.array(
        values_array,
        mask=~valid_channels(values_array, flags, excluded_flags=excluded_flags),
        copy=False,
    )


def channels_with_flag(flags: ChannelFlags, flag: ChannelFlag) -> np.ndarray:
    """Return a Boolean array selecting channels carrying ``flag``."""

    flags_array = np.asarray(flags)
    if not np.issubdtype(flags_array.dtype, np.integer):
        raise TypeError(f"Channel flags must use an integer dtype, got {flags_array.dtype}.")
    return (flags_array & int(flag)) != 0


def contiguous_regions(mask: np.ndarray) -> list[tuple[int, int]]:
    """Return ``(start, end)`` pairs for contiguous True regions; end is exclusive."""

    mask_array = np.asarray(mask, dtype=bool)
    if mask_array.ndim != 1:
        raise ValueError(f"Contiguous-region mask must be one-dimensional, got {mask_array.shape}.")
    padded = np.pad(mask_array.astype(np.int8), (1, 1), mode="constant")
    changes = np.diff(padded)
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    return list(zip(starts.tolist(), ends.tolist()))


def reconstructed_total_power(
    values: np.ndarray,
    flags: ChannelFlags,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Calculate total power after locally reconstructing RFI-flagged channels.

    The input values and flags are never modified. Channels excluded for any
    reason other than :attr:`ChannelFlag.RFI_DETECTED` remain excluded. Each
    contiguous RFI region inside the usable passband is filled temporarily by
    a linear transition between the means of up to five immediately adjacent
    clean channels on either side. At a one-sided boundary, the available side
    mean is used as a constant estimate. A region with no clean neighbour is
    left unfilled.

    The last dimension is treated as frequency, so both individual spectra and
    time-by-frequency matrices are supported. Returns ``(total_power,
    measured_count, filled_count)`` for every leading-dimension entry.
    """

    values_array = np.asarray(values)
    flags_array = np.asarray(flags)
    if values_array.shape != flags_array.shape:
        raise ValueError(
            "Spectrum values and channel flags must have the same shape; "
            f"got {values_array.shape} and {flags_array.shape}."
        )
    if values_array.ndim < 1:
        raise ValueError("Total-power reconstruction requires at least one frequency dimension.")
    if not np.issubdtype(flags_array.dtype, np.integer):
        raise TypeError(f"Channel flags must use an integer dtype, got {flags_array.dtype}.")

    channels = values_array.shape[-1]
    leading_shape = values_array.shape[:-1]
    value_rows = values_array.reshape(-1, channels)
    flag_rows = flags_array.reshape(-1, channels)
    totals = np.full(value_rows.shape[0], np.nan, dtype=np.float64)
    measured_counts = np.zeros(value_rows.shape[0], dtype=np.int64)
    filled_counts = np.zeros(value_rows.shape[0], dtype=np.int64)

    rfi_bit = int(ChannelFlag.RFI_DETECTED)
    permanent_exclusions = int(DEFAULT_EXCLUDED_FLAGS & ~ChannelFlag.RFI_DETECTED)

    for row_index, (row_values, row_flags) in enumerate(zip(value_rows, flag_rows)):
        in_usable_band = np.isfinite(row_values) & ((row_flags & permanent_exclusions) == 0)
        rfi = in_usable_band & ((row_flags & rfi_bit) != 0)
        clean = in_usable_band & ~rfi
        measured_counts[row_index] = int(np.count_nonzero(clean))

        total = float(np.sum(row_values[clean], dtype=np.float64))
        for start, end in contiguous_regions(rfi):
            left_values = []
            channel = start - 1
            while channel >= 0 and len(left_values) < TOTAL_POWER_NEIGHBOUR_CHANNELS:
                if not clean[channel]:
                    break
                left_values.append(float(row_values[channel]))
                channel -= 1

            right_values = []
            channel = end
            while channel < channels and len(right_values) < TOTAL_POWER_NEIGHBOUR_CHANNELS:
                if not clean[channel]:
                    break
                right_values.append(float(row_values[channel]))
                channel += 1

            if left_values and right_values:
                left_mean = float(np.mean(left_values))
                right_mean = float(np.mean(right_values))
                region_size = end - start
                fractions = np.arange(1, region_size + 1, dtype=np.float64) / (region_size + 1)
                replacements = left_mean + fractions * (right_mean - left_mean)
            elif left_values:
                replacements = np.full(end - start, np.mean(left_values), dtype=np.float64)
            elif right_values:
                replacements = np.full(end - start, np.mean(right_values), dtype=np.float64)
            else:
                continue

            total += float(np.sum(replacements, dtype=np.float64))
            filled_counts[row_index] += replacements.size

        if measured_counts[row_index] > 0 or filled_counts[row_index] > 0:
            totals[row_index] = total

    return (
        totals.reshape(leading_shape),
        measured_counts.reshape(leading_shape),
        filled_counts.reshape(leading_shape),
    )


def masked_sum(
    values: np.ndarray,
    flags: ChannelFlags,
    axis: int | tuple[int, ...] | None = None,
    keepdims: bool = False,
    excluded_flags: ChannelFlag = DEFAULT_EXCLUDED_FLAGS,
) -> tuple[np.ndarray, np.ndarray]:
    """Sum usable values and return ``(sum, valid_count)``.

    A reduction with no usable values returns NaN rather than zero.
    """

    values_array = np.asarray(values)
    valid = valid_channels(values_array, flags, excluded_flags=excluded_flags)
    totals = np.sum(np.where(valid, values_array, 0.0), axis=axis, keepdims=keepdims)
    counts = np.sum(valid, axis=axis, keepdims=keepdims, dtype=np.int64)
    totals = np.where(counts > 0, totals, np.nan)
    return totals, counts


def masked_mean(
    values: np.ndarray,
    flags: ChannelFlags,
    axis: int | tuple[int, ...] | None = None,
    keepdims: bool = False,
    excluded_flags: ChannelFlag = DEFAULT_EXCLUDED_FLAGS,
) -> tuple[np.ndarray, np.ndarray]:
    """Average usable values and return ``(mean, valid_count)``.

    A reduction with no usable values returns NaN rather than zero.
    """

    totals, counts = masked_sum(
        values,
        flags,
        axis=axis,
        keepdims=keepdims,
        excluded_flags=excluded_flags,
    )
    means = np.divide(
        totals,
        counts,
        out=np.full_like(totals, np.nan, dtype=np.result_type(totals, np.float64)),
        where=counts > 0,
    )
    return means, counts
