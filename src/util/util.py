from __future__ import annotations
from astropy import units as u
from astropy.coordinates import SpectralCoord, EarthLocation, SkyCoord, AltAz, ICRS
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np
import re
import scipy.constants as const

import logging
logger = logging.getLogger(__name__)

f_e = 1420.405751768 * u.MHz  # Rest frequency of HI hyperfine transition
speed_of_light = const.speed_of_light * (u.meter / u.second)

def unpack_result(result) -> tuple:
        """ Unpacks a tuple result containing status, message, value, and payload.
        """
        if isinstance(result, tuple) and len(result) == 4:
            status, message, value, payload = result
        elif isinstance(result, tuple) and len(result) == 3:
            status, message, value, payload = result, None
        elif isinstance(result, tuple) and len(result) == 2:
            status, message, value, payload = result, None, None
        elif isinstance(result, tuple) and len(result) == 1:
            status, message, value, payload = result, None, None, None
        else:
            status, message, value, payload = "error", "Invalid result format", None, None

        return status, message, value, payload

def delay_till_hour() -> float:
    """ Calculate the delay in milliseconds until the start of the next hour.
    """
    now = datetime.now()
    next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    delay = (next_hour - now).total_seconds() * 1000  # in milliseconds
    return delay

def dict_diff(old_dict, new_dict):
    """
    Compare two dictionaries and determine which keys are:
      - added
      - removed
      - updated (same key exists, but value changed)

    Handles old_dict = None gracefully.
    """

    # If old_dict is None, treat it as empty
    if old_dict is None:
        old_dict = {}

    if new_dict is None:
        new_dict = {}

    # Compute key sets
    old_keys = set(old_dict.keys())
    new_keys = set(new_dict.keys())

    added = new_keys - old_keys
    removed = old_keys - new_keys

    updated = {
        key for key in (old_keys & new_keys)
        if old_dict[key] != new_dict[key]
    }

    return {
        "added": {key: new_dict[key] for key in added},
        "removed": {key: old_dict[key] for key in removed},
        "updated": {
            key: {"old": old_dict[key], "new": new_dict[key]}
            for key in updated
        }
    }

def dict_flatten(d, parent_key="", sep="."):
    """ Flatten a nested dictionary into a single level dictionary with compound keys.
        For example, {'a': {'b': 1}} becomes {'a.b': 1}.
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k

        if isinstance(v, dict):
            items.extend(dict_flatten(v, new_key, sep=sep).items())
        elif isinstance(v, list):
            for i, item in enumerate(v):
                items.extend(
                    dict_flatten({f"{new_key}[{i}]": item}, sep=sep).items()
                )
        else:
            items.append((new_key, v))

    return dict(items)

import re

def dict_unflatten(flat_dict) -> dict:
    """
    Convert a flattened dict with dot notation and [index] lists
    back into a nested dictionary/list structure.
    String values that look like numbers, booleans, None, [] or {}
    are coerced to their native Python types.
    """

    def _coerce(value):
        """Attempt to coerce a string value to its native Python type."""
        if not isinstance(value, str):
            return value

        s = value.strip()

        # Empty list / dict
        if s == "[]":
            return []
        if s == "{}":
            return {}

        # Boolean
        if s.lower() == "true":
            return True
        if s.lower() == "false":
            return False

        # None
        if s.lower() in ("none", "null", ""):
            return None

        # Integer
        try:
            int_val = int(s)
            if str(int_val) == s:
                return int_val
        except ValueError:
            pass

        # Float
        try:
            return float(s)
        except ValueError:
            pass

        return value

    result = {}

    for flat_key, value in flat_dict.items():
        tokens = re.findall(r'[^.\[\]]+|\[\d+\]', flat_key)
        current = result

        for i, token in enumerate(tokens):
            is_last = i == len(tokens) - 1

            if token.startswith('['):
                index = int(token[1:-1])

                if not isinstance(current, list):
                    raise TypeError(f"Unexpected list index in key: {flat_key}")

                while len(current) <= index:
                    current.append({})

                if is_last:
                    current[index] = _coerce(value)
                else:
                    if not isinstance(current[index], (dict, list)):
                        current[index] = {}
                    current = current[index]

            else:
                next_token = tokens[i + 1] if not is_last else None

                if is_last:
                    current[token] = _coerce(value)
                else:
                    if next_token and next_token.startswith('['):
                        if token not in current:
                            current[token] = []
                    else:
                        if token not in current:
                            current[token] = {}

                    current = current[token]

    return result
    
def gen_file_prefix(
    dt:datetime,
    entity_id:str,
    gain:float,
    duration:int,
    sample_rate:float,
    center_freq:float,
    channels:int,
    instance_id:str = None,
    scan_type: ScanType = None,
    filetype: str = None) -> str:

    """ Generate a filename prefix based on metadata parameters.
        :param dt: The datetime object representing the entity start time e.g. scan start
        :param entity_id: The entity identifier e.g. dig_id 
        :param gain: The gain setting e.g. 39.6 dB
        :param duration: The duration in seconds
        :param sample_rate: The sample rate e.g. 2.4e6 Hz
        :param center_freq: The center frequency e.g. 1.42e9 Hz
        :param channels: The number of channels e.g. 1024
        :param instance_id: The instance ID number for multiple files per entity e.g. Obs ID
        :param scan_type: The type of scan (e.g., "sky", "load", "tsys", "gain")
        :param filetype: The type of file being generated (e.g., "raw", "spr", "cal", "meta", "mpr")
        :returns: A string representing the file prefix
    """
    prefix = (
        (str(instance_id) if instance_id is not None else '') +
        (dt.strftime("%Y-%m-%dT%H%M%S") if instance_id is None and dt is not None else '') +
        ("-" + str(entity_id) if entity_id is not None else '') +
        ("-g" + str(float(gain)) if gain is not None else '') +
        ("-du" + str(duration) if duration is not None else '') +
        ("-bw" + str(round(sample_rate/1e6,2)) if sample_rate is not None else '') +
        ("-cf" + str(round(center_freq/1e6,2)) if center_freq is not None else '') +
        ("-ch" + str(channels) if channels is not None else '') +
        ("-" + scan_type.name if scan_type is not None else '') +
        ("-" + filetype if filetype is not None else '')
    )
    # Remove characters not allowed in filenames (e.g., \\/:*?"<>|)
    prefix = re.sub(r'[:\\/*?"<>|]', '', prefix).lower()
    return prefix

def find_json_object_end(data:bytes) -> int:
    """ Finds the end index of the first complete JSON object in the byte stream.
        Returns the index of the byte after the closing brace of the JSON object,
        or -1 if no complete JSON object is found.
    """
    # data is bytes
    open_braces = 0
    in_string = False
    escape = False
    for i, b in enumerate(data):
        c = chr(b)
        if c == '"' and not escape:
            in_string = not in_string
        if not in_string:
            if c == '{':
                open_braces += 1
            elif c == '}':
                open_braces -= 1
                if open_braces == 0:
                    return i + 1  # End index is after this brace
        escape = (c == '\\' and not escape)
    return -1  # Not found

def velocity2LSR(coord: SkyCoord, observing_location: EarthLocation, observing_time: Time):
    """ Calculates the velocity adjustment for the Local Standard of Rest 
        coord:  Expected in the ICRS frame of reference i.e. RA DEC
        observing_location: EarthLocation of observer
        observing_time: Observing Time

        Validation by comparison to the Greenbank calculator here:
        https://www.gb.nrao.edu/cgi-bin/radvelcalc.py?UTDate=2025%2F07%2F16&UTTime=17%3A40%3A00&RA=06%3A41%3A00&DEC=09%3A52%3A59
        Lat Long for GMB Telescope: 38.432994106420274, -79.8400693370822
        """

    # ITRS (International Terrestial Reference System) frame of reference (a geocentric system)
    itrs = observing_location.get_itrs(obstime=observing_time)
    frequency = SpectralCoord(
        f_e, 
        observer=itrs, 
        target=coord) #Shift expected from just local motion
    # Return a new SpectralCoord with the velocity of the observer altered, but not the position
    f_shifted = frequency.with_observer_stationary_relative_to('lsrk') #correct for kinematic local standard of rest
    f_shifted = f_shifted.to(u.GHz)
    v = -freq2vel(f_shifted, f_e)
    v_adj = v.to(u.km/u.second)
    return v_adj

def freq2vel(freq, rest=f_e):
    """
    Calculates velocity from measured frequency via doppler shift.
    
    :param freq: array of frequency quantities (including units)
    :param rest (optional): Rest frequency, defaults to 1.42 GHz
    :returns vel: velocity inferred by doppler shift
    """
    return ((rest - freq) * speed_of_light / freq).to(u.km/u.second)

def get_azimuth_distance(az1, az2):
    """ This function calculates the distance needed to move in azimuth between two angles. This takes into account that the 
    telescope may need to go 'the other way around' to reach some positions, i.e. the distance to travel can be much larger than the 
    simple angular distance. This assumes that both azimuth positions are valid, something which can be checked first with the can_reach function."""
    # Normalize angles to [0, 360) range
    az1_norm = az1 % 360
    az2_norm = az2 % 360
    
    # Calculate absolute difference
    diff = np.abs(az1_norm - az2_norm)
    
    # Return the shorter path around the circle
    return min(diff, 360 - diff)

def get_angular_distance(alt1_deg, az1_deg, alt2_deg, az2_deg) -> float:
    """ Calculate angular distance between two altaz positions in degrees.
        For altitude azimuth coordinates, the angular separation satisfies:
    
        cosθ = sin(a1) sin(a2)+cos(a1) cos(a2) cos(ΔAz)
        where a1 and a2 are the altitudes, and ΔAz is the difference in azimuths.
    """

    alt1 = np.deg2rad(alt1_deg)
    az1  = np.deg2rad(az1_deg)
    alt2 = np.deg2rad(alt2_deg)
    az2  = np.deg2rad(az2_deg)

    x1 = np.cos(alt1) * np.cos(az1)
    y1 = np.cos(alt1) * np.sin(az1)
    z1 = np.sin(alt1)

    x2 = np.cos(alt2) * np.cos(az2)
    y2 = np.cos(alt2) * np.sin(az2)
    z2 = np.sin(alt2)

    dot = x1*x2 + y1*y2 + z1*z2
    dot = np.clip(dot, -1.0, 1.0)  # numerically safe

    return np.rad2deg(np.arccos(dot))

# Runs tests using: pytest util/util.py -v
# -v for verbose output (or -vv or -vvv for more verbosity)
# -s to show print output

def test_azimuth_distance():
    assert get_azimuth_distance(10.0, 20.0) == 10.0
    assert get_azimuth_distance(350.0, 10.0) == 20.0
    assert get_azimuth_distance(180.0, 270.0) == 90.0
    assert get_azimuth_distance(0.0, 360.0) == 0.0
    assert get_azimuth_distance(-10.0, 360.0) == 10.0

def test_angular_distance():
    assert abs(get_angular_distance(45.0, 180.0, 45.0, 180.0) - 0.0) < 0.001
    assert abs(get_angular_distance(45.0, 180.0, 46.0, 180.0) - 1.0) < 0.001
    assert abs(get_angular_distance(45.0, 180.0, 45.0, 181.0) - 0.707) < 0.001
    assert abs(get_angular_distance(0.0, 0.0, 90.0, 0.0) - 90.0) < 0.001
    assert abs(get_angular_distance(90.0, 0.0, -90.0, 0.0) - 180.0) < 0.001  

def test_find_json_object_end_simple():
    test_data = b'{"key1": "value1"}{"key2": "value2"}'
    end_index = find_json_object_end(test_data)
    assert end_index == 18
    assert test_data[:end_index].decode('utf-8') == '{"key1": "value1"}'

def test_find_json_object_end_empty():
    test_data = b''
    end_index = find_json_object_end(test_data)
    assert end_index == -1

def test_find_json_object_end_invalid():
    test_data = b'{"key1": "value1", "key2": {"nestedKey": "nestedValue" "missingComma" "oops"}} extra data'
    end_index = find_json_object_end(test_data)
    assert end_index == 78

def test_find_json_object_end_with_strings():
    test_data = b'{"key1": "value with } brace", "key2": {"nestedKey": "nestedValue"}} extra data'
    end_index = find_json_object_end(test_data)
    assert end_index == 68
    assert test_data[:end_index].decode('utf-8') == '{"key1": "value with } brace", "key2": {"nestedKey": "nestedValue"}}'

def test_find_json_object_end_with_sdp_msg():
    test_data = b'{"api_version": "1.0", "payload_length": 38400000, "to": "sdp", "from": "dig", "api_call": {"msg_type": "adv", "action_code": "samples", "metadata": [{"property": "center_freq", "value": 1420400000.0}, {"property": "sample_rate", "value": 2400000.0}, {"property": "bandwidth", "value": 2000000.0}, {"property": "gain", "value": 40}, {"property": "timestamp", "value": "2025-09-20T12:30:18.035666+00:00"}]}}RRRRRR'
    end_index = find_json_object_end(test_data)
    assert end_index == 406
    assert test_data[:end_index].decode('utf-8') == '{"api_version": "1.0", "payload_length": 38400000, "to": "sdp", "from": "dig", "api_call": {"msg_type": "adv", "action_code": "samples", "metadata": [{"property": "center_freq", "value": 1420400000.0}, {"property": "sample_rate", "value": 2400000.0}, {"property": "bandwidth", "value": 2000000.0}, {"property": "gain", "value": 40}, {"property": "timestamp", "value": "2025-09-20T12:30:18.035666+00:00"}]}}'
    assert test_data[end_index:] == b'RRRRRR'