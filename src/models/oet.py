import enum
from astropy import units as u
from datetime import datetime, timezone
from schema import Schema, And, Or, Use, SchemaError

from models.app import AppModel
from models.base import BaseModel
from models.comms import CommunicationStatus
from models.health import HealthState
from models.obs import ObsModel, ObsState
from models.scan import ScanModel, ScanState
from models.target import TargetModel, SkyCoord
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

class OETModel(BaseModel):
    """A class representing the observation execution tool model."""

    schema = Schema({
        "_type": And(str, lambda v: v == "OETModel"),
        "id": And(str, lambda v: isinstance(v, str)),
        "app": And(AppModel, lambda v: isinstance(v, AppModel)),
        
        "obs_created": And(int, lambda v: v >= 0),
        "obs_completed": And(int, lambda v: v >= 0),
        "obs_aborted": And(int, lambda v: v >= 0),
        "processing_obs": And(list, lambda v: all(isinstance(item, ObsModel) for item in v)),
        "tm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "OETModel",
            "app": AppModel(
                app_name="oet",
                app_running=False,
                num_processors=0,
                queue_size=0,
                interfaces=[],
                processors=[],
                health=HealthState.UNKNOWN,
                last_update=datetime.now(timezone.utc),
            ),
            "obs_created": 0,
            "obs_completed": 0,
            "obs_aborted": 0,
            "processing_obs": [],
            "tm_connected": CommunicationStatus.NOT_ESTABLISHED,
            "last_update": datetime.now(timezone.utc)
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def add_obs(self, obs: ObsModel):
        """Add an observation to the processing queue.
            A single observation is maintained in the processing observations list per digitiser id.
            If an observation with the same digitiser id already exists, it will be replaced.

        Args:
            obs (Observation): The observation model to add.
        """

        if not isinstance(obs, ObsModel):
            raise XAPIValidationFailed("obs must be an instance of Observation")
        # Replace existing observation with the same observation id
        for i, existing_obs in enumerate(self.processing_obs):
            if existing_obs.obs_id == obs.obs_id:
                self.processing_obs[i] = obs
                return

        self.processing_obs.append(obs)


if __name__ == "__main__":

    obs001 = ObsModel(
        obs_id="obs001",
        title="Test Observation",
        description="This is a test observation of a celestial target.",
        state=ObsState.EMPTY,
        dsh_id="dish001",
        start_dt=datetime.now(timezone.utc),
        end_dt=datetime.now(timezone.utc),
        last_update=datetime.now(timezone.utc)
    )
    
    oet001 = OETModel(
        id="oet001",
        app=AppModel(
            app_name="oet",
            app_running=True,
            num_processors=2,
            queue_size=0,
            interfaces=["tm"],
            processors=[],
            health=HealthState.UNKNOWN,
            last_update=datetime.now(timezone.utc)
        ),
        obs_created=1,
        obs_completed=0,
        obs_aborted=0,
        processing_obs=[obs001],
        tm_connected=CommunicationStatus.NOT_ESTABLISHED,
        last_update=datetime.now(timezone.utc)
    )

    oet001.add_obs(obs001)

    oet002 = OETModel(id="oet002")

    import pprint
    print("="*40)
    print("oet001 Model Initialized")
    print("="*40)
    pprint.pprint(oet001.to_dict())

    print("="*40)
    print("oet002 Model with Defaults Initialized")
    print("="*40)
    pprint.pprint(oet002.to_dict())
