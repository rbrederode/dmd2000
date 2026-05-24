import logging
import enum
from typing import Any

from api import tm_dig, tm_sdp, tm_dm
from models import dsh
from models.base import BaseModel
from models.dig import BandpassFilterType
from models.target import TargetConfig

logger = logging.getLogger(__name__)

# Map Configuration items to attribute names
_config_to_property = {
    "load_active":      tm_dig.PROPERTY_LOAD_ACTIVE,
    "sample_rate":      tm_dig.PROPERTY_SAMPLE_RATE,
    "center_freq":      tm_dig.PROPERTY_CENTER_FREQ,
    "bandwidth":        tm_dig.PROPERTY_BANDWIDTH,
    "freq_correction":  tm_dig.PROPERTY_FREQ_CORRECTION,
    "gain":             tm_dig.PROPERTY_GAIN,
    "scanning":         tm_dig.PROPERTY_SCANNING,
    "spectral_resolution": tm_sdp.PROPERTY_SPECTRAL_RESOLUTION,
    "channels":         tm_sdp.PROPERTY_CHANNELS,
    "scan_duration":    tm_sdp.PROPERTY_SCAN_DURATION,
    "capability":       tm_dm.PROPERTY_CAPABILITY,
    "mode":             tm_dm.PROPERTY_MODE,
    "target":           tm_dm.PROPERTY_TARGET,
    "scan_config":      tm_sdp.PROPERTY_SCAN_CONFIG,
    "obs_reset":        tm_sdp.PROPERTY_OBS_RESET,
    "obs_complete":     tm_sdp.PROPERTY_OBS_COMPLETE,
}

def get_property_name_value(config_item: str, value) -> (str, Any):
    """ Get the property name for a given configuration item.
    """
    property = _config_to_property.get(config_item, None)

    # If property is found, map the value accordingly
    if property:
        if property == tm_dig.PROPERTY_LOAD_ACTIVE:

            if isinstance(value, bool):
                return property, value
            elif isinstance(value, str) and value.upper() in ["TRUE", "1", "YES", "ON"]:
                return property, True
            elif isinstance(value, str) and value.upper() in ["FALSE", "0", "NO", "OFF"]:
                return property, False
            else:
                logger.error(f"Telescope Manager map: invalid LOAD_ACTIVE value {value} for property {property}")
                return property, None

        elif property == tm_dig.PROPERTY_GAIN:
            if TargetConfig.is_auto_gain_token(value):
                return property, {"time_in_secs": 0.5}
            else:
                try:
                    return property, float(value)
                except ValueError:
                    logger.error(f"Telescope Manager map: invalid GAIN value {value} for property {property}")
                return property, None

        elif property == tm_dm.PROPERTY_CAPABILITY:

            if isinstance(value, str):
                value_upper = value.upper()
                if value_upper in dsh.Capability.__members__:
                    return property, dsh.Capability[value_upper].value
                else:
                    logger.error(f"Telescope Manager map: invalid CAPABILITY value {value} for property {property}")
                    return property, None
            elif isinstance(value, int):
                if value in [state.value for state in dsh.Capability]:
                    return property, value
                else:
                    logger.error(f"Telescope Manager map: invalid CAPABILITY integer value {value} for property {property}")
                    return property, None

        elif property == tm_dm.PROPERTY_MODE:

            if isinstance(value, str):
                value_upper = value.upper()
                if value_upper in dsh.DishMode.__members__:
                    return property, dsh.DishMode[value_upper].value
                else:
                    logger.error(f"Telescope Manager map: invalid MODE value {value} for property {property}")
                    return property, None
            elif isinstance(value, int):
                if value in [state.value for state in dsh.DishMode]:
                    return property, value
                else:
                    logger.error(f"Telescope Manager map: invalid MODE integer value {value} for property {property}")
                    return property, None

        elif property in [tm_dig.PROPERTY_SCANNING, tm_dm.PROPERTY_TARGET, tm_sdp.PROPERTY_SCAN_CONFIG]:
            if isinstance(value, bool):
                return property, value
            elif isinstance(value, dict):

                # Recursively map dictionary keys if needed (e.g., for nested configurations)
                mapped_dict = {}
                for k, v in value.items():
                    if property == tm_sdp.PROPERTY_SCAN_CONFIG and k == tm_dig.PROPERTY_GAIN and TargetConfig.is_auto_gain_token(v):
                        logger.info("Telescope Manager map: deferring scan_config gain update until auto gain is resolved.")
                        continue
                    mapped_key, mapped_value = get_property_name_value(k, v)
                    # Use the mapped key if found, otherwise keep the original key
                    mapped_dict[mapped_key if mapped_key is not None else k] = mapped_value
                return property, mapped_dict

            elif str(value).upper() in ["TRUE", "1", "YES", "ON"]:
                return property, True
            elif str(value).upper() in ["FALSE", "0", "NO", "OFF"]:
                return property, False
            elif property == tm_dm.PROPERTY_TARGET and value is None:
                return property, None
            else:
                logger.error(f"Telescope Manager map: invalid value {value} for property {property}")
                return property, None

        elif property in [
            tm_dig.PROPERTY_SAMPLE_RATE,
            tm_dig.PROPERTY_CENTER_FREQ,
            tm_dig.PROPERTY_BANDWIDTH,
            tm_dig.PROPERTY_FREQ_CORRECTION,
            tm_sdp.PROPERTY_SPECTRAL_RESOLUTION,
            tm_sdp.PROPERTY_CHANNELS,
            tm_sdp.PROPERTY_SCAN_DURATION
        ]:
            try:
                # These properties expect numeric values
                if property in [tm_dig.PROPERTY_FREQ_CORRECTION, tm_sdp.PROPERTY_SPECTRAL_RESOLUTION, tm_sdp.PROPERTY_CHANNELS, tm_sdp.PROPERTY_SCAN_DURATION]:
                    return property, int(value)
                else:
                    return property, float(value)
            except ValueError:
                logger.error(f"Telescope Manager map: invalid numeric value {value} for property {property}")
                return property, None
        elif property in [tm_sdp.PROPERTY_OBS_COMPLETE, tm_sdp.PROPERTY_OBS_RESET]:
            if isinstance(value, str):
                return property, value
            else:
                logger.error(f"Telescope Manager map: invalid OBS_COMPLETE value {value} for property {property}")
                return property, None
  
    return property, value

def get_method_name_value(config_item: str, value) -> (str, Any):
    """ Get the method name for a given configuration item.
    """
    if config_item is None or value is None:
        return None, None

    if config_item == "gain" and TargetConfig.is_auto_gain_token(value):
        return tm_dig.METHOD_SET_AUTO_GAIN, {"time_in_secs": 0.5}
    
    return None, None

if __name__ == "__main__":
    # Test the mapping
    test_items = [
        "Load State",
        "Sample Rate",
        "Center Frequency",
        "Bandwidth",
        "Frequency Correction",
        "Gain",
        "Unknown Item"
    ]

    for item in test_items:
        prop_name = get_property_name(item, 10)
        print(f"Config Item: {item} -> Property Name: {prop_name}")
