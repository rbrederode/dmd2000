# -*- coding: utf-8 -*-
from datetime import datetime
import os
import math
from schema import Schema, And, Or

from sdk.models.app import AppModel
from sdk.models.base import BaseModel
from sdk.util.xbase import XAPIValidationFailed
from sdk.models.comms import CommunicationStatus
from sdk.models.dsh import DishManagerModel, DishModel, DishMode, PointingState, Feed, Capability
from sdk.models.dig import DigitiserList, DigitiserModel
from sdk.models.health import HealthState
from sdk.models.oet import OETModel
from sdk.models.oda import ODAModel
from sdk.models.sdp import ScienceDataProcessorModel
from sdk.models.proc import ProcessorModel
from sdk.models.tm import TelescopeManagerModel
from sdk.models.ws import WeatherStationModel
from sdk.models.web import WebServerModel


class TelescopeConfig(BaseModel):
    """Observatory identity/location only; independent of Config Profiles.

    Coordinates use decimal degrees (east-positive longitude), height metres.
    Unconfigured coordinates are None, never an implicit Greenwich location.
    """

    schema = Schema({
        '_type': And(str, lambda value: value == 'TelescopeConfig'),
        'name': And(str, lambda value: bool(value.strip())),
        'latitude': Or(None, And(lambda value: type(value) in (int, float),
                                 lambda value: math.isfinite(value) and -90 <= value <= 90)),
        'longitude': Or(None, And(lambda value: type(value) in (int, float),
                                  lambda value: math.isfinite(value) and -180 <= value <= 180)),
        'height': Or(None, And(lambda value: type(value) in (int, float), math.isfinite)),
    })

    def __init__(self, **kwargs):
        unexpected = set(kwargs) - set(self.schema.schema)
        if unexpected:
            raise XAPIValidationFailed(f"Unknown TelescopeConfig fields: {', '.join(sorted(unexpected))}")
        super().__init__(**({'_type': 'TelescopeConfig', 'name': 'dmd2000 Observatory',
                            'latitude': None, 'longitude': None, 'height': None} | kwargs))



# The Telescope Model
class TelescopeModel:
    """A class representing the telescope model, which includes:

        - Observation Data Archive,
        - Telescope Manager, 
        - Dish Manager, 
        - Digitiser Manager,
        - Science Data Processor, and the
        - Weather Station
        - Web Server
        
    """
    def __init__(self):

        self.config = TelescopeConfig()                            # Observatory-level configuration
        self.oda = ODAModel(id="oda001")                            # Observation Data Archive model
        self.tel_mgr = TelescopeManagerModel(id="telmgr001")        # Telescope Manager (App) model
        self.dsh_mgr = DishManagerModel(id="dshmgr001")             # Dish Manager (App) model
        self.dig_store = DigitiserList(list_id="diglist001")        # Digitiser store (just a list of digitisers)
        self.sdp = ScienceDataProcessorModel(sdp_id="sdp001")       # Science Data Processor (App) model
        self.web_svr = WebServerModel(id="web001")                  # Web Server (App) model
        self.wtr_stn = WeatherStationModel(id="ws001")              # Weather Station (App) model

    def get_app_model_by_name(self, app_name: str) -> AppModel:
        """Return the application model identified by its UI application name."""

        requested = str(app_name or "").strip().lower()
        if not requested:
            raise ValueError("No application name was specified")

        fixed_apps = {
            "tm": self.tel_mgr,
            "dm": self.dsh_mgr,
            "sdp": self.sdp,
            "web": self.web_svr,
            "ws": self.wtr_stn,
        }
        if requested in fixed_apps:
            model = fixed_apps[requested]
            if model is None:
                raise ValueError(f"Application '{app_name}' is unavailable")
            return model.app

        for digitiser in self.dig_store.dig_list:
            if requested == digitiser.dig_id.strip().lower():
                return digitiser.app

        raise ValueError(f"Unknown application name '{app_name}'")
    
    def save_to_disk(self):
        # Implement disk saving logic here
        pass

    def load_from_disk(self):
        # Implement disk loading logic here
        pass

    def get_scan_store_dir(self) -> str:
        # Determine the scan storage directory from SDP arguments

        if self.sdp.app.arguments and 'scan_store_dir' in self.sdp.app.arguments:

            scan_store_dir = self.sdp.app.arguments.get('scan_store_dir','~/') if self.sdp.app.arguments is not None else '~/'
            return os.path.expanduser(scan_store_dir)
        
        return os.path.expanduser('~/')

    def to_dict(self):
        return {
            "config": self.config.to_dict(),
            "oda": self.oda.to_dict(),
            "tel_mgr": self.tel_mgr.to_dict(),
            "dsh_mgr": self.dsh_mgr.to_dict(),
            "dig_store": self.dig_store.to_dict(),
            "sdp": self.sdp.to_dict(),
            "web_svr": self.web_svr.to_dict(),
            "wtr_stn": self.wtr_stn.to_dict()

        }

if __name__ == "__main__":
    telescope = TelescopeModel()

    import pprint
    print("="*40)
    print("Telescope Model Initialized")
    print("="*40)
    pprint.pprint(telescope.to_dict())
