"""Optional baseline subtraction on calibrated spectra."""
import numpy as np

from models.scan import ScanType
from sdp.channel_mask import valid_channels
from sdp.pipeline.pipeline_factory import ProcessingStep
from sdp.spectral_baseline import subtract_baseline


class Baseline(ProcessingStep):
    def __init__(self, config):
        super().__init__(config)
        self.scan = config.params.get("scan")
        self.method = config.params.get("method", "flatten")
        if self.method not in ("linear", "constant", "flatten", "none"):
            raise ValueError("Baseline method must be linear, constant, flatten or none")
        if config.params.get("context", "cal") != "cal":
            raise ValueError("Baseline subtraction must use the cal context")
        from sdp.pipeline.steps.bpf import BandpassFilter
        # Percentiles of usable channels, independent of flagged bandpass edges.
        self.ranges = BandpassFilter._validate_ranges(config.params.get("ranges_pct", [[0, 10], [90, 100]]))

    def process(self, context, signal):
        scan = self.scan
        # LOAD reference spectra must retain their power for division calibration.
        if self.method == "none" or (scan is not None and scan.get_scan_type() == ScanType.LOAD):
            return signal
        values = np.ma.asarray(signal, dtype=float)
        valid = valid_channels(values.filled(np.nan), context.get("channel_flags")) & ~np.ma.getmaskarray(values)
        x = np.asarray(context.get("baseline_x", np.arange(values.size)), dtype=float)
        if x.shape != values.shape or values.ndim != 1:
            raise ValueError("Baseline coordinates and spectrum must be matching 1D arrays")
        valid &= np.isfinite(x)
        mask = context.get("baseline_fit_mask")
        if mask is None:
            indices = np.flatnonzero(valid)
            indices = indices[np.argsort(x[indices])]
            mask = np.zeros(values.shape, dtype=bool)
            for low, high in self.ranges:
                mask[indices[int(len(indices)*low/100):int(np.ceil(len(indices)*high/100))]] = True
        else:
            mask = np.asarray(mask, dtype=bool)
            if mask.shape != values.shape:
                raise ValueError("Baseline fitting mask must match spectrum")
        corrected, _ = subtract_baseline(x, signal, self.method, valid & mask,
                                        fit_line_free=True, usable_mask=valid)
        return corrected

    @classmethod
    def describe(cls):
        return "Subtract a linear or constant baseline, or flatten its slope while retaining a level; preserve LOAD references."
