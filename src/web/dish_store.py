"""Profile-backed dish configuration, independent of live telescope state."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from dsh.drivers.drift.drift_config import DriftConfig
from dsh.drivers.md01.md01_config import MD01Config
from dsh.drivers.motion.motion_config import MotionDishConfig
from models.base import BaseModel
from models.dsh import DishModel, DriverType, Feed
from util.xbase import XAPIValidationFailed
from web.profile_store import ProfileFileStore, ProfileStoreError, _finite


FIELDS = ('dsh_id', 'short_desc', 'diameter', 'fd_ratio', 'latitude', 'longitude',
          'height', 'ws_id', 'feed_type', 'feed_config', 'dig_id', 'driver_type',
          'driver_config', 'driver_poll_period')
DRIVERS = {'MD01': MD01Config, 'DRIFT': DriftConfig, 'MOTION': MotionDishConfig}
DishStoreError = ProfileStoreError


def _view(raw):
    model = DishModel.from_dict(raw)
    data = model.to_dict()
    result = {key: data[key] for key in FIELDS}
    for key in ('feed_type', 'driver_type'):
        result[key] = getattr(model, key).name
    config = result['driver_config']
    if isinstance(config, dict):
        result['driver_config'] = {key: value for key, value in config.items()
                                   if key not in ('_type', 'last_update')}
    return result


class DishProfileStore(ProfileFileStore):
    def __init__(self, profile_dir):
        self.path = Path(profile_dir).expanduser() / 'DishList.json'

    def _read(self):
        try:
            content = self.path.read_bytes()
        except FileNotFoundError as error:
            raise DishStoreError('DishList.json was not found in the active profile', 'config_not_found', 404) from error
        try:
            data = json.loads(content)
            if not isinstance(data, dict) or data.get('_type') != 'DishList' or not isinstance(data.get('dish_list'), list):
                raise ValueError('Expected a DishList with a dish_list array')
            ids = []
            for dish in data['dish_list']:
                model = DishModel.from_dict(dish)
                ids.append(model.dsh_id)
            if len(set(ids)) != len(ids):
                raise ValueError('Duplicate dish identifiers')
        except (ValueError, TypeError, KeyError, XAPIValidationFailed) as error:
            raise DishStoreError('The profile DishList.json is invalid', 'invalid_configuration', 500) from error
        return data, hashlib.sha256(content).hexdigest()

    def read(self, dish_id=None):
        data, revision = self._read()
        if dish_id is None:
            return [_view(dish) for dish in data['dish_list']], revision
        dish = next((item for item in data['dish_list'] if item['dsh_id'] == dish_id), None)
        if dish is None:
            raise DishStoreError(f'Dish {dish_id} was not found', 'not_found', 404)
        return _view(dish), revision

    def delete(self, dish_id, if_match=None):
        return self.delete_entry(dish_id, 'dish_list', 'dsh_id', if_match)

    def save(self, changes, dish_id=None, if_match=None):
        if not isinstance(changes, dict) or not changes:
            raise DishStoreError('Send a non-empty dish configuration object')
        unknown = set(changes) - set(FIELDS)
        if unknown:
            raise DishStoreError('Unknown or non-configuration fields: ' + ', '.join(sorted(unknown)))
        _finite(changes)
        for field in ('diameter', 'fd_ratio', 'latitude', 'longitude', 'height'):
            if field in changes and type(changes[field]) not in (int, float):
                raise DishStoreError(f'{field} must be numeric')
        with self._locked():
            data, revision = self._read()
            if if_match is not None and not if_match.contains(revision):
                raise DishStoreError('Dish configuration changed; reload before saving', 'version_conflict', 412)
            dishes = data['dish_list']
            existing = next((item for item in dishes if item['dsh_id'] == dish_id), None) if dish_id is not None else None
            if dish_id is not None and existing is None:
                raise DishStoreError(f'Dish {dish_id} was not found', 'not_found', 404)
            identifier = changes.get('dsh_id', dish_id)
            if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', identifier):
                raise DishStoreError('dsh_id must contain 1–128 letters, digits, underscores or hyphens')
            if dish_id is not None and identifier != dish_id:
                raise DishStoreError('The dish identifier cannot be changed')
            if existing is None and any(item['dsh_id'] == identifier for item in dishes):
                raise DishStoreError(f'Dish {identifier} already exists', 'already_exists', 409)
            raw = deepcopy(existing) if existing is not None else DishModel(dsh_id=identifier).to_dict()
            old_driver = DishModel.from_dict(raw).driver_type.name
            for key, value in changes.items():
                if key not in ('driver_config', 'driver_type', 'feed_type'):
                    raw[key] = value
            try:
                for field, enum in (('driver_type', DriverType), ('feed_type', Feed)):
                    if field in changes:
                        value = changes[field]
                        if not isinstance(value, str):
                            raise DishStoreError(f'{field} must be an enum name')
                        raw[field] = BaseModel._serialise(enum[value.upper()])
                driver = DishModel.from_dict(raw).driver_type.name
                if driver not in DRIVERS:
                    raise DishStoreError('driver_type must be MD01, DRIFT or MOTION')
                config_class = DRIVERS[driver]
                config = (deepcopy(raw.get('driver_config')) or {}) if driver == old_driver else {}
                patch = changes.get('driver_config', {})
                if not isinstance(patch, dict):
                    raise DishStoreError('driver_config must be an object')
                if set(patch) - (set(config_class.schema.schema) - {'_type', 'last_update'}):
                    raise DishStoreError('Unknown fields in driver_config')
                config.update(patch)
                config['_type'] = config_class.__name__
                config['last_update'] = BaseModel._serialise(datetime.now(timezone.utc))
                # JSON integers are accepted for float-valued configuration fields.
                defaults = config_class().to_dict()
                for key, value in patch.items():
                    if type(defaults.get(key)) in (int, float) and isinstance(value, bool):
                        raise DishStoreError(f'driver_config.{key} must be numeric')
                    if type(defaults.get(key)) is float and type(value) is int:
                        config[key] = float(value)
                raw['driver_config'] = config
                raw['last_update'] = BaseModel._serialise(datetime.now(timezone.utc))
                model = DishModel.from_dict(raw)
                if type(model.driver_poll_period) is not int or model.driver_poll_period <= 0:
                    raise DishStoreError('driver_poll_period must be a positive integer')
                # Validate the whole candidate, keeping legacy/unknown fields on disk.
                _view(raw)
            except (KeyError, ValueError, TypeError, XAPIValidationFailed) as error:
                raise DishStoreError(f'Invalid dish configuration: {error}') from error
            if existing is None:
                dishes.append(raw)
            else:
                dishes[dishes.index(existing)] = raw
            data['last_update'] = raw['last_update']
            revision = self._write(data)
            return _view(raw), revision
