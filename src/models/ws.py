import enum
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
import math
from schema import Schema, And, Or, Use, SchemaError

from models.app import AppModel
from models.base import BaseModel
from models.comms import CommunicationStatus
from models.health import HealthState
from util.xbase import XSoftwareFailure

logger = logging.getLogger(__name__)

class WeatherStationDriverType(enum.IntEnum):
    ADS1115 = 1           # Anemometer driver based on the ADS1115 ADC
    MODBUS = 2            # Weather station driver based on Modbus RTU over RS485/USB
    UNKNOWN = 3

# Backwards-compatible alias for callers that import DriverType from models.ws.
DriverType = WeatherStationDriverType

class WeatherData(BaseModel):
    """A class representing weather data at a specific weather station (and location) and time."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WeatherData"),
        "ws_id": And(str, lambda v: isinstance(v, str)),                            # Weather station ID

        "obs_time": And(datetime, lambda v: isinstance(v, datetime)),               # Timestamp when the weather data was measured
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

class WeatherSummary(BaseModel):
    """A compact rolling weather summary for a single station."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WeatherSummary"),
        "ws_id": And(str, lambda v: isinstance(v, str)),
        "sample_secs": And(int, lambda v: v >= 0),
        "sample_count": And(int, lambda v: v >= 0),
        "wind_avg": And(float, lambda v: v >= 0.0),
        "wind_rms": And(float, lambda v: v >= 0.0),
        "wind_max": And(float, lambda v: v >= 0.0),
        "last_sample_time": Or(None, And(datetime, lambda v: isinstance(v, datetime))),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        defaults = {
            "_type": "WeatherSummary",
            "ws_id": "<undefined>",
            "sample_secs": 0,
            "sample_count": 0,
            "wind_avg": 0.0,
            "wind_rms": 0.0,
            "wind_max": 0.0,
            "last_sample_time": None,
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def __str__(self):
        return (
            f"WeatherSummary(ws_id={self.ws_id}, sample_secs={self.sample_secs}, sample_count={self.sample_count}, "
            f"wind_avg={self.wind_avg:.3f}, wind_rms={self.wind_rms:.3f}, wind_max={self.wind_max:.3f}, "
            f"last_sample_time={self.last_sample_time.isoformat() if self.last_sample_time else None}, "
            f"last_update={self.last_update.isoformat()})"
        )

class WeatherStation(BaseModel):
    """A class representing a weather station."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WeatherStation"),
        "ws_id": And(str, lambda v: isinstance(v, str)),                         # Weather station ID
        "location": And(str, lambda v: isinstance(v, str)),                      # Weather station location description
        "latitude": Or(None, And(float, lambda v: -90 <= v <= 90)),              # Latitude in degrees
        "longitude": Or(None, And(float, lambda v: -180 <= v <= 180)),           # Longitude in degrees
        "elevation": Or(None, And(float, lambda v: v >= 0)),                     # Elevation in meters
        "last_update": Or(None, And(datetime, lambda v: isinstance(v, datetime))),  # Timestamp when the weather station data was last updated
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):

        # Default values
        defaults = {
            "_type": "WeatherStation",
            "ws_id": "<undefined>",
            "location": None,
            "latitude": None,
            "longitude": None,
            "elevation": None,
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

class WeatherStationList(BaseModel):
    """A class representing a list of weather data from one or more weather stations."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WeatherStationList"),
        "list_id": And(str, lambda v: isinstance(v, str)),                 # Weather Station List identifier e.g. "active"   
        "weather_enabled": And(bool, lambda v: isinstance(v, bool)),       # Flag to enable or disable weather monitoring and alarm processing for the dishes
        "weather_stations": And(list, lambda v: isinstance(v, list)),      # List of WeatherStation objects
        "weather_data": And(list, lambda v: isinstance(v, list)),          # List of WeatherData objects
        "weather_summaries": And(list, lambda v: isinstance(v, list)),     # Compact rolling weather summaries, one per weather station
        "threshold_timeout": And(int, lambda v: v >= 0),                   # Maximum age of weather data in seconds to keep in the list
        "retention_period": And(int, lambda v: v >= 0),                    # Maximum age of weather data in seconds to retain locally
        "summary_window": And(int, lambda v: v >= 0),                      # Rolling window in seconds for compact weather summaries
        "threshold_wind_avg": And(float, lambda v: v >= 0),                # Threshold for high wind avg in m/s to trigger an alarm
        "threshold_wind_gust": And(float, lambda v: v >= 0),               # Threshold for high wind gust in m/s to trigger an alarm
        "threshold_wind_count": And(int, lambda v: v >= 0),                # Number of wind gust samples above threshold to trigger an alarm
        "threshold_precipitation": And(float, lambda v: v >= 0),           # Threshold for heavy precipitation in mm to trigger an alarm
        "trigger_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),  # Timestamp when the last alarm was triggered
        "last_mth_alarm_count": And(int, lambda v: v >= 0),                # Number of alarm activations over the trailing month
        "last_mth_alarm_activated": And(float, lambda v: v >= 0),          # Minutes alarm was active over the trailing month
        "last_mth_alarm_deactivated": And(float, lambda v: v >= 0),        # Minutes alarm was clear over the trailing month
        "last_mth_alarm_mtta": And(float, lambda v: v >= 0),               # Mean minutes between alarm activations over the trailing month
        "last_mth_alarm_mttr": And(float, lambda v: v >= 0),               # Mean minutes to recovery over the trailing month
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
            "weather_stations": [],  
            "weather_data": [],
            "weather_summaries": [],
            "threshold_timeout": 30,                    # Maximum age of weather data in seconds to keep in the list
            "retention_period": 300,                   # Retain 5 minutes of weather data locally for summaries and displays
            "summary_window": 60,                      # Summaries cover the last 60 seconds by default
            "threshold_wind_gust": 30.0,                # Threshold for high wind gust in m/s to trigger an alarm
            "threshold_wind_avg": 20.0,                 # Threshold for high wind avg in m/s to trigger an alarm
            "threshold_precipitation": 10.0,            # Threshold for heavy precipitation in mm to trigger an alarm
            "threshold_wind_count": 3,                  # Number of wind samples above threshold to trigger an alarm
            "trigger_dt": None,                         # Timestamp when the last alarm was triggered
            "last_mth_alarm_count": 0,
            "last_mth_alarm_activated": 0.0,
            "last_mth_alarm_deactivated": 0.0,
            "last_mth_alarm_mtta": 0.0,
            "last_mth_alarm_mttr": 0.0,
            "created_dt": datetime.now(timezone.utc),
            "last_update": datetime.now(timezone.utc),
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def __str__(self):
        summary_str = ",\n\n  ".join(str(ws) for ws in self.weather_summaries)
        return (
            f"WeatherStationList (list_id={self.list_id}, len={len(self.weather_data)}, "
            f"summaries={len(self.weather_summaries)}, threshold_timeout={self.threshold_timeout}, "
            f"retention_period={self.retention_period}, summary_window={self.summary_window}, "
            f"threshold_wind_gust={self.threshold_wind_gust}, threshold_wind_avg={self.threshold_wind_avg}, "
            f"threshold_precipitation={self.threshold_precipitation}, threshold_wind_count={self.threshold_wind_count}, "
            f"last_mth_alarm_count={self.last_mth_alarm_count}, last_mth_alarm_activated={self.last_mth_alarm_activated}, "
            f"last_mth_alarm_deactivated={self.last_mth_alarm_deactivated}, last_mth_alarm_mtta={self.last_mth_alarm_mtta}, "
            f"last_mth_alarm_mttr={self.last_mth_alarm_mttr}, last_update={self.last_update.isoformat()}, "
            f"trigger_dt={self.trigger_dt.isoformat() if self.trigger_dt else None}): [\n  {summary_str}\n]"
        )

    def is_ws_monitoring_enabled(self) -> bool:
        """
        Returns True if weather monitoring and alarm processing is enabled for the dishes, False otherwise.
        """
        return self.weather_enabled

    def get_station(self, ws_id: str) -> WeatherStation:
        """Return the WeatherStation object for the given station id, or None if not found."""
        for ws in self.weather_stations:
            if ws.ws_id == ws_id:
                return ws
        return None

    def get_station_ids(self) -> List[str]:
        """Return sorted weather-station ids currently present in the rolling weather list."""
        station_ids = {wd.ws_id for wd in self.weather_data if getattr(wd, "ws_id", None) is not None}
        station_ids.update({ws.ws_id for ws in self.weather_stations if getattr(ws, "ws_id", None) is not None})
        return sorted(station_ids)

    def get_station_weather(self, ws_id: str, now: datetime = None, window_sec: int = None) -> List[WeatherData]:
        """Return recent samples for a single weather station within the requested window."""
        if ws_id is None:
            return []

        now = datetime.now(timezone.utc) if now is None else now
        window_sec = self.threshold_timeout if window_sec is None else window_sec
        cutoff = now - timedelta(seconds=window_sec)

        return sorted(
            [
                wd for wd in self.weather_data
                if wd.ws_id == ws_id and wd.obs_time is not None and wd.obs_time >= cutoff
            ],
            key=lambda wd: wd.obs_time,
        )

    def get_alarm_metrics(self, ws_id: str = None, now: datetime = None) -> Dict[str, Any]:
        """Compute weather-alarm comparison metrics for one station or for the full rolling list."""
        now = datetime.now(timezone.utc) if now is None else now
        cutoff = now - timedelta(seconds=self.threshold_timeout)

        if ws_id is None:
            station_samples = sorted(
                [
                    wd for wd in self.weather_data
                    if wd.obs_time is not None and wd.obs_time >= cutoff
                ],
                key=lambda wd: wd.obs_time,
            )
        else:
            station_samples = self.get_station_weather(ws_id=ws_id, now=now)

        wind_speeds = [wd.wind_speed for wd in station_samples if wd.wind_speed is not None]
        precipitations = [wd.precipitation for wd in station_samples if wd.precipitation is not None]

        latest_sample = station_samples[-1] if station_samples else None
        latest_age_sec = (now - latest_sample.obs_time).total_seconds() if latest_sample is not None else None

        avg_wind = sum(wind_speeds) / len(wind_speeds) if wind_speeds else 0.0
        max_wind = max(wind_speeds) if wind_speeds else 0.0
        gust_count = sum(1 for speed in wind_speeds if speed > self.threshold_wind_gust)
        avg_precip = sum(precipitations) / len(precipitations) if precipitations else 0.0

        if ws_id is None:
            startup_age_sec = (now - self.created_dt).total_seconds()
        else:
            ws_all_samples = [wd for wd in self.weather_data if wd.ws_id == ws_id and wd.obs_time is not None]
            if ws_all_samples:
                first_station_time = min(wd.obs_time for wd in ws_all_samples)
                startup_age_sec = (now - first_station_time).total_seconds()
            else:
                startup_age_sec = (now - self.created_dt).total_seconds()

        timeout_triggered = len(station_samples) == 0 and startup_age_sec >= self.threshold_timeout
        wind_avg_triggered = avg_wind > self.threshold_wind_avg
        gust_triggered = max_wind > self.threshold_wind_gust
        gust_count_triggered = gust_count > self.threshold_wind_count
        precipitation_triggered = avg_precip > self.threshold_precipitation
        alarm_triggered = any([
            timeout_triggered,
            wind_avg_triggered,
            gust_count_triggered,
            precipitation_triggered,
        ])

        return {
            "sample_count": len(station_samples),
            "latest_sample": latest_sample,
            "latest_age_sec": latest_age_sec,
            "avg_wind": avg_wind,
            "max_wind": max_wind,
            "gust_count": gust_count,
            "avg_precipitation": avg_precip,
            "timeout_triggered": timeout_triggered,
            "wind_avg_triggered": wind_avg_triggered,
            "gust_triggered": gust_triggered,
            "gust_count_triggered": gust_count_triggered,
            "precipitation_triggered": precipitation_triggered,
            "alarm_triggered": alarm_triggered,
        }

    def get_weather_summary(self, ws_id: str, sample_secs: int = None, now: datetime = None) -> WeatherSummary:
        """Return a compact rolling weather summary for a single station."""
        now = datetime.now(timezone.utc) if now is None else now
        sample_secs = self.summary_window if sample_secs is None else sample_secs
        station_samples = self.get_station_weather(ws_id=ws_id, now=now, window_sec=sample_secs)

        wind_speeds = [wd.wind_speed for wd in station_samples if wd.wind_speed is not None]
        latest_sample = station_samples[-1] if station_samples else None

        wind_avg = (sum(wind_speeds) / len(wind_speeds)) if wind_speeds else 0.0
        wind_rms = math.sqrt(sum(speed * speed for speed in wind_speeds) / len(wind_speeds)) if wind_speeds else 0.0
        wind_max = max(wind_speeds) if wind_speeds else 0.0

        return WeatherSummary(
            ws_id=ws_id,
            sample_secs=sample_secs,
            sample_count=len(wind_speeds),
            wind_avg=float(wind_avg),
            wind_rms=float(wind_rms),
            wind_max=float(wind_max),
            last_sample_time=latest_sample.obs_time if latest_sample is not None else None,
            last_update=now,
        )

    def get_weather_summaries(self, sample_secs: int = None, now: datetime = None) -> List[WeatherSummary]:
        """Return compact rolling weather summaries for all known stations."""
        now = datetime.now(timezone.utc) if now is None else now
        sample_secs = self.summary_window if sample_secs is None else sample_secs
        return [self.get_weather_summary(ws_id=ws_id, sample_secs=sample_secs, now=now) for ws_id in self.get_station_ids()]

    def get_summary_by_ws_id(self, ws_id: str) -> WeatherSummary:
        """Retrieve a compact weather summary by station id."""
        for summary in self.weather_summaries:
            if summary.ws_id == ws_id:
                return summary
        return None

    def format_alarm_metrics(self, ws_id: str = None, now: datetime = None) -> str:
        """Return alarm metrics as a stable key=value string for logging."""
        metrics = self.get_alarm_metrics(ws_id=ws_id, now=now)
        latest_sample = metrics.get("latest_sample")
        latest_ws_id = getattr(latest_sample, "ws_id", None) if latest_sample is not None else None
        latest_obs_time = latest_sample.obs_time.isoformat() if latest_sample is not None and latest_sample.obs_time is not None else None

        parts = [
            f"ws_id={ws_id if ws_id is not None else 'all'}",
            f"sample_count={metrics.get('sample_count', 0)}",
            f"latest_ws_id={latest_ws_id}",
            f"latest_obs_time={latest_obs_time}",
            f"latest_age_sec={metrics.get('latest_age_sec')}",
            f"avg_wind={metrics.get('avg_wind', 0.0):.3f}",
            f"max_wind={metrics.get('max_wind', 0.0):.3f}",
            f"gust_count={metrics.get('gust_count', 0)}",
            f"avg_precipitation={metrics.get('avg_precipitation', 0.0):.3f}",
            f"threshold_timeout={self.threshold_timeout}",
            f"threshold_wind_avg={self.threshold_wind_avg:.3f}",
            f"threshold_wind_gust={self.threshold_wind_gust:.3f}",
            f"threshold_wind_count={self.threshold_wind_count}",
            f"threshold_precipitation={self.threshold_precipitation:.3f}",
            f"timeout_triggered={metrics.get('timeout_triggered', False)}",
            f"wind_avg_triggered={metrics.get('wind_avg_triggered', False)}",
            f"gust_triggered={metrics.get('gust_triggered', False)}",
            f"gust_count_triggered={metrics.get('gust_count_triggered', False)}",
            f"precipitation_triggered={metrics.get('precipitation_triggered', False)}",
            f"alarm_triggered={metrics.get('alarm_triggered', False)}",
        ]
        return "\n".join(f"  {part}" for part in parts)
    
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

        if self.weather_data is None:
            self.weather_data = []
            self.created_dt = datetime.now(timezone.utc) 

        metrics = self.get_alarm_metrics()

        logger.debug(
            f"WeatherStationList with {metrics['sample_count']} samples: "
            f"avg_wind_speed={metrics['avg_wind']:.2f} m/s, "
            f"avg_precipitation={metrics['avg_precipitation']:.2f} mm, "
            f"thresholds: threshold_wind_gust={self.threshold_wind_gust:.2f} m/s, "
            f"threshold_wind_avg={self.threshold_wind_avg:.2f} m/s, "
            f"threshold_precipitation={self.threshold_precipitation:.2f} mm"
        )
        
        if metrics["sample_count"] == 0 and not metrics["timeout_triggered"]:
            logger.debug("WeatherStationList alarm check: no weather data received but list is still within startup grace period, ignoring alarm.")
            return False

        if metrics["timeout_triggered"]:
            logger.warning(f"WeatherStationList alarm triggered: no weather data received within threshold timeout of {self.threshold_timeout} seconds.")
            return True
                
        if metrics["wind_avg_triggered"]:
            logger.warning(
                f"WeatherStationList alarm triggered by average wind speed: "
                f"{metrics['avg_wind']:.2f} m/s exceeds threshold of {self.threshold_wind_avg:.2f} m/s"
            )
            return True
      
        if metrics["gust_count_triggered"]:
            logger.warning(
                f"WeatherStationList alarm triggered by wind gust count: {metrics['gust_count']} "
                f"exceeding threshold of {self.threshold_wind_count} samples above gust threshold of {self.threshold_wind_gust:.2f} m/s"
            )
            return True
        elif metrics["gust_triggered"]:
            logger.warning(
                f"WeatherStationList registered {metrics['gust_count']} wind gust samples exceeding gust threshold of "
                f"{self.threshold_wind_gust:.2f} m/s, but not exceeding count threshold of {self.threshold_wind_count} samples to trigger alarm."
            )
        
        if metrics["precipitation_triggered"]:
            logger.warning(
                f"WeatherStationList alarm triggered by average precipitation: "
                f"{metrics['avg_precipitation']:.2f} mm exceeds threshold of {self.threshold_precipitation:.2f} mm"
            )
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

        self.weather_data.append(weather_data)
        
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.retention_period)
        self.weather_data = [wd for wd in self.weather_data if wd.obs_time >= cutoff]
        self.weather_summaries = self.get_weather_summaries(now=now)

        self.last_update = now

class WeatherStationModel(BaseModel):
    """A class representing the weather station model."""

    schema = Schema({
        "_type": And(str, lambda v: v == "WeatherStationModel"),
        "id": And(str, lambda v: isinstance(v, str)),
        "app": And(AppModel, lambda v: isinstance(v, AppModel)),
        "tm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "dm_connected": And(CommunicationStatus, lambda v: isinstance(v, CommunicationStatus)),
        "sim_mode": And(str, lambda v: v in ["off", "calm", "windy", "stormy"]),
        "driver_type": And(WeatherStationDriverType, lambda v: isinstance(v, WeatherStationDriverType)),  # Weather station driver implementation
        "driver_config": Or(None, lambda v: v is None or isinstance(v, BaseModel)),
        "driver_poll_period": Or(None, And(int, lambda v: v > 0)),                  # Driver poll period in milliseconds
        "driver_failures": And(int, lambda v: v >= 0),                              # Count of consecutive driver read failures
        "last_err_msg": Or(None, And(str, lambda v: isinstance(v, str))),           # Last weather station error message
        "last_err_dt": Or(None, And(datetime, lambda v: isinstance(v, datetime))),  # Last weather station error datetime
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
            "driver_type": WeatherStationDriverType.UNKNOWN,
            "driver_config": None,
            "driver_poll_period": 1000,
            "driver_failures": 0,
            "last_err_msg": None,
            "last_err_dt": None,
            "last_update": datetime.now(timezone.utc)
        }

        # Apply defaults if not provided in kwargs
        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def increment_failures(self):
        """Increment the consecutive weather station driver failure count."""
        self.driver_failures += 1
        self.last_update = datetime.now(timezone.utc)

    def reset_failures(self):
        """Reset the consecutive weather station driver failure count."""
        self.driver_failures = 0
        self.last_update = datetime.now(timezone.utc)

    def set_last_err(self, err_msg: str, err_dt: datetime = None) -> str:
        """Record the latest weather station driver error."""
        self.last_err_msg = err_msg
        self.last_err_dt = err_dt if err_dt is not None else datetime.now(timezone.utc)
        self.last_update = self.last_err_dt
        return err_msg

if __name__ == "__main__":

    import pprint

    print("*"*50)
    print("Testing Weather Data")
    print("*"*50)

    weather = WeatherData(ws_id="ws001", obs_time=datetime.now(timezone.utc), temperature=25.0, humidity=60.0)
    print(weather) 

    print("*"*50)
    print("Testing Weather Station")
    print("*"*50)

    weather_station = WeatherStation(ws_id="ws001", location="Test Location", latitude=40.0, longitude=-105.0, elevation=1600.0)
    pprint.pprint(weather_station.to_dict())

    print("*"*50)
    print("Testing Weather Data List and append method")
    print("*"*50)

    weather_data = WeatherStationList(weather_stations=[weather_station])
    weather_data.save_to_disk()  # Test saving to disk with empty list

    weather_data.append(weather)
    print(weather_data)

    print("*"*50)
    print("Testing Weather Data List and append duplicate ws_id")
    print("*"*50)

    weather_updated = WeatherData(ws_id="ws001", obs_time=datetime.now(timezone.utc), temperature=26.0, humidity=55.0)
    weather_data.append(weather_updated)
    print(weather_data)

    print("*"*50)
    print("Testing Weather Data List and append different ws_id")
    print("*"*50)

    weather_new = WeatherData(ws_id="ws002", obs_time=datetime.now(timezone.utc), temperature=22.0, humidity=65.0)
    weather_data.append(weather_new)
    print(weather_data)

    ws001 = WeatherStationModel(id="ws001")
    pprint.pprint(ws001.to_dict())
