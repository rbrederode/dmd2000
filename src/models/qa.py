import enum
from datetime import datetime, timezone
import logging
import numpy as np
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from util.xbase import XSoftwareFailure

logger = logging.getLogger(__name__)

class QA(BaseModel):
    """A class representing Quality Attributes for a signal (spectrum)."""

    schema = Schema({
        "_type":            And(str, lambda v: v == "QA"),
        "idx":              And(Or(None, int, np.integer), lambda v: v is None or v >= 0),  # Index of the QA attributes (e.g., second index for spr/cal pipelines)
        "baseline":         Or(None, float),                                                # Baseline level from the noise region median
        "snr_db":           Or(None, float),                                                # SNR (dB): 10 * log10(signal / noise)
        "signal_db":        Or(None, float),                                                # Signal (peak above baseline): max of signal region minus baseline, converted to dB        
        "noise_db":         Or(None, float),                                                # Noise (robust RMS via MAD): 1.4826 * median absolute deviation of noise region, converted to dB
        "signal_start":     And(Or(None, int, np.integer), lambda v: v is None or v >= 0),  # Start channel of signal region
        "signal_end":       And(Or(None, int, np.integer), lambda v: v is None or v >= 0),  # End channel of signal region (inclusive)
        "rfi_fraction":     And(Or(None, float), lambda v: v is None or 0.0 <= v <= 1.0),   # RFI fraction: fraction of channels in signal region flagged as RFI
        "fwhm":             And(Or(None, float), lambda v: v is None or v >= 0.0),          # FWHM (full width at half maximum): width of signal region above half max
        "dynamic_range_db": And(Or(None, float), lambda v: v is None or v >= 0.0),          # Dynamic range (dB): 10 * log10(peak signal / noise)
        "signal_pwr_db":    Or(None, float),                                                # Signal power (dB): 10 * log10(signal power)
        "last_update":      And(datetime, lambda v: isinstance(v, datetime)),
    })

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "QA",
            "idx": None,
            "baseline": None,
            "snr_db": None,
            "signal_db": None,
            "noise_db": None,
            "signal_start": None,
            "signal_end": None,
            "rfi_fraction": None,
            "fwhm": None,
            "dynamic_range_db": None,
            "signal_pwr_db": None,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def __str__(self):
        return f"QA(\n  idx={self.idx},\n  baseline={self.baseline},\n  snr_db={self.snr_db}, \n  signal_db={self.signal_db},\n  noise_db={self.noise_db},\n  signal_start={self.signal_start},\n  " + \
            f"signal_end={self.signal_end},\n  rfi_fraction={self.rfi_fraction},\n  fwhm={self.fwhm},\n  dynamic_range_db={self.dynamic_range_db},\n  signal_pwr_db={self.signal_pwr_db}, " + \
            f"last_update={self.last_update.isoformat()})"

    def to_dict(self):
        """Serialise QA metrics compactly by omitting fields that are still None."""
        data = {"_type": "QA"}

        for key, value in self._data.items():
            if key == "_type":
                continue
            if value is None:
                continue
            data[key] = BaseModel._serialise(value)

        return data

class ScanQA(BaseModel):
    """A class representing Quality Attributes for an entire scan."""

    schema = Schema({
        "_type": And(str, lambda v: v == "ScanQA"),
        "scan_id": And(str, lambda v: isinstance(v, str)),
        "spr_qa": And(object, lambda v: v is None or (isinstance(v, np.ndarray) and v.dtype == object and all(isinstance(item, QA) or item is None for item in v))),
        "cal_qa": And(object, lambda v: v is None or (isinstance(v, np.ndarray) and v.dtype == object and all(isinstance(item, QA) or item is None for item in v))),
        "mpr_qa": Or(None, And(QA, lambda v: isinstance(v, QA))),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    def __init__(self, scan_duration=None, **kwargs):
        # Default values
        defaults = {
            "_type": "ScanQA",
            "scan_id": None,
            "spr_qa": None,
            "cal_qa": None,
            "mpr_qa": None,
            "last_update": datetime.now(timezone.utc),
        }
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        # If scan_duration is provided, initialize arrays of that length
        if scan_duration is not None:
            kwargs["spr_qa"] = np.empty(scan_duration, dtype=object)
            kwargs["cal_qa"] = np.empty(scan_duration, dtype=object)
        else:
            # If lists are provided, convert to arrays
            if isinstance(kwargs["spr_qa"], list):
                kwargs["spr_qa"] = np.array(kwargs["spr_qa"], dtype=object)
            if isinstance(kwargs["cal_qa"], list):
                kwargs["cal_qa"] = np.array(kwargs["cal_qa"], dtype=object)

        super().__init__(**kwargs)

    def __str__(self):
        return f"ScanQA(\n  scan_id={self.scan_id},\n\n  spr_qa=[{', '.join(str(qa) for qa in self.spr_qa)}],\n\n  cal_qa=[{', '.join(str(qa) for qa in self.cal_qa)}],\n\n  mpr_qa={str(self.mpr_qa)},\n  last_update={self.last_update.isoformat()})"

    def to_dict(self):
        """Serialise scan QA compactly while preserving second-by-second indexing."""
        data = {
            "_type": "ScanQA",
            "scan_id": self.scan_id,
            "last_update": BaseModel._serialise(self.last_update),
        }

        if self.spr_qa is not None and any(item is not None for item in self.spr_qa):
            data["spr_qa"] = [BaseModel._serialise(item) if item is not None else None for item in self.spr_qa]

        if self.cal_qa is not None and any(item is not None for item in self.cal_qa):
            data["cal_qa"] = [BaseModel._serialise(item) if item is not None else None for item in self.cal_qa]

        if self.mpr_qa is not None:
            data["mpr_qa"] = BaseModel._serialise(self.mpr_qa)

        return data

    def getQA(self, pipeline: str, idx: int) -> QA:
        """
        Get the QA attributes for a specific pipeline in this scan.
            :param pipeline: Name of the pipeline to get QA for (e.g., "calibration", "mean_power_spectrum")
            :param idx: Index of the QA attributes to retrieve (zero-based index)
            :returns: A QA instance containing the QA attributes for the specified pipeline, or None if not found
        """
        if pipeline == "spr":
            if self.spr_qa is not None and 0 <= idx < len(self.spr_qa):
                if self.spr_qa[idx] is None or getattr(self.spr_qa[idx], 'idx', None) != idx:
                    self.spr_qa[idx] = QA(idx=idx)
                return self.spr_qa[idx]
            else:
                logger.warning(f"ScanQA: spr_qa array not initialized or idx {idx} out of range.")
                return None
        elif pipeline == "cal":
            if self.cal_qa is not None and 0 <= idx < len(self.cal_qa):
                if self.cal_qa[idx] is None or getattr(self.cal_qa[idx], 'idx', None) != idx:
                    self.cal_qa[idx] = QA(idx=idx)
                return self.cal_qa[idx]
            else:
                logger.warning(f"ScanQA: cal_qa array not initialized or idx {idx} out of range.")
                return None
        elif pipeline == "mpr":
            if self.mpr_qa is not None:
                if getattr(self.mpr_qa, 'idx', None) != idx:
                    self.mpr_qa.idx = idx
                return self.mpr_qa
            else:
                new_qa = QA(idx=idx)
                self.mpr_qa = new_qa
                return new_qa
        else:
            raise XSoftwareFailure(f"ScanQA: Unknown pipeline name '{pipeline}' for getting QA attributes.")

if __name__ == "__main__":

    import pprint

    logger.setLevel(logging.INFO)
    logging.basicConfig(level=logging.INFO)

    qa = QA(idx=0, snr_db=10.5, signal_db=-50.0, noise_db=-60.5, signal_start=100, signal_end=200, rfi_fraction=0.05, fwhm=20.0, dynamic_range=10.5, signal_pwr_db=-50.0)
    pprint.pprint(qa.to_dict())

    scan_qa = ScanQA(scan_id="obs001_0_1_5", scan_duration=5)
    pprint.pprint(scan_qa.to_dict())
    print(scan_qa)

    scan_qa.getQA("spr", 0).snr_db = 10.5
    scan_qa.getQA("cal", 0).signal_db = -50.0
    scan_qa.getQA("mpr", 0).noise_db = -60.5
    pprint.pprint(scan_qa.to_dict())
    print(scan_qa)
