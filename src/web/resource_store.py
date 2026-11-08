"""File-backed digitiser and weather-station collection operations."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from dig.filters.sawbird import SAWbirdH1_BareBones
from models.base import BaseModel
from models.dig import BandpassFilterType, DigitiserModel
from models.ws import WeatherStation
from util.xbase import XAPIValidationFailed
from web.profile_store import ProfileFileStore, ProfileStoreError, _finite


def _merge(original, patch):
    result = deepcopy(original) if isinstance(original, dict) else {}
    for key, value in patch.items():
        result[key] = _merge(result.get(key), value) if isinstance(value, dict) else deepcopy(value)
    return result


class ProfileResourceStore(ProfileFileStore):
    def __init__(self, profile_dir):
        self.path = Path(profile_dir).expanduser() / self.filename

    def _read(self):
        try:
            content = self.path.read_bytes()
        except FileNotFoundError as error:
            raise ProfileStoreError(f'{self.filename} was not found in the active profile', 'config_not_found', 404) from error
        try:
            data = json.loads(content)
            if not isinstance(data, dict) or data.get('_type') != self.list_type or not isinstance(data.get(self.list_field), list):
                raise ValueError('Invalid collection')
            ids = []
            for raw in data[self.list_field]:
                ids.append(getattr(self.model.from_dict(raw), self.id_field))
            if len(ids) != len(set(ids)):
                raise ValueError('Duplicate identifiers')
        except (ValueError, TypeError, KeyError, XAPIValidationFailed) as error:
            raise ProfileStoreError(f'The profile {self.filename} is invalid', 'invalid_configuration', 500) from error
        return data, hashlib.sha256(content).hexdigest()

    def read(self, identifier=None):
        data, revision = self._read()
        if identifier is None:
            return [self.view(item) for item in data[self.list_field]], revision
        raw = next((item for item in data[self.list_field] if item[self.id_field] == identifier), None)
        if raw is None:
            raise ProfileStoreError(f'{self.label} {identifier} was not found', 'not_found', 404)
        return self.view(raw), revision

    def view(self, raw):
        data = self.model.from_dict(raw).to_dict()
        return {field: data[field] for field in self.fields}

    def delete(self, identifier, if_match=None):
        return self.delete_entry(identifier, self.list_field, self.id_field, if_match)

    def save(self, changes, identifier=None, if_match=None):
        if not isinstance(changes, dict) or not changes:
            raise ProfileStoreError('Send a non-empty configuration object')
        unknown = set(changes) - set(self.fields)
        if unknown:
            raise ProfileStoreError('Unknown or non-configuration fields: ' + ', '.join(sorted(unknown)))
        _finite(changes)
        with self._locked():
            data, revision = self._read()
            if if_match is not None and not if_match.contains(revision):
                raise ProfileStoreError('Configuration changed; reload before saving', 'version_conflict', 412)
            items = data[self.list_field]
            existing = next((item for item in items if item[self.id_field] == identifier), None) if identifier is not None else None
            if identifier is not None and existing is None:
                raise ProfileStoreError(f'{self.label} {identifier} was not found', 'not_found', 404)
            new_id = changes.get(self.id_field, identifier)
            if not isinstance(new_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', new_id):
                raise ProfileStoreError(f'{self.id_field} must contain 1–128 letters, digits, underscores or hyphens')
            if identifier is not None and new_id != identifier:
                raise ProfileStoreError('The identifier cannot be changed')
            if existing is None and any(item[self.id_field] == new_id for item in items):
                raise ProfileStoreError(f'{self.label} {new_id} already exists', 'already_exists', 409)
            try:
                raw = self.prepare(deepcopy(existing) if existing is not None else self.new(new_id), changes)
                raw['last_update'] = BaseModel._serialise(datetime.now(timezone.utc))
                result = self.view(raw)
            except (ValueError, TypeError, KeyError, XAPIValidationFailed) as error:
                raise ProfileStoreError(f'Invalid {self.label.lower()} configuration: {error}') from error
            if existing is None:
                items.append(raw)
            else:
                items[items.index(existing)] = raw
            data['last_update'] = raw['last_update']
            revision = self._write(data)
            return result, revision


class DigitiserProfileStore(ProfileResourceStore):
    filename, list_type, list_field = 'DigitiserList.json', 'DigitiserList', 'dig_list'
    model, id_field, label = DigitiserModel, 'dig_id', 'Digitiser'
    fields = ('dig_id', 'bpf_type', 'bpf_config', 'temp_type', 'temp_config', 'temp_max',
              'gain', 'sample_rate', 'bandwidth', 'center_freq', 'freq_correction', 'channels',
              'sdr_type', 'sdr_config', 'local_host', 'app_cmd_host', 'app_cmd_port')

    def new(self, identifier):
        return DigitiserModel(dig_id=identifier).to_dict()

    def view(self, raw):
        data = DigitiserModel.from_dict(raw).to_dict()
        result = {field: data[field] for field in self.fields if field in data}
        result['bpf_type'] = data['bpf_type']['value']
        if isinstance(result['bpf_config'], dict):
            result['bpf_config'] = {key: value for key, value in result['bpf_config'].items()
                                    if key not in ('_type', 'last_update')}
        result.update(local_host=(data['app']['arguments'] or {}).get('local_host'),
                      app_cmd_host=data['app']['app_cmd_host'], app_cmd_port=data['app']['app_cmd_port'])
        return result

    def prepare(self, raw, changes):
        previous = self.view(raw)
        for field, value in changes.items():
            if field in ('bpf_type', 'bpf_config', 'local_host', 'app_cmd_host', 'app_cmd_port'):
                continue
            if field in ('temp_type', 'sdr_type') and isinstance(value, str):
                value = value.lower()
                config_field = 'temp_config' if field == 'temp_type' else 'sdr_config'
                if value != previous[field]:
                    raw[config_field] = {}
            if field in ('temp_max', 'gain', 'sample_rate', 'bandwidth', 'center_freq'):
                if value is not None and type(value) not in (int, float):
                    raise ProfileStoreError(f'{field} must be numeric')
                value = float(value) if value is not None else None
            if field in ('freq_correction', 'channels') and type(value) is not int:
                raise ProfileStoreError(f'{field} must be an integer')
            if field in ('temp_config', 'sdr_config'):
                if not isinstance(value, dict):
                    raise ProfileStoreError(f'{field} must be an object')
                continue  # Apply after type changes, regardless of JSON key order.
            raw[field] = value
        for field in ('temp_config', 'sdr_config'):
            if field in changes:
                raw[field] = _merge(raw.get(field), changes[field])
        if 'bpf_type' in changes:
            value = changes['bpf_type']
            if not isinstance(value, str):
                raise ProfileStoreError('bpf_type must be an enum name')
            raw['bpf_type'] = BaseModel._serialise(BandpassFilterType[value.upper()])
            if value.upper() != previous['bpf_type']:
                raw['bpf_config'] = None
        if 'bpf_config' in changes:
            config = changes['bpf_config']
            if config is None:
                raw['bpf_config'] = None
            else:
                if raw['bpf_type']['value'] != 'SAWBIRD_H1_BAREBONES' or not isinstance(config, dict):
                    raise ProfileStoreError('bpf_config requires SAWBIRD_H1_BAREBONES and an object, or null')
                if set(config) - {'gpio_pin_power', 'gpio_pin_load'}:
                    raise ProfileStoreError('Unknown fields in bpf_config')
                if any(value is not None and type(value) is not int for value in config.values()):
                    raise ProfileStoreError('GPIO pins must be integers or null')
                merged = _merge(raw.get('bpf_config'), config)
                merged['_type'] = 'SAWbirdH1_BareBones'
                merged['last_update'] = BaseModel._serialise(datetime.now(timezone.utc))
                raw['bpf_config'] = SAWbirdH1_BareBones.from_dict(merged).to_dict()
        app = raw['app']
        if 'local_host' in changes:
            host = changes['local_host']
            if host is not None and (not isinstance(host, str) or not host.strip()):
                raise ProfileStoreError('local_host must be a non-empty string or null')
            app['arguments'] = dict(app.get('arguments') or {}, local_host=host)
        for field in ('app_cmd_host', 'app_cmd_port'):
            if field in changes:
                value = changes[field]
                if field == 'app_cmd_port' and value is not None and (type(value) is not int or not 1 <= value <= 65535):
                    raise ProfileStoreError('app_cmd_port must be an integer between 1 and 65535')
                if field == 'app_cmd_host' and value is not None and (not isinstance(value, str) or not value.strip()):
                    raise ProfileStoreError('app_cmd_host must be a non-empty string')
                app[field] = value
        return raw


class WeatherStationProfileStore(ProfileResourceStore):
    filename, list_type, list_field = 'WeatherStationList.json', 'WeatherStationList', 'weather_stations'
    model, id_field, label = WeatherStation, 'ws_id', 'Weather station'
    fields = ('ws_id', 'location', 'latitude', 'longitude', 'elevation')

    def new(self, identifier):
        return WeatherStation(ws_id=identifier, location='').to_dict()

    def prepare(self, raw, changes):
        for field, value in changes.items():
            if field in ('latitude', 'longitude', 'elevation'):
                if value is not None and type(value) not in (int, float):
                    raise ProfileStoreError(f'{field} must be numeric or null')
                value = float(value) if value is not None else None
            raw[field] = value
        if not isinstance(raw['location'], str) or not raw['location'].strip():
            raise ProfileStoreError('location must be a non-empty string')
        return raw
