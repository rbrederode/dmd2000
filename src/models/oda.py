import enum
import json
from pathlib import Path
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.base import BaseModel
from models.obs import ObsModel, ObsState

# Models comprising the Observation Data Archive (ODA)

class ScanStore(BaseModel):
    """A class representing the scan store."""

    schema = Schema({
        "_type": And(str, lambda v: v == "ScanStore"),
        "spr_files": And(list, lambda v: isinstance(v, list)),          # List of *spr.csv (summed power) filenames
        "load_files": And(list, lambda v: isinstance(v, list)),         # List of *load.csv (terminated signal chain) filenames    
        "tsys_files": And(list, lambda v: isinstance(v, list)),         # List of *tsys.csv (system temperature calibration) filenames
        "gain_files": And(list, lambda v: isinstance(v, list)),         # List of *gain.csv (gain calibration) filenames
        "meta_files": And(list, lambda v: isinstance(v, list)),         # List of *meta.csv (scan metadata) filenames
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "ScanStore",
            "spr_files": [],
            "load_files": [],
            "tsys_files": [],
            "gain_files": [],
            "meta_files": [],
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class ObsList(BaseModel):
    """A class representing a list of observations."""

    schema = Schema({
        "_type": And(str, lambda v: v == "ObsList"),
        "obs_list": And(list, lambda v: isinstance(v, list)),          # List of observations
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "ObsList",
            "obs_list": [],
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def get_obs_by_id(self, obs_id: str):
        """Retrieve an observation by its identifier."""
        for obs in self.obs_list:
            if obs.obs_id == obs_id:
                return obs
        return None

    def get_obs_by_dsh_id(self, dsh_id: str):
        """Retrieve an observation by its Dish identifier."""
        for obs in self.obs_list:
            if obs.dsh_id == dsh_id:
                return obs
        return None

    @classmethod
    def from_disk(cls, observation_file: str):
        """Load an observation definition file and normalise it into an ObsList instance.

        Accepts:
        - an ObsList JSON object
        - a single ObsModel JSON object
        - a raw list of observation dictionaries
        """

        obs_path = Path(observation_file).expanduser()
        if not obs_path.is_absolute():
            obs_path = Path.cwd() / obs_path

        with obs_path.open("r", encoding="utf-8") as f:
            observation_data = json.load(f)

        if isinstance(observation_data, dict):
            model_type = observation_data.get("_type")
            if model_type == "ObsList":
                return cls.from_dict(observation_data)
            if model_type == "ObsModel":
                return cls.from_dict({
                    "_type": "ObsList",
                    "obs_list": [observation_data],
                    "last_update": {
                        "_type": "datetime",
                        "value": datetime.now(timezone.utc).isoformat()
                    }
                })

        if isinstance(observation_data, list):
            return cls.from_dict({
                "_type": "ObsList",
                "obs_list": observation_data,
                "last_update": {
                    "_type": "datetime",
                    "value": datetime.now(timezone.utc).isoformat()
                }
            })

        raise ValueError(
            f"ODA encountered unsupported observation file format in {obs_path}. "
            "Expected an ObsList dict, ObsModel dict, or a list of observations."
        )

class ODAModel(BaseModel):
    """A class representing the observation data archive."""

    schema = Schema({
        "_type": And(str, lambda v: v == "ODAModel"),
        "scan_store": And(ScanStore, lambda v: isinstance(v, ScanStore)),
        "obs_store": And(ObsList, lambda v: isinstance(v, ObsList)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "ODAModel",
            "scan_store": ScanStore(),
            "obs_store": ObsList(),
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

if __name__ == "__main__":
    
    import pprint

    obs_list_dict = {'_type': 'ObsList', 'obs_list': [{'_type': 'ObsModel', 'dsh_id': 'Dish002', 'capabilities': 'Drift Scan over Zenith', 'diameter': 3, 'f/d_ratio': 1.3, 'latitude': 53.2421, 'longitude': -2.3067, 'total_integration_time': 60, 'estimated_slewing_time': 30, 'estimated_observation_duration': '00:01:30', 'scheduling_block_start': {'_type': 'datetime', 'value': '2025-12-07T19:00:00.000Z'}, 'scheduling_block_end': {'_type': 'datetime', 'value': '2025-12-07T20:00:00.000Z'}, 'obs_id': '2025-12-07T19:00Z-Dish002', 'obs_state': {'_type': 'enum.IntEnum', 'instance': 'ObsState', 'value': 'IDLE'}, 'target_configs': [{'_type': 'TargetConfig', 'feed_type': {'_type': 'enum.IntEnum', 'instance': 'Feed', 'value': 'H3T_1420'}, 'gain': 12, 'center_freq': 1420400000, 'bandwidth': 1000000, 'sample_rate': 2400000, 'target': {'_type': 'TargetModel', 'sky_coord': {'_type': 'SkyCoord', 'frame': 'icrs', 'ra': 204.2538, 'dec': -29.8658}, 'id': 'M83', 'type': {'_type': 'enum.IntEnum', 'instance': 'TargetType', 'value': 'SIDEREAL'}}, 'integration_time': 60, 'spectral_resolution': 128, 'target_id': 1}], 'user_email': 'ray.brederode@skao.int', 'created': {'_type': 'datetime', 'value': '2025-12-07T18:19:37.503Z'}}], 'last_update': {'_type': 'datetime', 'value': '2025-12-07T18:19:46.369Z'}}
  
    obslist001 = ObsList().from_dict(obs_list_dict) 
    print("="*40)
    print("Observation List from Dict")
    print("="*40)
    pprint.pprint(obslist001.to_dict())


    ss001 = ScanStore()
    ss_dir = "/Users/r.brederode/samples"

    # Read scan store directory listing 
    scan_files = list(Path(ss_dir).glob("*spr.csv"))
    # Sort by modification time, newest first
    scan_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    if scan_files:

        for scan_file in scan_files:
            mod_time = datetime.fromtimestamp(scan_file.stat().st_mtime, timezone.utc)
            ss001.spr_files.append(scan_file.name)

    print("="*40)
    print("Scan Store with Scan Store SPR Files")
    print("="*40)
    pprint.pprint(ss001.to_dict())

    print("="*40)
    print("Scan Store to_dict / from_dict Consistency Check")
    print("="*40)

    ss002 = ScanStore()
    ss002.from_dict(ss001.to_dict())
    pprint.pprint(ss002.to_dict())

    obs001 = ObsModel(
        obs_id="obs001",
        title="Test Observation",
        description="This is a test observation of a celestial target.",
        state=ObsState.EMPTY,
        dsh_id="dish001",
        scans=[],
        start_dt=datetime.now(timezone.utc),
        end_dt=datetime.now(timezone.utc),
        tsys_calibrators=[],
        gain_calibrators=[],
        load_calibrators=[],
        spr_scans=[],
        last_update=datetime.now(timezone.utc)
    )

    obs_list = ObsList()
    obs_list.obs_list.append(obs001)
    print("="*40)
    print("Observation List with One Observation")
    print("="*40)
    pprint.pprint(obs_list.to_dict())
