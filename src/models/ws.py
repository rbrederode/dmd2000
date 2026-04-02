import enum
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from schema import Schema, And, Or, Use, SchemaError

from models.app import AppModel
from models.base import BaseModel
from models.comms import CommunicationStatus
from models.health import HealthState
from util.xbase import XSoftwareFailure

logger = logging.getLogger(__name__)

class WeatherData(BaseModel):
    """A class representing weather data at a specific location and time."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WeatherData"),
        "obs_time": And(datetime, lambda v: isinstance(v, datetime)),               # Timestamp when the weather data was measured
        "ws_id": And(str, lambda v: isinstance(v, str)),                            # Weather station ID

        "temperature": Or(None, And(float, lambda v: -100 <= v <= 100)),            # Temperature in Celsius
        "humidity": Or(None, And(float, lambda v: 0 <= v <= 100)),                  # Humidity in percentage
        "pressure": Or(None, And(float, lambda v: v >= 0)),                         # Pressure in hPa
        "wind_speed": Or(None, And(float, lambda v: v >= 0)),                       # Wind speed in m/s
        "wind_direction": Or(None, And(float, lambda v: 0 <= v < 360)),             # Wind direction in degrees (0-359, where 0 is North)
        "precipitation": Or(None, And(float, lambda v: v >= 0)),                    # Precipitation in mm
        "dew_point": Or(None, And(float, lambda v: -100 <= v <= 100)),              # Dew point in Celsius
        "air_quality": Or(None, And(float, lambda v: v >= 0)),                      # Air quality index (AQI), where higher values indicate worse air quality
        "uv_index": Or(None, And(float, lambda v: v >= 0)),                         # UV index, where higher values indicate greater risk of harm from unprotected sun exposure
        "cloud_cover": Or(None, And(float, lambda v: 0 <= v <= 100)),               # Cloud cover in percentage
        "last_update": Or(None, And(datetime, lambda v: isinstance(v, datetime))),  # Timestamp when the weather data was last updated
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "WeatherData",
            "obs_time": None,
            "ws_id": None,

            "temperature": None,
            "humidity": None,
            "pressure": None,
            "wind_speed": None,
            "wind_direction": None,
            "precipitation": None,
            "dew_point": None,
            "air_quality": None,
            "uv_index": None,
            "cloud_cover": None,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def __str__(self):
        return f"WeatherData from station: {self.ws_id} (\n  temperature={self.temperature},\n  humidity={self.humidity},\n  pressure={self.pressure},\n  wind_speed={self.wind_speed},\n" + \
            f"  wind_direction={self.wind_direction},\n  precipitation={self.precipitation},\n  dew_point={self.dew_point},\n  air_quality={self.air_quality},\n  uv_index={self.uv_index},\n" + \
            f"  cloud_cover={self.cloud_cover},\n  obs_time={self.obs_time.isoformat() if self.obs_time else None},\n  last_update={self.last_update.isoformat() if self.last_update else None})"

class WeatherStationList(BaseModel):
    """A class representing a list of weather stations."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WeatherStationList"),
        "list_id": And(str, lambda v: isinstance(v, str)),                 # Weather Station List identifier e.g. "active"   
        "weather_enabled": And(bool, lambda v: isinstance(v, bool)),       # Flag to enable or disable weather monitoring and alarm processing for the dishes
        "weather_list": And(list, lambda v: isinstance(v, list)),          # List of WeatherData objects
        "threshold_timeout": And(int, lambda v: v >= 0),                   # Maximum age of weather data in seconds to keep in the list
        "threshold_wind_avg": And(float, lambda v: v >= 0),                # Threshold for high wind avg in m/s to trigger an alarm
        "threshold_wind_gust": And(float, lambda v: v >= 0),               # Threshold for high wind gust in m/s to trigger an alarm
        "threshold_wind_count": And(int, lambda v: v >= 0),                # Number of wind gust samples above threshold to trigger an alarm
        "threshold_precipitation": And(float, lambda v: v >= 0),           # Threshold for heavy precipitation in mm to trigger an alarm
        "created_dt": And(datetime, lambda v: isinstance(v, datetime)),    # Timestamp when the weather station list was created
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "WeatherStationList",
            "list_id": "active",
            "weather_enabled": True,                    # Default to True to enable weather monitoring and alarm processing
            "weather_list": [],
            "threshold_timeout": 30,                    # Maximum age of weather data in seconds to keep in the list
            "threshold_wind_gust": 30.0,                # Threshold for high wind gust in m/s to trigger an alarm
            "threshold_wind_avg": 20.0,                 # Threshold for high wind avg in m/s to trigger an alarm
            "threshold_precipitation": 10.0,            # Threshold for heavy precipitation in mm to trigger an alarm
            "threshold_wind_count": 3,                  # Number of wind samples above threshold to trigger an alarm
            "created_dt": datetime.now(timezone.utc),
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def __str__(self):
        weather_str = ",\n\n  ".join(str(wd) for wd in self.weather_list)
        return f"WeatherStationList (list_id={self.list_id}, len={len(self.weather_list)}, threshold_timeout={self.threshold_timeout}, threshold_wind_gust={self.threshold_wind_gust}, threshold_wind_avg={self.threshold_wind_avg}, threshold_precipitation={self.threshold_precipitation}, threshold_wind_count={self.threshold_wind_count}, last_update={self.last_update.isoformat()}): [\n  {weather_str}\n]"

    def is_ws_monitoring_enabled(self) -> bool:
        """
        Returns True if weather monitoring and alarm processing is enabled for the dishes, False otherwise.
        """
        return self.weather_enabled
    
    def alarm(self) -> bool:
        """
        Trims the weather list to only include samples within the threshold_timeout period.
        Alarm is True if:
            no samples in the threshhold period have been received, or 
            if wind avg / wind gust or precipitation in the list exceeds thresholds.
        Returns
            True if an alarm condition is met, False otherwise.
        """
        if not self.weather_enabled:
            return False

        if self.weather_list is None:
            self.weather_list = []
            self.created_dt = datetime.now(timezone.utc) 

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.threshold_timeout)

        wind_speeds = [wd.wind_speed for wd in self.weather_list if wd.wind_speed is not None and wd.obs_time >= cutoff]
        precipitations = [wd.precipitation for wd in self.weather_list if wd.precipitation is not None and wd.obs_time >= cutoff]

        avg_wind = sum(wind_speeds) / len(wind_speeds) if wind_speeds else 0.0
        max_wind = max(wind_speeds) if wind_speeds else 0.0
        gust_count = sum(1 for wd in self.weather_list if wd.wind_speed is not None and wd.wind_speed > self.threshold_wind_gust and wd.obs_time >= cutoff)
        avg_precip = sum(precipitations) / len(precipitations) if precipitations else 0.0

        logger.debug(f"WeatherStationList with {len(self.weather_list)} samples: avg_wind_speed={avg_wind:.2f} m/s, avg_precipitation={avg_precip:.2f} mm, thresholds: threshold_wind_gust={self.threshold_wind_gust:.2f} m/s, threshold_wind_avg={self.threshold_wind_avg:.2f} m/s, threshold_precipitation={self.threshold_precipitation:.2f} mm")
        
        if len(self.weather_list) == 0:

            # Ignore alarm if the list was created less than threshold_timeout seconds ago, to avoid false alarms on startup before any data has been received
            if (now - self.created_dt).total_seconds() < self.threshold_timeout:
                logger.debug("WeatherStationList alarm check: no recent weather data received but list is still within startup grace period, ignoring alarm.")
                return False

            logger.warning("WeatherStationList alarm triggered: no recent weather data received within threshold timeout period.")
            return True
                
        if avg_wind > self.threshold_wind_avg:
            logger.warning(f"WeatherStationList alarm triggered by average wind speed: {avg_wind:.2f} m/s exceeds threshold of {self.threshold_wind_avg:.2f} m/s")
            return True
      
        if gust_count > self.threshold_wind_count:
            logger.warning(f"WeatherStationList alarm triggered by wind gust count: {gust_count} exceeding threshold of {self.threshold_wind_count} samples above gust threshold of {self.threshold_wind_gust:.2f} m/s")
            return True
        elif gust_count > 0:
            logger.warning(f"WeatherStationList registered {gust_count} wind gust samples exceeding gust threshold of {self.threshold_wind_gust:.2f} m/s, but below count threshold of {self.threshold_wind_count} samples to trigger alarm.")
        
        if avg_precip > self.threshold_precipitation:
            logger.warning(f"WeatherStationList alarm triggered by average precipitation: {avg_precip:.2f} mm exceeds threshold of {self.threshold_precipitation:.2f} mm")
            return True
        return False

    def append(self, weather_data: WeatherData):
        """
        Add a WeatherData sample to the list
        """
        if not hasattr(weather_data, 'ws_id') or weather_data.ws_id is None:
            raise ValueError("WeatherData must have a ws_id attribute.")
        if not hasattr(weather_data, 'obs_time') or weather_data.obs_time is None:
            raise ValueError("WeatherData must have an obs_time attribute.")

        self.weather_list.append(weather_data)
        
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.threshold_timeout)
        self.weather_list = [wd for wd in self.weather_list if wd.obs_time >= cutoff]

        self.last_update = datetime.now(timezone.utc)

class WeatherStationModel(BaseModel):
    """A class representing the weather station model."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WeatherStationModel"),
        "id": And(str, lambda v: isinstance(v, str)),
        "app": And(AppModel, lambda v: isinstance(v, AppModel)),
        "tm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "dm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "sim_mode": And(str, lambda v: v in ["off", "calm", "windy", "stormy"]),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "WeatherStationModel",
            "app": AppModel(
                app_name="ws",
                app_running=False,
                num_processors=0,
                queue_size=0,
                interfaces=[],
                processors=[],
                health=HealthState.UNKNOWN,
                last_update=datetime.now(timezone.utc),
            ),
            "id": "<undefined>",
            "tm_connected": CommunicationStatus.NOT_ESTABLISHED,
            "dm_connected": CommunicationStatus.NOT_ESTABLISHED,
            "sim_mode": "off",
            "last_update": datetime.now(timezone.utc)
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

if __name__ == "__main__":

    import pprint

    print("*"*50)
    print("Testing Weather Data")
    print("*"*50)

    weather = WeatherData(ws_id="ws001", obs_time=datetime.now(timezone.utc), temperature=25.0, humidity=60.0)
    print(weather) 

    print("*"*50)
    print("Testing Weather Data List and append method")
    print("*"*50)

    weather_list = WeatherStationList()
    weather_list.save_to_disk()  # Test saving to disk with empty list

    weather_list.append(weather)
    print(weather_list)

    print("*"*50)
    print("Testing Weather Data List and append duplicate ws_id")
    print("*"*50)

    weather_updated = WeatherData(ws_id="ws001", obs_time=datetime.now(timezone.utc), temperature=26.0, humidity=55.0)
    weather_list.append(weather_updated)
    print(weather_list)

    print("*"*50)
    print("Testing Weather Data List and append different ws_id")
    print("*"*50)

    weather_new = WeatherData(ws_id="ws002", obs_time=datetime.now(timezone.utc), temperature=22.0, humidity=65.0)
    weather_list.append(weather_new)
    print(weather_list)

    ws001 = WeatherStationModel(id="ws001")
    pprint.pprint(ws001.to_dict())
