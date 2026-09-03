# -*- coding: utf-8 -*-

import enum
from datetime import datetime, timezone
import numpy as np
from schema import Schema, And, Or, Use, SchemaError

from astropy.coordinates import EarthLocation, AltAz
import astropy.units as u
from astropy.time import Time

from models.app import AppModel
from models.base import BaseModel
from models.comms import CommunicationStatus
from models.health import HealthState
from models.ws import WeatherData, WeatherStationList
from util.xbase import XInvalidTransition, XAPIValidationFailed, XSoftwareFailure

# Definition: Mode
# State: A mutually exclusive condition representing the current internal and operational status of a system, typically changing automatically in response to events or execution progress.
# Mode: A configuration or operating context that defines how a system behaves, what actions are permitted, and how state transitions are constrained.

# Think of state as internal truth
# Think of mode as external contract

# Pointing States are only relevant when the dish is in OPERATE mode
class PointingState(enum.IntEnum):
    READY = 0               # Dish is pointing in a stationary direction and is ready to slew, track or scan i.e. receive pointing commands
    SLEW = 1                # Dish moves to the commanded alt-az position at maximum speed. SLEW is also used when settling onto a target. 
    TRACK = 2               # Dish is tracking a target within the pointing accuracy limits of the dish. The target may be moving across the sky.
    SCAN = 3                # Dish is scanning across the sky e.g. offset or five-point scan. The target may be moving across the sky.
    UNKNOWN = 4             # Pointing state is unknown

class DishMode(enum.IntEnum):
    STARTUP = 0             # Transitional: Reported when power is restored to the dish, perform initial checks and generally auto-transition to STANDBY
    SHUTDOWN = 1            # Non-transitional: To ensure dish is safe before power loss (for a planned outage or UPS trigger), power on should set to STARTUP
    STANDBY_LP = 2          # Non-transitional: Dish is paritally powered e.g. running on a UPS, generally transition to SHUTDOWN or STANDBY_FP from here
    STANDBY_FP = 3          # Non-transitional: Dish is fully powered and can prepare for an observation, generally transition to CONFIG from here
    MAINTENANCE = 4         # Non-transitional: Stow the dish to make it safe for maintenance activities, remain in maintenance until explicitly changed to another mode
    STOW = 5                # Non-transitional: Stow the dish to a safe position, generally transition to STANDBY after stowing
    CONFIG = 6              # Transitional: Configure the dish before observations e.g. switching a feed, generally auto-transition to OPERATE (by TM)
    OPERATE = 7             # Transitional: Actively observe targets as directed by TM, generally auto-transition to STANDBY after observations
    UNKNOWN = 8

class Capability(enum.IntEnum):
    UNAVAILABLE = 0         # Dish is unavailable due to functional error or components are not fitted, or during STARTUP 
    STANDBY = 1             # Dish is fully functional and ready to operate, but not currently marked as operational
    CONFIGURING = 2         # Dish is in the process of configuring to become ready for operation
    OPERATE_DEGRADED = 3    # Dish is operating but with degraded performance or partial functionality
    OPERATE_FULL = 4        # Dish is operating at full performance and functionality
    UNKNOWN = 5             # Dish capability state is unknown

class Feed(enum.IntEnum):
    NONE = 0
    H3T_1420 = 1    # 3 Turn Helical Feed 1420 MHz 
    H7T_1420 = 2    # 7 Turn Helical Feed 1420 MHz
    LF_400 = 3      # Loop Feed 400 MHz
    LOAD = 4        # Load for calibration

class DriverType(enum.IntEnum):
    UNKNOWN = 0
    MD01 = 1            # RF Hamdesigns MD-01
    MD02 = 2            # RF Hamdesigns MD-02
    MD03 = 3            # RF Hamdesigns MD-03
    DRIFT = 4           # Custom driver for drift scan dishes that do not have slewing/tracking capability
    LOSMANDY_G11 = 5    # Losmandy G-11
    ASCOM = 6           # ASCOM Standard Driver
    INDI = 7            # INDI Standard Driver  
    MOTION = 9          # Dish with no integrated motors, but pointing is reported by an attached IMU

class PECModel(BaseModel):
    """A class representing the periodic error correction (PEC) model for a dish target."""

    schema = Schema({
        "_type": And(str, lambda v: v == "PECModel"),
        "tgt_id": Or(None, And(str, lambda v: isinstance(v, str))),   # Target identifier in the form {obs_id}_{obs.tgt_idx}
        "alt_rms": And(Or(int, float), lambda v: v >= 0.0),           # RMS periodic error correction in altitude (arcseconds)
        "az_rms": And(Or(int, float), lambda v: v >= 0.0),            # RMS periodic error correction in azimuth (arcseconds)
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "PECModel",
            "tgt_id": None,
            "alt_rms": 0.0,
            "az_rms": 0.0,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class DishModel(BaseModel):
    """A class representing the dish model."""

    schema = Schema({      
        "_type": And(str, lambda v: v == "DishModel"),                                                                     
        "dsh_id": And(str, lambda v: isinstance(v, str)),                                         # Dish identifer e.g. "dish001" 
        "short_desc": Or(None, And(str, lambda v: isinstance(v, str))),                           # Short description of the dish
        "diameter": And(Or(int, float), lambda v: v >= 0.0),                                      # Dish diameter (meters)
        "fd_ratio": And(Or(int, float), lambda v: v >= 0.0),                                      # Dish focal length to diameter ratio
        "latitude": And(Or(int, float), lambda v: -90.0 <= v <= 90.0),                            # Dish latitude (degrees)
        "longitude": And(Or(int, float), lambda v: -180.0 <= v <= 180.0),                         # Dish longitude (degrees)
        "height": And(Or(int, float), lambda v: v >= 0.0),                                        # Dish height (meters) above sea level
        "ws_id": Or(None, And(str, lambda v: isinstance(v, str))),                                # Preferred weather station id for this dish
        "feed_type": And(Feed, lambda v: isinstance(v, Feed)),                                    # Current feed type installed on the dish
        "feed_config": Or(None, lambda v: v is None or isinstance(v, BaseModel)),                 # Feed configuration instance
        "dig_id": Or(None, And(str, lambda v: isinstance(v, str))),                               # Current digitiser id assigned to the dish
        "mode": And(DishMode, lambda v: isinstance(v, DishMode)),
        "pointing_state": And(PointingState, lambda v: isinstance(v, PointingState)),
        "desired_altaz": Or(None, dict, lambda v: v is None or isinstance(v, (dict, SkyCoord))),  # Desired alt-az position of dish
        "pointing_altaz": Or(None, dict, lambda v: v is None or isinstance(v, (dict, SkyCoord))), # Current alt-az pointing direction of dish
        "pointing_altaz_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),          # Datetime corresponding to the current pointing_altaz measurement
        "velocity_altaz": Or(None, dict, lambda v: v is None or isinstance(v, dict)),             # Current velocity of dish in Altitude and Azimuth (degrees per second)
        "target": Or(None, lambda v: v is None or isinstance(v, BaseModel)),                      # Current target model assigned to the dish
        "tgt_id": Or(None, And(str, lambda v: isinstance(v, str))),                               # Current target id assigned to the dish in the form {obs_id}_{obs.tgt_idx}
        "tgt_acq_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),                 # Datetime when the dish acquired the current target
        "tgt_pec": And(list, lambda v: isinstance(v, list)),                                      # Current periodic error correction (PEC) list of PECModel instances 
        "capability": And(Capability, lambda v: isinstance(v, Capability)),
        "driver_type": And(DriverType, lambda v: isinstance(v, DriverType)),                      # Dish driver type e.g. "ASCOM", "INDI", "MD-01", "MD-02"
        "driver_config": Or(None, lambda v: v is None or isinstance(v, BaseModel)),               # Dish driver configuration instance e.g. MD01Config
        "driver_poll_period": Or(None, And(int, lambda v: v > 0)),                                # Dish driver poll period in milliseconds to get altaz updates
        "driver_failures": And(int, lambda v: v >= 0),                                            # Count of consecutive driver call failures
        "health": And(HealthState, lambda v: isinstance(v, HealthState)),                         # Overall health state of the dish based on driver failures and other factors
        "weather_alarm": And(bool, lambda v: isinstance(v, bool)),                                # Weather alarm status for the dish, True when weather conditions are unsafe for operation
        "last_err_msg": Or(None, And(str, lambda v: isinstance(v, str))),                         # Last error message from the dish manager
        "last_err_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),                # Last error datetime from the dish manager
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    # Allow transitions to UNKNOWN (inconsistency detected) from any state/mode, and to itself to remain in a given state/mode following an event
    allowed_transitions = {
        "mode": { 
            DishMode.STARTUP:     {DishMode.UNKNOWN, DishMode.STARTUP, DishMode.STANDBY_FP, DishMode.SHUTDOWN},
            DishMode.SHUTDOWN:    {DishMode.UNKNOWN, DishMode.SHUTDOWN, DishMode.STARTUP},
            DishMode.STANDBY_LP:  {DishMode.UNKNOWN, DishMode.STANDBY_LP, DishMode.STANDBY_FP, DishMode.STOW, DishMode.SHUTDOWN},
            DishMode.STANDBY_FP:  {DishMode.UNKNOWN, DishMode.STANDBY_FP, DishMode.STANDBY_LP, DishMode.CONFIG, DishMode.STOW, DishMode.SHUTDOWN},
            DishMode.CONFIG:      {DishMode.UNKNOWN, DishMode.CONFIG, DishMode.OPERATE, DishMode.STOW, DishMode.SHUTDOWN},
            DishMode.STOW:        {DishMode.UNKNOWN, DishMode.STOW, DishMode.STANDBY_LP, DishMode.STANDBY_FP, DishMode.MAINTENANCE, DishMode.SHUTDOWN},
            DishMode.MAINTENANCE: {DishMode.UNKNOWN, DishMode.MAINTENANCE, DishMode.STOW, DishMode.SHUTDOWN},
            DishMode.OPERATE:     {DishMode.UNKNOWN, DishMode.OPERATE, DishMode.STANDBY_FP, DishMode.STANDBY_LP, DishMode.CONFIG, DishMode.STOW, DishMode.SHUTDOWN},
            DishMode.UNKNOWN:     {DishMode.UNKNOWN, DishMode.STARTUP, DishMode.STOW, DishMode.MAINTENANCE, DishMode.SHUTDOWN, DishMode.STANDBY_FP},
        },
        "pointing_state": { 
            PointingState.UNKNOWN:  {PointingState.UNKNOWN, PointingState.READY},
            PointingState.READY:    {PointingState.UNKNOWN, PointingState.READY, PointingState.SLEW, PointingState.TRACK, PointingState.SCAN},
            PointingState.SLEW:     {PointingState.UNKNOWN, PointingState.SLEW, PointingState.READY},
            PointingState.TRACK:    {PointingState.UNKNOWN, PointingState.TRACK, PointingState.READY},
            PointingState.SCAN:     {PointingState.UNKNOWN, PointingState.SCAN, PointingState.READY},
        },
        "capability": {
            Capability.UNKNOWN:          {Capability.UNKNOWN, Capability.UNAVAILABLE, Capability.STANDBY, Capability.CONFIGURING, Capability.OPERATE_DEGRADED, Capability.OPERATE_FULL},
            Capability.UNAVAILABLE:      {Capability.UNKNOWN, Capability.UNAVAILABLE, Capability.STANDBY},
            Capability.STANDBY:          {Capability.UNKNOWN, Capability.STANDBY, Capability.CONFIGURING, Capability.UNAVAILABLE},
            Capability.CONFIGURING:      {Capability.UNKNOWN, Capability.CONFIGURING, Capability.OPERATE_DEGRADED, Capability.OPERATE_FULL, Capability.STANDBY},
            Capability.OPERATE_DEGRADED: {Capability.UNKNOWN, Capability.OPERATE_DEGRADED, Capability.OPERATE_FULL, Capability.STANDBY, Capability.CONFIGURING},
            Capability.OPERATE_FULL:     {Capability.UNKNOWN, Capability.OPERATE_FULL, Capability.OPERATE_DEGRADED, Capability.STANDBY, Capability.CONFIGURING},
        },
    }

    def __init__(self, **kwargs):

        if "feed" in kwargs and "feed_type" not in kwargs:
            kwargs["feed_type"] = kwargs["feed"]

        # Default values
        defaults = {
            "_type": "DishModel",
            "dsh_id": "<undefined>",
            "short_desc": None,
            "diameter": 0.0,
            "fd_ratio": 0.0,
            "latitude": 0.0,
            "longitude": 0.0,
            "height": 0.0,
            "ws_id": None,
            "feed_type": Feed.NONE,
            "feed_config": None,
            "dig_id": None,
            "mode": DishMode.UNKNOWN,
            "pointing_state": PointingState.UNKNOWN,
            "desired_altaz": None,
            "pointing_altaz": None,
            "pointing_altaz_dt": None,
            "velocity_altaz": None,
            "target": None,
            "tgt_id": None,
            "tgt_acq_dt": None,
            "tgt_pec": [],
            "capability": Capability.UNKNOWN,
            "driver_type": DriverType.UNKNOWN,
            "driver_config": None,                          # Initialize with None, will be set based on driver_type
            "driver_poll_period": 1000,                     # Default to 1000 ms
            "driver_failures": 0,                           # Initialize failure count to zero
            "health": HealthState.UNKNOWN,
            "weather_alarm": False,                         # Initialize weather alarm to False
            "last_err_msg": None,
            "last_err_dt": None,   
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

        # Mode transition history: each row is [unix_timestamp, old_mode, new_mode]
        self._mode_hist_max = 1000
        self._mode_hist = np.zeros((self._mode_hist_max, 3))

    def __setattr__(self, name, value):
        """Override to capture mode transitions in a numpy history array."""
        if name == "mode" and hasattr(self, '_mode_hist'):
            try:
                old_mode = self._data.get("mode", None)
            except AttributeError:
                old_mode = None

            # Delegate to BaseModel (validates transition + schema)
            super().__setattr__(name, value)

            # Record the transition after successful validation
            if old_mode is not None:
                now = Time(datetime.now(timezone.utc))
                now.format = 'unix'
                self._mode_hist = np.roll(self._mode_hist, shift=-1, axis=0)
                self._mode_hist[-1] = (now.value, int(old_mode), int(value))
            return

        super().__setattr__(name, value)

    def get_mode_hist(self) -> np.ndarray:
        """Return the mode transition history as a numpy array.
        Each row is [unix_timestamp, old_mode, new_mode].
        Rows with timestamp == 0 are unused slots.
        """
        return self._mode_hist[self._mode_hist[:, 0] > 0]

    def increment_failures(self):
        """ Increment the driver failure count by one.
        """
        self.driver_failures += 1
        self.last_update = datetime.now(timezone.utc)

    def reset_failures(self):
        """ Reset the driver failure count to zero.
        """
        self.driver_failures = 0
        self.last_update = datetime.now(timezone.utc)

    def get_pec_by_tgt_id(self, tgt_id: str) -> PECModel:
        """ Retrieve a PECModel from the tgt_pec list by its tgt_id.
        Args: tgt_id (str): The identifier of the target to retrieve.
        Returns: PECModel: The PECModel with the specified tgt_id. Returns None if not found.
        """
        for pec in self.tgt_pec:
            if pec.tgt_id == tgt_id:
                return pec
        return None

    def set_last_err(self, message: str):
        """ Set the last dish model error message and timestamp.
        """
        self.last_err_msg = message
        now = datetime.now(timezone.utc)
        self.last_err_dt = now
        self.last_update = now
        return message

class DishList(BaseModel):
    """A class representing a list of dishes."""

    schema = Schema({
        "_type": And(str, lambda v: v == "DishList"),
        "list_id": And(str, lambda v: isinstance(v, str)),              # Dish List identifier e.g. "active"   
        "dish_list": And(list, lambda v: isinstance(v, list)),          # List of DishModel objects
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "DishList",
            "list_id": "<undefined>",
            "dish_list": [],
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class DishManagerModel(BaseModel):
    """A class representing the dish manager (application) model."""

    schema = Schema({    
        "_type": And(str, lambda v: v == "DishManagerModel"),     
        "id": And(str, lambda v: isinstance(v, str)),                                         # Dish Manager identifier e.g. "dm001"         
        "dish_store": And(DishList, lambda v: isinstance(v, DishList)),                       # List of DishModel objects
        "weather_store": Or(None, lambda v: v is None or isinstance(v, WeatherStationList)),  # List of WeatherData objects from weather stations relevant to the dishes
        "app": And(AppModel, lambda v: isinstance(v, AppModel)),
        "tm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "ws_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "DishManagerModel",
            "id": "<undefined>",
            "dish_store": DishList(),
            "weather_store": WeatherStationList(),
            "app": AppModel(
                app_name="dshmgr",
                app_running=False,
                app_cmd_port=60003,
                num_processors=0,
                queue_size=0,
                interfaces=[],
                processors=[],
                health=HealthState.UNKNOWN,
                last_update=datetime.now(timezone.utc),
            ),
            "tm_connected": CommunicationStatus.NOT_ESTABLISHED,
            "ws_connected": CommunicationStatus.NOT_ESTABLISHED,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def get_dish_by_id(self, dsh_id: str) -> DishModel:
        """ Retrieve a DishModel from the dish_store by its dsh_id.
        Args: dsh_id (str): The identifier of the dish to retrieve.
        Returns: DishModel: The DishModel with the specified dsh_id. Returns None if not found.
        """
        for dish in self.dish_store.dish_list:
            if dish.dsh_id == dsh_id:
                return dish
        return None

    def get_dish_by_dig_id(self, dig_id: str) -> DishModel:
        """ Retrieve a DishModel from the dish_store by its dig_id.
            Theoretically, dig_id should be unique across dishes.
        Args: dig_id (str): The digitiser identifier assigned to the dish.
        Returns: DishModel: The DishModel with the specified dig_id. Returns None if not found.
        """
        for dish in self.dish_store.dish_list:
            if dish.dig_id == dig_id:
                return dish
        return None

    def get_weather_by_ws_id(self, ws_id: str) -> WeatherData:
        """ Retrieve a WeatherData from the weather_store by its ws_id.
        Args: ws_id (str): The identifier of the weather station to retrieve.
        Returns: WeatherData: The WeatherData with the specified ws_id. Returns None if not found.
        """
        if self.weather_store is None:
            return None

        for weather in self.weather_store.weather_data:
            if weather.ws_id == ws_id:
                return weather
        return None

if __name__ == "__main__":

    dish001 = DishModel(
        dsh_id="dish001",
        short_desc="70cm Discovery dish",
        latitude=45.67, longitude=-111.05, height=1500.0,
        mode=DishMode.STARTUP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.NONE,
        dig_id="dig001",
        capability=Capability.UNKNOWN,
        last_update=datetime.now(timezone.utc)
    )

    dish002 = DishModel(id="dish002")

    print("="*40)
    print("Dish001 Model Initialized")
    print(dish001.to_dict())
    print("="*40)

    # ✅ Valid transition
    dish001.mode = DishMode.STANDBY_LP
    print(f"After valid transition: {dish001.mode.name}")

    # ❌ Invalid transition (will raise ValueError)
    try:
        dish001.mode = DishMode.OPERATE
    except XInvalidTransition as e:
        print(f"Caught expected exception on invalid transition: {e}")

    # ❌ Schema violation (wrong type)
    try:
        dish001.feed_type = "H3T_1420"
    except XAPIValidationFailed as e:
        print("Schema check failed:", e)

    import pprint
    pprint.pprint(dish001.to_dict())

    print("="*40)
    print("Dish002 Model Initialized")
    pprint.pprint(dish002.to_dict())
    print("="*40)

    print("Dish Manager Model Test")
    dsh_mgr = DishManagerModel(
        id="dm001",
        dish_store=DishList(
            dish_list=[
                DishModel(
                    dsh_id="dish001",
                    short_desc="70cm Discovery dish",
                    latitude=45.67, longitude=-111.05, height=1500.0,
                    mode=DishMode.STARTUP,
                    pointing_state=PointingState.UNKNOWN,
                    feed_type=Feed.NONE,
                    capability=Capability.UNKNOWN,
                    last_update=datetime.now(timezone.utc)
                )
            ],
            last_update=datetime.now(timezone.utc)
        ),
        app=AppModel(
            app_name="dsh",
            app_running=True,
            num_processors=2,
            queue_size=0,
            interfaces=["tm", "dsh"],
            processors=[],
            health=HealthState.UNKNOWN,
            last_update=datetime.now(timezone.utc)
        ),
        tm_connected=CommunicationStatus.ESTABLISHED,
        last_update=datetime.now(timezone.utc)
    )
    pprint.pprint(dsh_mgr.to_dict())

    print("="*40)
    print("Add another Dish to Dish Manager Model")
    print("="*40)
    new_dish = DishModel(
        dsh_id="dish002",
        short_desc="50cm Explorer dish",
        latitude=46.00, longitude=-112.00, height=1200.0,
        mode=DishMode.STARTUP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.NONE,
        dig_id="dig002",
        capability=Capability.UNKNOWN,
        last_update=datetime.now(timezone.utc)
    )
    dsh_mgr.dish_store.dish_list.append(new_dish)
    pprint.pprint(dsh_mgr.to_dict())

    print("="*40)
    print("Dish Manager Model Default Test")
    print("="*40)
    dsh_mgr_default = DishManagerModel()
    pprint.pprint(dsh_mgr_default.to_dict())

    print("="*40)
    print("Save Dish List to disk as JSON")
    print("="*40)

    dsh_mgr.dish_store.save_to_disk(filename="dish_store_test.json")

    print("="*40)
    print("Load Dish List from disk as JSON")
    print("="*40)   
    dish_store = DishList().load_from_disk(filename="dish_store_test.json")
    pprint.pprint(dish_store.to_dict())

    print("="*40)
    print("Now prepare default DigitiserList configuration")
    print("="*40)  

    dish001 = DishModel(
        dsh_id="dish001",
        short_desc="70cm Discovery Dish",
        diameter=0.7,
        fd_ratio=0.37,
        latitude=53.187052, longitude=-2.256079, height=94.0,
        mode=DishMode.STANDBY_FP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.H3T_1420,
        dig_id="dig001",
        capability=Capability.OPERATE_FULL,
        driver_type=DriverType.LOSMANDY_G11,
        driver_config=None,
        last_update=datetime.now(timezone.utc)
    )

    from dsh.drivers.md01.md01_config import MD01Config

    md01_cfg = MD01Config(
        host="192.168.0.2",
        port=65000,
        stow_alt=90.0,
        stow_az=0.0,
        offset_alt=0.0,
        offset_az=0.0,
        min_alt=0.0,
        max_alt=90.0,
        close_enough=0.1,
        last_update=datetime.now(timezone.utc)
    )

    dish002 = DishModel(
        dsh_id="dish002",
        short_desc="3m Jodrell Dish",
        diameter=3.0,
        fd_ratio=0.43,
        latitude=53.2421, longitude=-2.3067, height=80.0,
        mode=DishMode.STANDBY_FP,
        pointing_state=PointingState.UNKNOWN,
        feed_type=Feed.NONE,
        dig_id="dig002",
        capability=Capability.OPERATE_FULL,
        driver_type=DriverType.MD01,
        driver_config=md01_cfg,
        last_update=datetime.now(timezone.utc)
    )
    
    default_dshlist = DishList(
        list_id = "default",
        dish_list=[dish001, dish002],
    )

    default_dshlist.save_to_disk(output_dir="./config/default")

    
