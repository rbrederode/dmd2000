"""Bandpass-selection pipeline step."""

import math
from numbers import Real
from typing import Any

import numpy as np

from models.pipeline import StepConfig
from sdp.channel_mask import ChannelFlag
from sdp.pipeline.pipeline_factory import ProcessingStep


class BandpassFilter(ProcessingStep):
    """Flag channels outside configured percentage ranges without changing data."""

    def __init__(self, config: StepConfig = None):
        super().__init__(config)
        self.ranges_pct = self._validate_ranges(config.params.get("ranges_pct"))

    @staticmethod
    def _validate_ranges(ranges_pct: Any) -> tuple[tuple[float, float], ...]:
        if not isinstance(ranges_pct, (list, tuple)) or not ranges_pct:
            raise ValueError("BandpassFilter: 'ranges_pct' must be a non-empty list of [start, end] pairs.")

        validated = []
        for index, range_pct in enumerate(ranges_pct):
            if not isinstance(range_pct, (list, tuple)) or len(range_pct) != 2:
                raise ValueError(f"BandpassFilter: range {index} must contain exactly [start, end].")

            start, end = range_pct
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, Real)
                or not isinstance(end, Real)
            ):
                raise ValueError(f"BandpassFilter: range {index} boundaries must be numeric.")

            start = float(start)
            end = float(end)
            if not math.isfinite(start) or not math.isfinite(end):
                raise ValueError(f"BandpassFilter: range {index} boundaries must be finite.")
            if not 0.0 <= start < end <= 100.0:
                raise ValueError(
                    f"BandpassFilter: range {index} must satisfy 0 <= start < end <= 100; "
                    f"got [{start}, {end}]."
                )
            validated.append((start, end))

        return tuple(validated)

    def process(self, context: Any, signal: Any) -> Any:
        if not isinstance(signal, np.ndarray):
            raise ValueError("BandpassFilter: signal must be a numpy array.")
        if signal.ndim != 1:
            raise ValueError(f"BandpassFilter: signal must be one-dimensional, got shape {signal.shape}.")
        if signal.size == 0:
            raise ValueError("BandpassFilter: signal must contain at least one channel.")
        if not isinstance(context, dict):
            raise ValueError("BandpassFilter: context must be a dictionary.")

        channel_flags = context.get("channel_flags")
        if not isinstance(channel_flags, np.ndarray):
            raise ValueError("BandpassFilter: context must contain a NumPy 'channel_flags' array.")
        if channel_flags.shape != signal.shape:
            raise ValueError(
                "BandpassFilter: signal and channel_flags must have the same shape; "
                f"got {signal.shape} and {channel_flags.shape}."
            )
        if not np.issubdtype(channel_flags.dtype, np.unsignedinteger):
            raise ValueError(
                f"BandpassFilter: channel_flags must use an unsigned integer dtype, got {channel_flags.dtype}."
            )
        if not channel_flags.flags.writeable:
            raise ValueError("BandpassFilter: channel_flags must be writable.")

        channels = signal.shape[0]
        channel_pct = (np.arange(channels, dtype=np.float64) + 0.5) * 100.0 / channels
        allowed = np.zeros(channels, dtype=bool)
        for start, end in self.ranges_pct:
            allowed |= (channel_pct >= start) & (channel_pct < end)

        flag_bit = np.array(int(ChannelFlag.BANDPASS_EXCLUDED), dtype=channel_flags.dtype)
        clear_mask = np.bitwise_not(flag_bit)
        channel_flags[:] &= clear_mask
        channel_flags[~allowed] |= flag_bit

        return signal

    @classmethod
    def describe(cls) -> str:
        return (
            "Flag channels outside one or more allowed percentage ranges while "
            "preserving the measured spectrum values."
        )


# Concise class alias for callers that use the common step abbreviation.
BPF = BandpassFilter
