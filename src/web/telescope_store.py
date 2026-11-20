"""Validated, atomic editing of the profile-independent observatory configuration."""

import hashlib
import json
from pathlib import Path

from models.telescope import TelescopeConfig
from util.xbase import XAPIValidationFailed
from web.profile_store import ProfileFileStore, ProfileStoreError

FIELDS = ('name', 'latitude', 'longitude', 'height')
CONFIG_DIR = Path(__file__).resolve().parents[1] / 'config'


class TelescopeConfigStore(ProfileFileStore):
    def __init__(self, config_dir=None):
        self.path = Path(config_dir if config_dir is not None else CONFIG_DIR) / 'telescope.json'

    def _read(self):
        try:
            content = self.path.read_bytes()
        except FileNotFoundError as error:
            raise ProfileStoreError('config/telescope.json was not found', 'config_not_found', 404) from error
        try:
            model = TelescopeConfig.from_dict(json.loads(content))
        except (ValueError, XAPIValidationFailed) as error:
            raise ProfileStoreError(f'Invalid telescope configuration: {error}', 'invalid_configuration') from error
        return {key: getattr(model, key) for key in FIELDS}, hashlib.sha256(content).hexdigest()

    def read(self):
        return self._read()

    def save(self, data, if_match=None):
        if not isinstance(data, dict) or set(data) != set(FIELDS):
            raise ProfileStoreError('Supply only name, latitude, longitude and height')
        try:
            model = TelescopeConfig(**data)
        except XAPIValidationFailed as error:
            raise ProfileStoreError(str(error)) from error
        validated = {key: getattr(model, key) for key in FIELDS}
        with self._locked():
            _, revision = self._read()
            if if_match is None:
                raise ProfileStoreError('Reload Telescope configuration before saving', 'version_required', 428)
            if not if_match.contains(revision):
                raise ProfileStoreError('Telescope configuration changed; reload before saving', 'version_conflict', 412)
            return validated, self._write({'_type': 'TelescopeConfig', **validated})
