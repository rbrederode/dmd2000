# -*- coding: utf-8 -*-
from datetime import datetime
import os

from models.app import AppModel
from models.comms import CommunicationStatus
from models.dsh import DishManagerModel, DishModel, DishMode, PointingState, Feed, Capability
from models.dig import DigitiserList, DigitiserModel
from models.health import HealthState
from models.oet import OETModel
from models.oda import ODAModel
from models.sdp import ScienceDataProcessorModel
from models.proc import ProcessorModel
from models.tm import TelescopeManagerModel
from models.ws import WeatherStationModel

# The Telescope Model
class TelescopeModel:
    """A class representing the telescope model, which includes:

        - Observation Data Archive,
        - Telescope Manager, 
        - Dish Manager, 
        - Digitiser Manager,
        - Science Data Processor, and the
        - Weather Station
        
    """
    def __init__(self):

        self.oda = ODAModel(id="oda001")                            # Observation Data Archive model
        self.tel_mgr = TelescopeManagerModel(id="telmgr001")        # Telescope Manager (App) model
        self.dsh_mgr = DishManagerModel(id="dshmgr001")             # Dish Manager (App) model
        self.dig_store = DigitiserList(list_id="diglist001")        # Digitiser store (just a list of digitisers)
        self.sdp = ScienceDataProcessorModel(sdp_id="sdp001")       # Science Data Processor (App) model
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
            "oda": self.oda.to_dict(),
            "tel_mgr": self.tel_mgr.to_dict(),
            "dsh_mgr": self.dsh_mgr.to_dict(),
            "dig_store": self.dig_store.to_dict(),
            "sdp": self.sdp.to_dict(),
            "wtr_stn": self.wtr_stn.to_dict()

        }

if __name__ == "__main__":
    telescope = TelescopeModel()

    import pprint
    print("="*40)
    print("Telescope Model Initialized")
    print("="*40)
    pprint.pprint(telescope.to_dict())
