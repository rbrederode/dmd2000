import enum
import logging
import math
import numpy as np
import re
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

import astropy.units as u
from astropy.coordinates import get_body
from astropy.coordinates import SkyCoord, EarthLocation, AltAz
from astropy.time import Time

from models.base import BaseModel
from models.dsh import Feed
from models.fil import FilterBank
from models.scan import ScanModel, ScanState
from util import log

logger = logging.getLogger(__name__)

USABLE_BANDWIDTH = 0.65                         # Percentage of usable bandwidth for a scan
MAX_SCAN_DURATION_SEC = 60                      # Maximum duration of a single scan in seconds
AUTO_GAIN_TOKEN_RE = re.compile(r"^AUTO\d*$")   # Regular expression to match AUTO gain tokens (e.g. "AUTO", "AUTO1", "AUTO2", etc.)

#=======================================
# Models comprising a Target (TARGET)
#=======================================

class PointingType(enum.IntEnum):
    """Python enumerated type for pointing types."""

    SIDEREAL_TRACK = 0        # Sidereal tracking (fixed RA/Dec) e.g. Andromeda Galaxy
    NON_SIDEREAL_TRACK = 1    # Solar system or satellite tracking e.g. planet, moon or Sun
    DRIFT_SCAN = 2            # Fixed Alt-azimuth target e.g. Zenith
    OFFSET_SCAN = 3           # Offset scan over the target
    FIVE_POINT_SCAN = 4       # Center point and 4 offset points e.g. for beam mapping

class OffsetScan(BaseModel):
    """ A class representing the parameters for an offset scan over a target.
        Angle is the position angle of the scan in the tangent plane 
            0°  - North-South scan
            90° - West-East scan
            180° - South-North scan
            270° - East-West scan

        Example: To scan 5 degrees over the Sun in 60 seconds.
            offset = -2.5 (start 2.5 degrees before the Sun)
            rate = 0.0833 (5 degrees in 60 seconds)
            angle = 90.0 (West-East scan)

    """

    schema = Schema({
        "_type": And(str, lambda v: v == "OffsetScan"),
        "offset": And(float, lambda v: isinstance(v, float)),                               # Offset in degrees for offset scans or five-point scans (e.g. 0.1 degree)
        "rate": And(float, lambda v: isinstance(v, float)),                                 # Slew rate in degrees per second for offset scans or five-point scans (e.g. 0.5 degree/sec)
        "angle": And(float, lambda v: isinstance(v, float)),                                # Angle in degrees for offset scans or five-point scans (e.g. 0.0 degree)
        "start": Or(None, And(datetime, lambda v: v is None or isinstance(v, datetime))),   # Scan start date and time
    })

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "OffsetScan",
            "offset": 1.0,                          # Offset in degrees for offset scans or five-point scans (e.g. 0.1 degree)
            "rate": 1.0,                            # Slew rate in degrees per second for offset scans or five-point scans (e.g. 0.5 degree/sec)
            "angle": 0.0,                           # Angle in degrees for offset scans or five-point scans (e.g. 0.0 degree)
            "start": None                           # Scan start date and time
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class FivePointScan(BaseModel):
    """ A class representing the parameters for a five point scan over a target.
        Five points: Centre, N, S, E, W
        Scan duration seconds will be spent on each point.
        Offset is the distance in degrees from the center to the N, S, E and W points.
        Offset is usually HPBW (deg) = 70 * (wavelength (m) / dish diameter (m)) for beam mapping.
        Compute offsets using directional_offset_by() to maintain accurate great-circle geometry
    """

    schema = Schema({
        "_type": And(str, lambda v: v == "FivePointScan"),
        "offset": And(float, lambda v: isinstance(v, float)), # Offset in degrees for five-point scans (e.g. 0.1 degree)
        "direction": Or(None, And(str, lambda v: isinstance(v, str) and v in ["C", "N", "S", "E", "W"])),  # Direction C, N, S, E, W for the five points
    })

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "FivePointScan",
            "offset": 10.0,                          # Offset in degrees for five-point scans (e.g. 0.1 degree)
            "direction": None                        # Direction C, N, S, E, W for the five points (default to Center point "C")
         }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class TargetModel(BaseModel):
    """A class representing a target model."""

    schema = Schema({
        "_type": And(str, lambda v: v == "TargetModel"),
        "obs_id": Or(None, And(str, lambda v: isinstance(v, str))),                      # Observation identifier (see Observation model)
        "tgt_idx": And(int, lambda v: v >= -1),                                          # Target list index (-1 = not set, 0-based)

        "id": Or(None, And(str, lambda v: isinstance(v, str))),                          # Target identifier e.g. "Sun", "Moon", "M82", "Vega"
        "pointing": And(PointingType, lambda v: isinstance(v, PointingType)),            # Pointing type
        "sky_coord": Or(None, lambda v: v is None or isinstance(v, SkyCoord)),           # Sky coordinates (any frame)
        "altaz": Or(None, dict, lambda v: v is None or isinstance(v, (dict, SkyCoord, AltAz))), # Alt-az coordinates (SkyCoord or AltAz)
        "scan": Or(None, OffsetScan, FivePointScan, lambda v: v is None or isinstance(v,(OffsetScan, FivePointScan)))    # Offset or five-point scan parameters (e.g. offsets)
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "TargetModel",
            "obs_id": None,                         # Observation identifier (see Observation model)
            "tgt_idx": -1,                          # Target list index (-1 = not set, 0-based)
            
            "id": None,                             # Used for solar and lunar (and optionally sidereal) targets e.g. "Sun", "Moon", "Mars", "Vega"
            "pointing": PointingType.DRIFT_SCAN,    # Default to drift scan pointing
            "sky_coord": None,                      # Used for sidereal targets (ra,dec or l,b)
            "altaz": None,                          # Used for non-sidereal targets e.g. solar, terrestrial or satellite targets
            "scan": None                            # Offset or five-point scan parameters (e.g. offset positions and durations)
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        altaz = kwargs.get("altaz")
        if isinstance(altaz, dict):
            normalised_altaz = dict(altaz)
            for axis in ("alt", "az"):
                value = normalised_altaz.get(axis)
                if isinstance(value, str):
                    try:
                        normalised_altaz[axis] = float(value)
                    except ValueError:
                        pass
            kwargs["altaz"] = normalised_altaz

        super().__init__(**kwargs)

    def __str__(self):

        if self.id is not None:
            return f"Id={self.id}"
        elif self.sky_coord is not None:
            return f"{self.pointing.name}(ra={self.sky_coord.ra}, dec={self.sky_coord.dec})"
        elif self.altaz is not None:
            return f"{self.pointing.name}(alt={self.altaz.get('alt')}, az={self.altaz.get('az')})"
        else:
            return f"Undefined Target with pointing type {self.pointing}"

    def start_scan(self):
        """Start the scan for this target by setting the scan start time to now if not already set."""
        if self.pointing == PointingType.OFFSET_SCAN and isinstance(self.scan, OffsetScan) and self.scan.start is None:
            self.scan.start = datetime.now(timezone.utc)

class TargetConfig(BaseModel):
    """A class representing a target configuration."""

    schema = Schema({
        "_type": And(str, lambda v: v == "TargetConfig"),
        "obs_id": Or(None, And(str, lambda v: isinstance(v, str))),             # Observation identifier (see Observation model)
        "tgt_idx": And(int, lambda v: v >= -1),                                 # Target list index (-1 = not set, 0-based)
        "feed_type": And(Feed, lambda v: isinstance(v, Feed)),                  # Feed enum
        "gain": Or(And(str, lambda v: TargetConfig.is_auto_gain_token(v)),      # Gain (dBi), AUTO, or AUTO<n>
            And(Or(int, float), lambda v: v >= 0.0)),                           # AUTO always run set_auto_gain for this target config. No caching.
                                                                                # AUTO<n> named cached auto-gain groups, first encountered group will be used for caching and subsequent AUTO<n> 
        "center_freq": And(Or(int, float), lambda v: v >= 0.0),                 # Center frequency (Hz) 
        "bandwidth": And(Or(int, float), lambda v: v >= 0.0),                   # Bandwidth (Hz) 
        "sample_rate": And(Or(int, float), lambda v: v >= 0.0),                 # Sample rate (Hz) 
        "integration_time": And(Or(int, float), lambda v: v >= 0.0),            # Integration time (seconds) on a target (e.g. 300 seconds)
        "spectral_resolution": And(int, lambda v: v >= 0),                      # Spectral resolution (fft size)
        "filter_bank": Or(None, FilterBank, lambda v: v is None or isinstance(v, FilterBank)), # Filterbank parameters for this target  
      })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        if "feed" in kwargs and "feed_type" not in kwargs:
            kwargs["feed_type"] = kwargs["feed"]

        # Default values
        defaults = {
            "_type": "TargetConfig",
            "obs_id": None,                 # Observation identifier (see Observation model)
            "tgt_idx": -1,                  # Target list index (-1 = not set, 0-based)

            "feed_type": Feed.NONE,         # Default to None feed
            "gain": 0.0,                    # Gain (dBi)
            "center_freq": 0.0,             # Center frequency (Hz) 
            "bandwidth": 0.0,               # Bandwidth (Hz) 
            "sample_rate": 0.0,             # Sample rate (Hz) 
            "integration_time": 0.0,        # Integration time (seconds)
            "spectral_resolution": 0,       # Spectral resolution (fft size)
            "filter_bank": None,            # Filterbank parameters for this target
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        gain = kwargs.get("gain")
        kwargs["gain"] = gain.upper() if self.is_auto_gain_token(gain) else gain

        if isinstance(kwargs.get("filter_bank"), dict):
            kwargs["filter_bank"] = FilterBank(**kwargs["filter_bank"])

        super().__init__(**kwargs)

    @classmethod
    def is_auto_gain_token(cls, value) -> bool:
        return isinstance(value, str) and AUTO_GAIN_TOKEN_RE.fullmatch(value.upper()) is not None

class TargetScanSet(BaseModel):
    """A class representing a set of scans for a particular target."""

    schema = Schema({
        "_type": And(str, lambda v: v == "TargetScanSet"),
        "obs_id": Or(None, And(str, lambda v: isinstance(v, str))),                  # Observation identifier (see Observation model)
        "tgt_idx": And(int, lambda v: v >= -1),                                      # Target list index (-1 = not set, 0-based)

        # Below parameters are calculated based on the above parameters
        "freq_min": And(Or(None, float, int), lambda v: v is None or v >= 0.0),      # Start of frequency scanning (Hz)
        "freq_max": And(Or(None, float, int), lambda v: v is None or v >= 0.0),      # End of frequency scanning (Hz)
        "freq_scans": And(Or(None, int), lambda v: v is None or v >= 0),             # Number of frequency scans
        "freq_overlap": And(Or(None, float, int), lambda v: v is None or v >= 0.0),  # Overlap between frequency scans (Hz)
        "scan_iterations": And(Or(None, int), lambda v: v is None or v >= 0),        # Number of scan iterations (within a frequency scan)
        "scan_duration": And(Or(None, float, int), lambda v: v is None or v >= 0.0), # Duration of each scan (seconds)

        "scans": And(list, lambda v: isinstance(v, list)),                           # List of scans to be performed for this target
      })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "TargetScanSet",
            "obs_id": None,                 # Observation identifier (see Observation model)
            "tgt_idx": -1,                  # Target list index (-1 = not set, 0-based)

            "freq_min": None,               # Start of frequency scanning (Hz)
            "freq_max": None,               # End of frequency scanning (Hz)
            "freq_scans": 0,                # Number of frequency scans
            "freq_overlap": None,           # Overlap between frequency scans (Hz)
            "scan_iterations": 0,           # Number of scan iterations (within a frequency scan)
            "scan_duration": None,          # Duration of each scan (seconds)
            "scans": [],                    # List of scans for this target
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def get_scan_by_index(self, freq_scan: int, scan_iter: int) -> ScanModel:
        """Retrieve a scan by its frequency scan and scan iteration indices."""

        if freq_scan is None or freq_scan < 0 or freq_scan >= self.freq_scans:
            return None

        if scan_iter is None or scan_iter < 0 or scan_iter >= self.scan_iterations:
            return None

        # Total scans = freq_scans * scan_iterations
        idx = freq_scan * self.scan_iterations + scan_iter
        if idx < 0 or idx >= len(self.scans):
            return None

        return self.scans[idx] 

    def get_scan_by_id(self, scan_id: str) -> ScanModel:
        """Retrieve a scan by its unique scan_id."""
        # Scan_id should be in the form: <obs_id>-<target_index>-<freq_scan>-<scan_iter>
        if scan_id is None or not isinstance(scan_id, str):
            return None

        # Split the scan_id to extract target, freq_scan and scan_iter indices
        try:
            tgt_indx  = int(scan_id.split("-")[-3])
            freq_scan = int(scan_id.split("-")[-2])
            scan_iter = int(scan_id.split("-")[-1])

            if tgt_indx != self.tgt_idx:
                return None # Target index does not match

            return self.get_scan_by_index(freq_scan, scan_iter) 

        except Exception as e:
            # Use brute force method if parsing scan_id fails
            for scan in self.scans:
                if scan.scan_id == scan_id:
                    return scan
        return None

    def determine_scans(self, obs_id: str, tgt_config: TargetConfig):
        """
        Calculate the number of frequency scans needed to cover the target frequency range from start_freq to end_freq
        and the overlap in the frequency domain. NOTE: The overlap is different to the non-usable bandwidth !!
        
        Calculate the number of scan iterations and scan duration to keep the scan duration within MAX_SCAN_DURATION_SEC
        i.e. manageable from a performance perspective.

        E.g. We may need 10 scans of 1 minute each to cover the frequency range from start_freq to end_freq,
        where each scan is iterated 5 times to cover the duration of 5 minutes per frequency scan.

        :param tgt_config: TargetConfig object
        """
        if tgt_config is None or tgt_config.tgt_idx == -1:
            logger.error(f"Target cannot determine scans for obs_id={obs_id}: TargetConfig is not valid {tgt_config}")
            return

        self.obs_id = obs_id if obs_id is not None else '<undefined>'
        self.tgt_idx = tgt_config.tgt_idx

        self.freq_min = tgt_config.center_freq - tgt_config.bandwidth / 2  # Requested start frequency
        self.freq_max = tgt_config.center_freq + tgt_config.bandwidth / 2  # Requested end frequency

        logger.info(f"Target in {obs_id} determining scans for TargetConfig idx={self.tgt_idx} from {self.freq_min/1e6:.2f} MHz to {self.freq_max/1e6:.2f} MHz with Sample Rate: {tgt_config.sample_rate/1e6:.2f} MHz and Duration: {tgt_config.integration_time} sec(s)")

        # Calculate the number of frequency scans needed to cover the requested
        # usable bandwidth. A target narrower than one usable sample-rate window
        # still needs one full sample-rate capture centered on the target.
        freq_span = self.freq_max - self.freq_min
        usable_sample_rate = tgt_config.sample_rate * USABLE_BANDWIDTH
        self.freq_scans = max(1, int(math.ceil(freq_span / usable_sample_rate))) if usable_sample_rate > 0 else 0
        usable_step = (freq_span - usable_sample_rate) / (self.freq_scans - 1) if self.freq_scans > 1 else 0
        self.freq_overlap = round(tgt_config.sample_rate - usable_step if self.freq_scans > 1 else 0, 4) # Overlap in the frequency domain (Hz) rounded to 4 decimals
        self.scan_iterations = int(np.ceil(tgt_config.integration_time / MAX_SCAN_DURATION_SEC))  # Number of iterations of a frequency scan, # e.g. 5 minutes of data will be 5 scans of 1 minute each
        self.scan_duration = math.ceil(tgt_config.integration_time / self.scan_iterations) if self.scan_iterations > 1 else tgt_config.integration_time  # Duration of each scan in seconds

        logger.info(f"Target in {obs_id} Frequency-Iteration Scans: {self.freq_scans}-{self.scan_iterations} each of Scan Duration: {self.scan_duration} sec(s)")
        logger.info(f"Target in {obs_id} Sample Rate: {tgt_config.sample_rate} Hz, Overlap: {self.freq_overlap:.2f} Hz")
        
        # Initialise the scans list
        self.scans = []
  
        for i in range(self.freq_scans * self.scan_iterations):
            freq_scan = i // self.scan_iterations               # Current frequency scan number
            scan_iter = i % self.scan_iterations                # Current iteration within the frequency scan

            # Calculate the start, end and center frequencies for each scan
            if self.freq_scans == 1:
                scan_center_freq = tgt_config.center_freq
                scan_start_freq = scan_center_freq - tgt_config.sample_rate / 2
                scan_end_freq = scan_center_freq + tgt_config.sample_rate / 2
            else:
                scan_start_freq = (
                    self.freq_min
                    - tgt_config.sample_rate * (1 - USABLE_BANDWIDTH) / 2
                    + (freq_scan * (tgt_config.sample_rate - self.freq_overlap))
                )
                scan_end_freq = scan_start_freq + tgt_config.sample_rate
                scan_center_freq = scan_start_freq + tgt_config.sample_rate / 2

            scan = ScanModel(
                obs_id=obs_id if obs_id is not None else '<undefined>',
                tgt_idx=self.tgt_idx,
                freq_scan=freq_scan,
                scan_iter=scan_iter,
                dig_id=None,
                duration=self.scan_duration,
                sample_rate=tgt_config.sample_rate,
                spectral_resolution=tgt_config.spectral_resolution,
                filter_bank=tgt_config.filter_bank,
                start_freq=scan_start_freq,
                center_freq=scan_center_freq,
                end_freq=scan_end_freq,
                gain=0.0 if TargetConfig.is_auto_gain_token(tgt_config.gain) else tgt_config.gain,
                status=ScanState.EMPTY,
                last_update=datetime.now(timezone.utc)
            )
            self.scans.append(scan)  # Append the scan to the scans list

if __name__ == "__main__":

    import pprint
    from util.format import fmt_title
    
    fmt_title("Offset Scan Test")
    
    offsetscan001 = OffsetScan(
        offset=0.1,
        rate=0.5,
        start=datetime.now(timezone.utc)
    )
    
    fmt_title("OffsetScan Test")
    pprint.pprint(offsetscan001.to_dict())

    coord = SkyCoord(ra="18h36m56.33635s", dec="+38d47m01.2802s", frame="icrs")
    altaz = {"alt": 45.0*u.deg, "az": 180.0*u.deg}

    target001 = TargetModel(
        id="Vega",
        pointing=PointingType.SIDEREAL_TRACK,
        sky_coord=coord,
        altaz=None
    )

    fmt_title("Target Model: Sidereal Target")
    pprint.pprint(target001.to_dict())

    target002 = TargetModel(
        id="Ground Station Alpha",
        pointing=PointingType.DRIFT_SCAN,
        sky_coord=None,
        altaz=altaz
    )
    fmt_title("Target Model: Terrestrial Target")
    pprint.pprint(target002.to_dict())

    fmt_title("Target Model: Solar Target (Moon)")
    dt = datetime.now(timezone.utc)
    location = EarthLocation(lat=45.67*u.deg, lon=-111.05*u.deg, height=1500*u.m)

    moon_icrs = get_body('moon', Time(dt), location)
    altaz_frame = AltAz(obstime=Time(dt), location=location)
    altaz = moon_icrs.transform_to(altaz_frame)

    print("Computed AltAz for Moon at", dt.isoformat())

    target003 = TargetModel(
        id="Moon",
        pointing=PointingType.NON_SIDEREAL_TRACK,
        sky_coord=None,
        altaz={"alt": altaz.alt, "az": altaz.az}
        )

    fmt_title("Target Model: Solar Target (Moon)")
    pprint.pprint(target003.to_dict())

    fmt_title("Target Model: Solar Target (Moon)")
    print('Tests from_dict method')
    fmt_title("")

    target004 = TargetModel()
    target004 = target004.from_dict(target003.to_dict())

    pprint.pprint(target004.to_dict())

    fmt_title("Target Config: Vega Target")
    target_config001 = TargetConfig(
        obs_id="obs001",
        tgt_idx=1,
        feed_type=Feed.H3T_1420,
        gain=12.0,
        center_freq=1.42e9,
        bandwidth=2e6,
        sample_rate=2.0e6,
        integration_time=300,
        spectral_resolution=1024,
        filter_bank={
            "_type": "FilterBank",
            "enabled": True,                      # Whether filterbank file creation is enabled
            "temporal_resolution": 10.0,            # Time resolution (milliseconds) for summing power spectra (e.g. 1 millisecond)
            "dtype": "uint8",                     # Data type for filterbank output (e.g. uint16)
         }
    )
    pprint.pprint(target_config001.to_dict())

    targetscanset = TargetScanSet(
        obs_id="obs001",
        tgt_idx=1
    )

    targetscanset.determine_scans(obs_id="obs001", tgt_config=target_config001)
    print(f"Determined Scans: freq_scans={targetscanset.freq_scans}, freq_overlap={targetscanset.freq_overlap} Hz, scan_iterations={targetscanset.scan_iterations}, scan_duration={targetscanset.scan_duration} sec(s)")
    pprint.pprint(targetscanset.to_dict())
