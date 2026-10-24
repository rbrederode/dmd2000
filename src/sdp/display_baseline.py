"""Small, independent baseline copies for signal plots, without scan buffers."""

from dataclasses import dataclass

import numpy as np

from sdp.channel_mask import empty_channel_flags


@dataclass(frozen=True)
class DisplayBaseline:
    dig_id: str
    scan_id: str
    mpr: np.ndarray
    mpr_flags: np.ndarray

    @classmethod
    def copy_from(cls, load):
        if load is None or isinstance(load, cls):
            return load
        if load.mpr is None:
            return None
        values = load.mpr.copy()
        flags = getattr(load, "mpr_flags", None)
        flags = flags.copy() if isinstance(flags, np.ndarray) and flags.shape == values.shape else empty_channel_flags(values.shape)
        values.setflags(write=False)
        flags.setflags(write=False)
        return cls(load.scan_model.dig_id, load.scan_model.scan_id, values, flags)
