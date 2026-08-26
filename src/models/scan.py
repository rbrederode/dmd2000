# -*- coding: utf-8 -*-

import enum
import time
from datetime import datetime, timezone
import logging
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from models.comms import CommunicationStatus
from models.dsh import Feed
from models.fil import FilterBank
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

logger = logging.getLogger(__name__)

class ScanType(enum.IntEnum):
    UNKNOWN = 0     # Unknown scan type (default)
    SKY = 1         # Scan of the sky (i.e. target observation)
    LOAD = 2        # Scan of the load (terminated signal chain)
    TSYS = 3        # System Temperature calibration scan
    GAIN = 4        # Gain calibration scan

# ScanDataSource tracks whether a Scan was populated from raw IQ or from precomputed spectrum data
class ScanDataSource(enum.IntEnum):
    NONE = 0        # A scan starts as NONE
    RAW = 1         # Once RAW (IQ) data from the digitiser is loaded (e.g. time-domain voltage samples or raw FFTs)
    SPR = 2         # Summed Power Spectrum (SPR) data loaded from disk or synthesised by integrating other scans

class ScanState(enum.IntEnum):
    EMPTY = 0       # Scan has been created but no data loaded
    WIP = 1         # Scan is has some but not all data loaded
    ABORTED = 2     # Scan has been aborted (not fully loaded)
    COMPLETE = 3    # Scan has been fully loaded

class ScanModel(BaseModel):
    """A class representing the scan model."""

    _sample_timing = Schema({
        "read_start": Or(None, And(datetime, lambda v: isinstance(v, datetime))),
        "read_end": Or(None, And(datetime, lambda v: isinstance(v, datetime))),
        "gap": Or(None, And(float, lambda v: isinstance(v, float))),
    })

    schema = Schema({
        "_type": And(str, lambda v: v == "ScanModel"),
        "obs_id": Or(None, And(str, lambda v: isinstance(v, str))),                 # Observation ID for this scan (e.g. "obs001")
        "tgt_idx": Or(None, And(int, lambda v: isinstance(v, int))),                # Target index for this scan (e.g. 0 for first target, 1 for second target in the observation)
        "freq_scan": Or(None, And(int, lambda v: isinstance(v, int))),              # Frequency scan index for this scan (e.g. 0 for first freq scan, 1 for second freq scan in the target)
        "scan_iter": Or(None, And(int, lambda v: isinstance(v, int))),              # Scan iteration index for this scan (e.g. 0 for first scan, 1 for second scan on the same target and freq scan)
        "scan_type": And(ScanType, lambda v: isinstance(v, ScanType)),              # Type of scan (SKY, LOAD, TSYS, GAIN)
        "dig_id": Or(None, And(str, lambda v: isinstance(v, str))),                 # Digitiser ID for this scan (e.g. "dig001")
        "created": And(datetime, lambda v: isinstance(v, datetime)),                # Timestamp when the scan model was created
        "read_start": Or(None, And(datetime, lambda v: isinstance(v, datetime))),   # Timestamp when of the first sample that was read by the digitiser for this scan
        "read_end": Or(None, And(datetime, lambda v: isinstance(v, datetime))),     # Timestamp when the last sample that was read by the digitiser for this scan
        "gap": Or(None, And(float, lambda v: isinstance(v, float))),                # Gap in seconds between the previous set of samples and the current set of samples read by the digitiser 
        "start_idx": And(int, lambda v: v >= 0),                                    # Index of the first sample in the scan, corresponding to the digitiser read counter value for the first sample in the scan
        "duration": And(Or(int, float), lambda v: v >= 0),                          # Duration of the scan in seconds e.g. 60 seconds
        "sample_rate": And(Or(int, float), lambda v: v >= 0.0),                     # Sample rate in Hz
        "spectral_resolution": And(int, lambda v: v >= 0),                          # Number of spectral bins / FFT size for the analysis
        "start_freq": Or(None, And(Or(int, float), lambda v: v >= 0.0)),            # Start frequency of the samples in Hz (optional, can be calculated from center_freq and sample_rate if not provided)
        "center_freq": And(Or(int, float), lambda v: v >= 0.0),                     # Center frequency of the samples in Hz
        "end_freq": Or(None, And(Or(int, float), lambda v: v >= 0.0)),              # End frequency of the samples in Hz (optional, can be calculated from center_freq and sample_rate if not provided)
        "gain": And(Or(int, float), lambda v: 0 <= v <= 100.0),                     # Gain setting for the scan (0 to 100, where 100 is maximum gain)   
        "load": And(bool, lambda v: isinstance(v, bool)),                           # Flag indicating whether this scan is a load scan (True) or a sky scan (False)
        "load_scan_id": Or(None, And(str, lambda v: isinstance(v, str))),           # Scan id of load scan if this is a sky scan (in the form <obs_id>-<target_index>-<freq_scan>-<scan_iter>)
        "status": And(ScanState, lambda v: isinstance(v, ScanState)),               # Status of the scan (EMPTY, WIP, ABORTED, COMPLETE)
        "load_failures": And(int, lambda v: v >= 0),                                # Number of times loading this scan has failed (used for retry logic)
        "filter_bank": Or(None, And(FilterBank, lambda v: isinstance(v, FilterBank))), # Filterbank parameters for this scan (optional, can be used to specify filterbank creation parameters on a per-scan basis)
        "files_prefix": Or(None, And(str, lambda v: isinstance(v, str))),           # Prefix of filenames containing scan data (e.g. "ODT-2026-03-11T2100Z-dish002-7-0-0-dig002-g23.0-du60-bw2.05-cf1420.07-ch2048")
        "files_directory": Or(None, And(str, lambda v: isinstance(v, str))),        # Directory where the scan data is stored (e.g. "~/samples")
        "tgt_acq_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),   # Timestamp when the dish started tracking or scanning the target
        "tgt_acq_altaz": Or(None, And(dict, lambda v: isinstance(v.get("alt"), (int, float)) and isinstance(v.get("az"), (int, float)))),  # Actual dish Alt/Az when the target was acquired (degrees)
        "tgt_ref_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),   # Timestamp of an additional dish Alt/Az reference point during the scan
        "tgt_ref_altaz": Or(None, And(dict, lambda v: isinstance(v.get("alt"), (int, float)) and isinstance(v.get("az"), (int, float)))),  # Additional actual dish Alt/Az reference point during the scan (degrees)
        "tgt_alt_pec_rms": Or(None, And(Or(int, float), lambda v: v >= 0.0)),       # Target-level RMS periodic error correction in altitude (degrees)
        "tgt_az_pec_rms": Or(None, And(Or(int, float), lambda v: v >= 0.0)),        # Target-level RMS periodic error correction in azimuth (degrees)
        "tgt_pec_last_update": Or(None, And(datetime, lambda v: isinstance(v, datetime))),  # Timestamp of the PEC value copied from the Dish Manager
        "ws_id": Or(None, And(str, lambda v: isinstance(v, str))),                  # Weather station id used to contextualise this scan
        "ws_sec": Or(None, And(int, lambda v: v >= 0)),                             # Rolling weather summary window in seconds
        "wind_avg": Or(None, And(Or(int, float), lambda v: v >= 0.0)),              # Rolling average wind speed in m/s
        "wind_rms": Or(None, And(Or(int, float), lambda v: v >= 0.0)),              # Rolling RMS wind speed in m/s
        "wind_max": Or(None, And(Or(int, float), lambda v: v >= 0.0)),              # Rolling maximum wind speed in m/s
        "wind_sample_count": Or(None, And(int, lambda v: v >= 0)),                  # Number of wind samples used for the rolling summary
        "wind_sample_time": Or(None, And(datetime, lambda v: isinstance(v, datetime))),  # Timestamp of the latest weather sample used
        "synthesised": And(bool, lambda v: isinstance(v, bool)),                    # Flag indicating whether this scan was synthesised from other scans (e.g. for a load scan synthesised from a sky scan)
        "sample_times": Or(None, And(list, lambda v: all(isinstance(i, dict) and ScanModel._sample_timing.validate(i) for i in v))),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),            # Timestamp when the scan model was last updated
    })

    allowed_transitions = {}

    # Default values
    _defaults = {
        "_type": "ScanModel",
        "obs_id": datetime.now(timezone.utc).isoformat(),
        "tgt_idx": -1,
        "freq_scan": -1,
        "scan_iter": -1,
        "scan_type": ScanType.UNKNOWN,
        "dig_id": None,
        "created": datetime.now(timezone.utc),
        "read_start": None,
        "read_end": None,
        "gap": None,
        "start_idx": 0,
        "duration": 0,
        "sample_rate": 0.0,
        "spectral_resolution": 0,
        "start_freq": 0.0,
        "center_freq": 0.0,
        "end_freq": 0.0,
        "gain": 0.0,
        "load": False,
        "load_scan_id": None,
        "status": ScanState.EMPTY,
        "load_failures": 0,
        "filter_bank": None,
        "files_prefix": None,
        "files_directory": None,
        "tgt_acq_dt": None,
        "tgt_acq_altaz": None,
        "tgt_ref_dt": None,
        "tgt_ref_altaz": None,
        "tgt_alt_pec_rms": None,
        "tgt_az_pec_rms": None,
        "tgt_pec_last_update": None,
        "ws_id": None,
        "ws_sec": None,
        "wind_avg": None,
        "wind_rms": None,
        "wind_max": None,
        "wind_sample_count": None,
        "wind_sample_time": None,
        "synthesised": False,
        "sample_times": None,
        "last_update": datetime.now(timezone.utc)
    }

    def __init__(self, **kwargs):
        # Backward compatibility for older persisted scan metadata and callers.
        if "spectral_resolution" not in kwargs and "channels" in kwargs:
            kwargs["spectral_resolution"] = kwargs["channels"]

        # Apply defaults if not provided in kwargs
        for key, value in self._defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    @property
    def scan_id(self):
        if self.synthesised:
            return f"{self.obs_id}-{self.tgt_idx}-{self.freq_scan}"
        return f"{self.obs_id}-{self.tgt_idx}-{self.freq_scan}-{self.scan_iter}"

    def update_from_model(self, other_scan_model):
        """Update the current scan model with values from another scan model.
            Only update attributes that are different from the defaults.
        """
        if not isinstance(other_scan_model, ScanModel):
            raise XSoftwareFailure("Provided model is not a ScanModel instance")

        defaults = self.__class__._defaults
        updated = False

        for key in self.schema.schema.keys():
            if hasattr(other_scan_model, key):
                other_value = getattr(other_scan_model, key)
                # Only update if key not in defaults or value differs from default
                if key not in defaults or other_value != defaults[key]:
                    # Only update if value is different from current value
                    if getattr(self, key, None) != other_value:
                        setattr(self, key, other_value)
                        updated = True

        self.last_update = datetime.now(timezone.utc) if updated else self.last_update

    def equivalent(self, other) -> bool:
        """Check if this scan is equivalent to another scan.
            Equivalence is defined as covering the same scan parameters on the same digitiser.
        """
        if not isinstance(other, ScanModel):
            return False

        equivalent = str(self.dig_id) == str(other.dig_id) and \
                     float(self.center_freq) == float(other.center_freq) and \
                     float(self.sample_rate) == float(other.sample_rate) and \
                     int(self.spectral_resolution) == int(other.spectral_resolution) and \
                     float(self.gain) == float(other.gain)

        logger.debug(f"Scan equivalence is {equivalent} between:\n{self}\nand\n{other}\n")

        return equivalent

    def __eq__(self, other):
        if not isinstance(other, ScanModel):
            return False

        if self.scan_id != other.scan_id or self.created != other.created:
            return False

        return True

    def __str__(self):
        return f"ScanModel(scan_id={self.scan_id}, dig_id={self.dig_id}, type={self.scan_type.name}, status={self.status.name}, " + \
               f"center_freq={self.center_freq}, duration={self.duration}, sample_rate={self.sample_rate}, " + \
               f"spectral_resolution={self.spectral_resolution},  gain={self.gain}, " + \
               f"start_idx={self.start_idx}, created={self.created}, tgt_acq_dt={self.tgt_acq_dt}, " + \
               f"tgt_acq_altaz={self.tgt_acq_altaz}, " + \
               f"tgt_ref_dt={self.tgt_ref_dt}, tgt_ref_altaz={self.tgt_ref_altaz}, " + \
               f"read_start={self.read_start}, read_end={self.read_end}, " + \
               f"last_update={self.last_update})"   

if __name__ == "__main__":
    
    scan001 = ScanModel(
        dig_id="dig001",
        obs_id="obs001",
        tgt_idx=0,
        freq_scan=1,
        scan_iter=5,
        created=datetime.now(timezone.utc),
        read_start=datetime.now(timezone.utc),
        read_end=datetime.now(timezone.utc),
        start_idx=100,
        duration=60,
        sample_rate=1024.0,
        spectral_resolution=1024,
        center_freq=1420405752.0,
        gain=50.0,
        load=False,
        status=ScanState.WIP,
        load_failures=0,
        filter_bank=FilterBank(enabled=True, temporal_resolution=1.0, dtype="uint16"),
        last_update=datetime.now(timezone.utc)
    )

    scan002 = ScanModel(
        obs_id="obs002",
        tgt_idx=1,
        freq_scan=0,
        scan_iter=0
    )

    import pprint
    print("="*40)
    print("scan001 Model Initialized")
    print("="*40)
    pprint.pprint(scan001.to_dict())

    print("="*40)
    print("scan002 Model with Defaults Initialized")
    print("="*40)
    pprint.pprint(scan002.to_dict())

    print(scan002)

    scan001.update_from_model(scan002)
    print("="*40)
    print("scan001 after update from scan002")
    print("="*40)
    pprint.pprint(scan001.to_dict())
