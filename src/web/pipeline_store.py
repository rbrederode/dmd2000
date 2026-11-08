"""Profile-backed pipeline step maps without instantiating processing plugins."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

from models.base import BaseModel
from models.pipeline import PipelineConfig, StepConfig, StepType
from util.xbase import XAPIValidationFailed
from web.profile_store import ProfileFileStore, ProfileStoreError, _finite


class PipelineProfileStore(ProfileFileStore):
    def __init__(self, profile_dir):
        self.path = Path(profile_dir).expanduser() / 'PipelineConfig.json'

    def _read(self, allow_missing=False):
        try:
            content = self.path.read_bytes()
        except FileNotFoundError as error:
            if allow_missing:
                return PipelineConfig(steps_map={}).to_dict(), None
            raise ProfileStoreError('PipelineConfig.json was not found in the active profile', 'config_not_found', 404) from error
        try:
            data = json.loads(content)
            if not isinstance(data, dict) or data.get('_type') != 'PipelineConfig' or not isinstance(data.get('steps_map'), dict):
                raise ValueError('Expected PipelineConfig with steps_map')
            PipelineConfig.from_dict(data)
            _finite(data)
        except (ValueError, TypeError, KeyError, XAPIValidationFailed, ProfileStoreError) as error:
            raise ProfileStoreError('The profile PipelineConfig.json is invalid', 'invalid_configuration', 500) from error
        return data, hashlib.sha256(content).hexdigest()

    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', name):
            raise ProfileStoreError('Map name must contain 1–128 letters, digits, underscores or hyphens')
        return name

    @staticmethod
    def _steps(raw_steps):
        result = []
        for index, raw in enumerate(raw_steps):
            step = StepConfig.from_dict(raw)
            result.append({'index': index, 'step': step.step.name if isinstance(step.step, StepType) else step.step,
                           'params': step.to_dict()['params']})
        return result

    def _map(self, data, name):
        if name not in data['steps_map']:
            raise ProfileStoreError(f'Steps map {name} was not found', 'not_found', 404)
        steps = self._steps(data['steps_map'][name])
        return {'name': name, 'steps': steps, 'count': len(steps)}

    def read(self, name=None, index=None):
        data, revision = self._read()
        if name is not None:
            result = self._map(data, self._name(name))
            if index is not None:
                if not 0 <= index < result['count']:
                    raise ProfileStoreError('Step index was not found', 'not_found', 404)
                result['step'] = result['steps'][index]
            return result, revision
        return {'steps_map': {key: self._steps(value) for key, value in data['steps_map'].items()},
                'count': len(data['steps_map']), 'step_types': list(StepType.__members__)}, revision

    @staticmethod
    def _step(changes, original=None):
        if not isinstance(changes, dict) or not changes or set(changes) - {'step', 'params'}:
            raise ProfileStoreError('A step must contain step and/or params only')
        _finite(changes)
        if original is None and 'step' not in changes:
            raise ProfileStoreError('A new step requires step')
        raw = deepcopy(original) if original is not None else StepConfig(params={}).to_dict()
        if 'step' in changes:
            name = changes['step']
            if not isinstance(name, str) or not name.strip():
                raise ProfileStoreError('step must be a non-empty built-in name or custom registry key')
            name = name.strip()
            previous = StepConfig.from_dict(raw).get_step_key()
            value = StepType.__members__.get(name.upper(), name)
            raw['step'] = BaseModel._serialise(value)
            if name.lower() != previous:
                raw['params'] = {}
        if 'params' in changes:
            if not isinstance(changes['params'], dict):
                raise ProfileStoreError('params must be an object')
            raw['params'] = dict(raw.get('params', {}), **deepcopy(changes['params']))
        raw['last_update'] = BaseModel._serialise(datetime.now(timezone.utc))
        try:
            StepConfig.from_dict(raw)
        except (ValueError, TypeError, KeyError, XAPIValidationFailed) as error:
            raise ProfileStoreError(f'Invalid step configuration: {error}') from error
        return raw

    def delete(self, name, index=None, if_match=None):
        self._name(name)
        with self._locked():
            data, revision = self._read()
            if if_match is not None and not if_match.contains(revision):
                raise ProfileStoreError('Pipeline configuration changed; reload before deleting', 'version_conflict', 412)
            if name not in data['steps_map']:
                raise ProfileStoreError(f'Steps map {name} was not found', 'not_found', 404)
            result = {'name': name}
            if index is None:
                del data['steps_map'][name]
            else:
                steps = data['steps_map'][name]
                if type(index) is not int or not 0 <= index < len(steps):
                    raise ProfileStoreError('Step index was not found', 'not_found', 404)
                del steps[index]
                result['deleted_index'] = index
                result['steps'] = self._steps(steps)
                result['count'] = len(steps)
            data['last_update'] = BaseModel._serialise(datetime.now(timezone.utc))
            return result, self._write(data)

    def save(self, changes, name=None, index=None, if_match=None):
        if not isinstance(changes, dict) or not changes:
            raise ProfileStoreError('Send a non-empty JSON object')
        _finite(changes)
        creating_map = name is None
        if creating_map:
            if set(changes) - {'name', 'steps'} or 'name' not in changes:
                raise ProfileStoreError('A new map requires name and optional steps')
            name = self._name(changes['name'])
            steps = changes.get('steps', [])
            if not isinstance(steps, list):
                raise ProfileStoreError('steps must be an array')
            new_steps = [self._step(step) for step in steps]
        else:
            self._name(name)
        with self._locked():
            data, revision = self._read(allow_missing=creating_map)
            if if_match is not None and (revision is None or not if_match.contains(revision)):
                raise ProfileStoreError('Pipeline configuration changed; reload before saving', 'version_conflict', 412)
            maps = data['steps_map']
            if creating_map:
                if name in maps:
                    raise ProfileStoreError(f'Steps map {name} already exists', 'already_exists', 409)
                maps[name] = new_steps
            else:
                if name not in maps:
                    raise ProfileStoreError(f'Steps map {name} was not found', 'not_found', 404)
                steps = maps[name]
                if index is not None:
                    if type(index) is not int or not 0 <= index < len(steps):
                        raise ProfileStoreError('Step index was not found', 'not_found', 404)
                    steps[index] = self._step(changes, steps[index])
                else:
                    position = changes.get('index', len(steps))
                    if type(position) is not int or not 0 <= position <= len(steps):
                        raise ProfileStoreError('Insertion index must be between zero and the step count')
                    patch = {key: value for key, value in changes.items() if key != 'index'}
                    steps.insert(position, self._step(patch))
                    index = position
            data['last_update'] = BaseModel._serialise(datetime.now(timezone.utc))
            PipelineConfig.from_dict(data)
            revision = self._write(data)
            result = self._map(data, name)
            if index is not None:
                result['step'] = result['steps'][index]
            return result, revision
