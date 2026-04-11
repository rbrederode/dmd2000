import enum
import os
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from models.scan import ScanModel, ScanState
from models.target import TargetModel, TargetConfig, TargetScanSet, PointingType, MAX_SCAN_DURATION_SEC
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure
from util.fits_utils import observation_to_fits_hdulist

# A scheduling block is the minimum time allocation of resources to an observation
# For example, if an observation requires 90 minutes, and the scheduling block size is 60 minutes,
# then the observation will be allocated 2 scheduling blocks (120 minutes) to ensure sufficient time
# for the observation. Dishes (and other resources) will be booked in increments of the scheduling block size.
SCHEDULING_BLOCK_SIZE = 60  # in minutes

#=======================================
# Models comprising an Observation (OBS)
#=======================================

class ObsState(enum.IntEnum):
    """Python enumerated type for observing state."""

    EMPTY = 0
    """The observation is not resourced."""

    IDLE = 2
    """The observation is sitting idle, resources are allocated or deallocated in this state.
    """

    CONFIGURING = 3
    """
    The resources allocated to an observation are being configured.

    This is a transient state; the observation will automatically
    transition to READY when configuring completes normally.
    """

    READY = 4
    """
    The observation is fully prepared to scan, but is not scanning.
    It may be tracking but is not taking data.
    """

    SCANNING = 5
    """
    The observation is scanning.

    It is taking data and, if needed, all subsystems are synchronously
    moving in the observed coordinate system.

    Any changes to the subsystems are happening automatically (this
    allows for a scan to cover the case where the phase centre is moved
    in a pre-defined pattern).
    """

    ABORTED = 6
    """The observation is in an aborted state. It will need to be reset."""

    FAULT = 9
    """The observation has encountered an error."""

class ObsTransition (enum.IntEnum):
    """Python enumerated type for observation workflow transitions."""

    START = 1
    ASSIGN_RESOURCES = 2
    RELEASE_RESOURCES = 3
    CONFIGURE_RESOURCES = 4
    CONFIGURE_ABORTED = 5
    READY = 6
    SCAN_STARTED = 7
    SCAN_COMPLETED = 8
    SCAN_ENDED = 9
    ABORT = 10
    FAULT_OCCURRED = 11
    RESET = 12

class ObsModel(BaseModel):
    """A class representing a model of an observation"""

    schema = Schema({
        "_type": And(str, lambda v: v == "ObsModel"),                           # Type identifier for the model
        "obs_id": And(Or(None, str), lambda v: v is None or isinstance(v, str)),# Unique identifier
        "title": And(str, lambda v: isinstance(v, str)),                        # Short description (255 chars) 
        "description": And(str, lambda v: isinstance(v, str)),                  # Description (no strict upper limit)
        "obs_state": And(ObsState, lambda v: isinstance(v, ObsState)),

        "targets": And(list, lambda v: isinstance(v, list)),                    # List of targets (TargetModel)
        "target_configs": And(list, lambda v: isinstance(v, list)),             # List of target configurations (TargetConfig)
        "target_scans": And(list, lambda v: isinstance(v, list)),               # List of target scan sets (TargetScanSet)

        "tgt_idx": And(int, lambda v: isinstance(v, int)),                      # Index of the next target to be observed (0-based)
        "tgt_scan": And(int, lambda v: isinstance(v, int)),                     # Index of the next scan (for the given tgt_idx) to be observed (0-based)

        "dsh_id": And(Or(None, str), lambda v: v is None or isinstance(v, str)),# Dish identifier e.g. "dish001"
        "capabilities": And(str, lambda v: isinstance(v, str)),                 # Dish capabilities e.g. "Drift Scan over Zenith"
        "diameter": And(Or(int, float), lambda v: v >= 0.0),                    # Dish diameter (meters)
        "f/d_ratio": And(Or(int, float), lambda v: v >= 0.0),                   # Dish focal length to diameter ratio
        "latitude": And(Or(int, float), lambda v: -90.0 <= v <= 90.0),          # Dish latitude (degrees)
        "longitude": And(Or(int, float), lambda v: -180.0 <= v <= 180.0),       # Dish longitude (degrees)

        "total_integration_time": And(Or(int, float), lambda v: v >= 0.0),          # Total integration time (seconds)
        "estimated_slewing_time": And(Or(int, float), lambda v: v >= 0.0),          # Estimated slewing time (seconds)
        "estimated_observation_duration": And(str, lambda v: isinstance(v, str)),   # Estimated observation duration (HH:MM:SS)
        "scheduling_block_start": And(Or(None, datetime), lambda v: v is None or isinstance(v, datetime)), # Scheduling block start datetime (UTC)
        "scheduling_block_end": And(Or(None, datetime), lambda v: v is None or isinstance(v, datetime)),   # Scheduling block end datetime (UTC)

        "created": And(Or(None, datetime), lambda v: v is None or isinstance(v, datetime)),  # Creation datetime (UTC)
        "user_email": And(str, lambda v: isinstance(v, str)),                   # User email that created the observation
        "timeout_ms_scan": And(int, lambda v: v > 0),                           # Scan timeout in milliseconds
        "timeout_ms_config": And(int, lambda v: v > 0),                         # Configuration timeout in milliseconds

        "start_dt": And(datetime, lambda v: isinstance(v, datetime)),           # Start datetime (UTC) of the observation 
        "end_dt": And(datetime, lambda v: isinstance(v, datetime)),             # End datetime (UTC) of the observation

        # Calibration files to be used by this observation
        "tsys_calibrators": And(list, lambda v: isinstance(v, list)),           # List of *tsys.csv (system temperature calibration) filenames
        "gain_calibrators": And(list, lambda v: isinstance(v, list)),           # List of *gain.csv (gain calibration) filenames
        "load_calibrators": And(list, lambda v: isinstance(v, list)),           # List of *load.csv (terminated signal chain) filenames

        # Summed power scan files generated during this observation
        "spr_scans": And(list, lambda v: isinstance(v, list)),                  # List of *spr.csv (summed power) filenames

        "last_update": And(datetime, lambda v: isinstance(v, datetime)),        # Last update datetime (UTC) of the observation
    })

    allowed_transitions = {
        "obs_state": {
            ObsState.EMPTY: {ObsState.EMPTY, ObsState.IDLE, ObsState.FAULT, ObsState.ABORTED},
            ObsState.IDLE: {ObsState.IDLE,ObsState.CONFIGURING, ObsState.FAULT, ObsState.ABORTED},
            ObsState.CONFIGURING: {ObsState.CONFIGURING,ObsState.READY, ObsState.FAULT, ObsState.ABORTED},
            ObsState.READY: {ObsState.CONFIGURING, ObsState.READY, ObsState.SCANNING, ObsState.IDLE, ObsState.FAULT, ObsState.ABORTED},
            ObsState.SCANNING: {ObsState.READY, ObsState.FAULT, ObsState.ABORTED},
            ObsState.ABORTED: {ObsState.ABORTED, ObsState.IDLE},
        }
    }

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "ObsModel",
            "obs_id": None,
            "title": "",
            "description": "",
            "obs_state": ObsState.EMPTY,
            "targets": [],
            "target_configs": [],
            "target_scans": [],
            "tgt_idx": 0,
            "tgt_scan": 0,
            "dsh_id": None,
            "capabilities": "",
            "diameter": 0.0,
            "f/d_ratio": 0.0,
            "latitude": 0.0,
            "longitude": 0.0,
            "total_integration_time": 0.0,
            "estimated_slewing_time": 0.0,
            "estimated_observation_duration": "00:00:00",
            "scheduling_block_start": None,
            "scheduling_block_end": None,
            "created": None,
            "user_email": "",
            "timeout_ms_scan": MAX_SCAN_DURATION_SEC*2*1000,  # Scan timeout in milliseconds
            "timeout_ms_config": 60000,                      # Configuration timeout in milliseconds (includes slew time)

            "start_dt": datetime.now(timezone.utc),
            "end_dt": datetime.now(timezone.utc),
            "tsys_calibrators": [],
            "gain_calibrators": [],
            "load_calibrators": [],
            "spr_scans": [],
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def get_target_by_index(self, tgt_idx:int) -> TargetModel:
        """Retrieve a target by its index from the targets list."""

        if tgt_idx is None or not isinstance(tgt_idx, int):
            return None

        return self.targets[tgt_idx] if 0 <= tgt_idx < len(self.targets) else None

    def get_target_config_by_index(self, tgt_idx:int) -> TargetConfig:
        """Retrieve a target configuration by its index from the target configurations list."""

        if tgt_idx is None or not isinstance(tgt_idx, int):
            return None

        return self.target_configs[tgt_idx] if 0 <= tgt_idx < len(self.target_configs) else None

    def get_target_scan_set_by_index(self, tgt_idx:int) -> TargetScanSet:
        """Retrieve a target scan set by its index from the target scans list."""

        if tgt_idx is None or not isinstance(tgt_idx, int):
            return None

        return self.target_scans[tgt_idx] if 0 <= tgt_idx < len(self.target_scans) else None

    def get_target_scan_by_index(self, tgt_idx:int, freq_scan:int, scan_iter:int) -> ScanModel:
        """Retrieve a target scan by its indices from the target scans list."""

        if tgt_idx is None or freq_scan is None or scan_iter is None:
            return None

        target_scans = self.target_scans[tgt_idx] if 0 <= tgt_idx < len(self.target_scans) else None
        return target_scans.get_scan_by_index(freq_scan, scan_iter) if target_scans else None

    def get_target_scan_by_id(self, scan_id) -> ScanModel:
        """Retrieve a target scan by its identifier from the target scans list."""
        # Scan_id should be in the form: <obs_id>-<target_index>-<freq_scan>-<scan_iter>
        if scan_id is None or not isinstance(scan_id, str):
            return None

        # Split the scan_id to extract target, freq_scan and scan_iter indices
        try:
            tgt_idx = int(scan_id.split("-")[-3])
            freq_scan = int(scan_id.split("-")[-2])
            scan_iter = int(scan_id.split("-")[-1])

            return self.get_target_scan_by_index(tgt_idx, freq_scan, scan_iter)
        except Exception as e:
            # Use brute force method if parsing scan_id fails
            for tgt_idx in range(len(self.target_scans)):
                target_scans = self.target_scans[tgt_idx]
                for scan in target_scans.scans:
                    if scan.scan_id == scan_id:
                        return scan  
        return None

    def determine_scans(self):
        """Determine the set of scans for each target configuration in the observation."""

        # Iterate through each target, expand Five Point Scan targets into five seperate targets each with a pointing direction in C, N,S, E, W
        # Duplicate target configurations for each of the five pointings, update the target index in the target and configuration to the expanded index
        expanded_targets = []
        expanded_target_configs = []
        for tgt_idx, target in enumerate(self.targets):
            if target.pointing == PointingType.FIVE_POINT_SCAN and target.scan and target.scan.direction is None:
                # Create the five pointings for the Five Point Scan target
                for direction in ["C", "N", "S", "E", "W"]:

                    # Copy the target model and update the Five Point Scan direction 
                    tgt_scan_copy = target.scan.copy()
                    tgt_scan_copy.direction = direction

                    target_copy = target.copy()
                    target_copy.scan = tgt_scan_copy
                    target_copy.tgt_idx = len(expanded_targets)

                    expanded_targets.append(target_copy)

                    # Copy the target configuration and update the target index
                    target_config_copy = self.target_configs[tgt_idx].copy()
                    target_config_copy.tgt_idx = target_copy.tgt_idx
                    
                    expanded_target_configs.append(target_config_copy)
            else:
                target.tgt_idx = len(expanded_targets)
                target_config_copy = self.target_configs[tgt_idx].copy()
                target_config_copy.tgt_idx = target.tgt_idx

                expanded_targets.append(target)
                expanded_target_configs.append(target_config_copy)

        self.targets = expanded_targets
        self.target_configs = expanded_target_configs

        # Initialise the target scans list
        self.target_scans = []

        # Iterate through each target configuration
        for tgt_idx, tgt_config in enumerate(self.target_configs):

            # Determine the set of scans for the given target configuration
            target_scans = TargetScanSet(tgt_idx=tgt_idx)
            target_scans.determine_scans(obs_id=self.obs_id, tgt_config=tgt_config)
            self.target_scans.append(target_scans)

    def get_current_tgt_scan_set(self) -> TargetScanSet:
        """Get the current target scan set to be observed based on the current tgt_idx."""

        target_scan_set = self.target_scans[self.tgt_idx] if 0 <= self.tgt_idx < len(self.target_scans) else None
        if target_scan_set is None or target_scan_set.scans is None or len(target_scan_set.scans) == 0:
            return None

        return target_scan_set

    def get_current_tgt_scan(self) -> ScanModel:
        """Get the current target scan to be observed based on the current tgt_idx and tgt_scan."""

        target_scan_set = self.target_scans[self.tgt_idx] if 0 <= self.tgt_idx < len(self.target_scans) else None
        if target_scan_set is None or target_scan_set.scans is None or len(target_scan_set.scans) == 0:
            return None

        scan_iterations = target_scan_set.scan_iterations
        if scan_iterations <= 0:
            return None

        return self.get_target_scan_by_index(self.tgt_idx, self.tgt_scan // scan_iterations, self.tgt_scan % scan_iterations)
 
    def set_next_tgt_scan(self):
        """Set the next target and scan index to the next EMPTY OR WIP scan (i.e. open scan) in the observation's set of scans."""

        # Iterate through targets starting from the current target index
        for tgt_idx in range(self.tgt_idx, len(self.targets)):

            # Get the target scans for the given target index
            target_scan_set = self.target_scans[tgt_idx] if 0 <= tgt_idx < len(self.target_scans) else None
            if target_scan_set is None:
                continue
            
            for idx, scan in enumerate(target_scan_set.scans):
                if scan.status == ScanState.EMPTY or scan.status == ScanState.WIP:
                    self.tgt_idx = tgt_idx
                    self.tgt_scan = idx
                    print(f"Observation.set_next_tgt_scan: set tgt_idx to {self.tgt_idx}, set tgt_scan to {self.tgt_scan}")
                    return

        # If no EMPTY scan found, set to the end of the targets
        self.tgt_idx = len(self.targets)
        self.tgt_scan = 0
        print(f"Observation.set_next_tgt_scan: set tgt_idx to {self.tgt_idx}, set tgt_scan to {self.tgt_scan}")

    def save_to_disk(self, output_dir) -> bool:
        """
        Flush the observation to a file on disk.
            :param output_dir: Directory where the file will be saved
            :returns: True if the data was saved successfully, False otherwise
        """
        filename = f"{self.obs_id.replace(':', '')}-obs.json"

        try:
            super().save_to_disk(output_dir, filename)
            return True
        except XAPIValidationFailed as e:
            raise XSoftwareFailure(f"Observation {self.obs_id} failed to save to disk due to validation error: {e}")
        except Exception as e:
            raise XSoftwareFailure(f"Observation {self.obs_id} failed to save to disk due to unexpected error: {e}")

    def save_fits_to_disk(self, output_dir) -> bool:
        """
        Flush the observation to a FITS file on disk.
            :param output_dir: Directory where the file will be saved
            :returns: True if the data was saved successfully, False otherwise
        """
        filename = f"{self.obs_id.replace(':', '')}-obs.fits"

        try:
            # Convert the observation to a FITS file and save to disk
            from util.fits_utils import observation_to_fits_hdulist
            hdulist = observation_to_fits_hdulist(self)
            hdulist.writeto(os.path.join(output_dir, filename), overwrite=True)
            return True
        except Exception as e:
            raise XSoftwareFailure(f"Observation {self.obs_id} failed to save FITS to disk due to unexpected error: {e}")
    
    def __str__(self):
        return f"Observation(obs_id={self.obs_id}, obs_state={self.obs_state.name})"

                
if __name__ == "__main__":

    from astropy.coordinates import SkyCoord
    import astropy.units as u
    import pprint

    obs_dict = {'_type': 'ObsModel', 'dsh_id': 'Dish002', 'capabilities': 'Drift Scan over Zenith', 'diameter': 3, 'f/d_ratio': 1.3, 'latitude': 53.2421, 'longitude': -2.3067, 'total_integration_time': 60, 'estimated_slewing_time': 30, 'estimated_observation_duration': '00:01:30', 'scheduling_block_start': {'_type': 'datetime', 'value': '2025-12-07T19:00:00.000Z'}, 'scheduling_block_end': {'_type': 'datetime', 'value': '2025-12-07T20:00:00.000Z'}, 'obs_id': '2025-12-07T19:00Z-Dish002', 'obs_state': {'_type': 'enum.IntEnum', 'instance': 'ObsState', 'value': 'EMPTY'}, 'targets': [{'_type': 'TargetModel', 'sky_coord': {'_type': 'SkyCoord', 'frame': 'icrs', 'ra': 204.2538, 'dec': -29.8658}, 'id': 'M83', 'type': {'_type': 'enum.IntEnum', 'instance': 'PointingType', 'value': 'SIDEREAL_TRACK'}}],'target_configs': [{'_type': 'TargetConfig', 'tgt_idx': 0, 'feed': {'_type': 'enum.IntEnum', 'instance': 'Feed', 'value': 'H3T_1420'}, 'gain': 12, 'center_freq': 1420400000, 'bandwidth': 1000000, 'sample_rate': 2400000, 'integration_time': 60, 'spectral_resolution': 128, 'target_id': 1}], 'user_email': 'ray.brederode@skao.int', 'created': {'_type': 'datetime', 'value': '2025-12-07T18:19:37.503Z'}}
    obs000 = ObsModel().from_dict(obs_dict)
    print("="*40)
    print("Observation Model from Dict Test")
    print("="*40)
    pprint.pprint(obs000.to_dict())

    print("="*40)
    print("ObsState valid transition test")
    print("="*40)

    obsx = ObsModel()
    obsx.obs_state = ObsState.EMPTY
    print(f"Initial obs_state: {obsx.obs_state.name}")
    try:
        obsx.obs_state = ObsState.IDLE
        print(f"Updated obs_state: {obsx.obs_state.name}")
        obsx.obs_state = ObsState.CONFIGURING
        print(f"Updated obs_state: {obsx.obs_state.name}")
        obsx.obs_state = ObsState.READY
        print(f"Updated obs_state: {obsx.obs_state.name}")
        obsx.obs_state = ObsState.SCANNING
        print(f"Updated obs_state: {obsx.obs_state.name}")
    except XInvalidTransition as e:
        print(f"Caught expected exception on invalid transition: {e}")

    print("="*40)
    print("ObsState invalid transition test")
    print("="*40)
    
    print(f"Current obs_state: {obsx.obs_state.name}")
    try:
        obsx.obs_state = ObsState.IDLE  # Invalid transition from SCANNING to IDLE
        print(f"Updated obs_state: {obsx.obs_state.name}")
    except XInvalidTransition as e:
        print(f"Caught expected exception on invalid transition: {e}")

    obs002 = ObsModel()
    print("="*40)
    print("Observation Model with Defaults Test")
    print("="*40)
    pprint.pprint(obs002.to_dict())

    obs001 = ObsModel().from_dict(obs_dict)

    print("="*40)
    print("Observation Model Test")
    print("="*40)
    pprint.pprint(obs001.to_dict())

    print("="*40)
    print("Observation Determine Scans Test")
    print("="*40)
    obs000.determine_scans()
    pprint.pprint(obs000.to_dict())

    print("="*40)
    print("Observation Save to Disk Test")
    print("="*40)
    try:
        obs000.save_to_disk(output_dir="./data")
        print("Observation saved to disk successfully.")
    except XSoftwareFailure as e:
        print(f"Failed to save observation to disk: {e}")

    print("="*40)
    print("Observation Convert to FITS and Save to Disk Test")
    print("="*40)
    try:
        obs000.save_fits_to_disk(output_dir="./data")
        print("Observation FITS file saved to disk successfully.")
    except XSoftwareFailure as e:
        print(f"Failed to save observation FITS to disk: {e}")
