"""Existing configuration contracts remain readable after relocating models."""

import json
from pathlib import Path

import pytest

from sdk.models.base import BaseModel


CONFIG_ROOT = Path(__file__).resolve().parents[2] / 'config'
CONFIG_NAMES = {'DishList.json', 'DigitiserList.json', 'WeatherStationList.json',
                'LaunchConfig.json', 'PipelineConfig.json', 'UIDriverList.json',
                'telescope.json'}
CONFIG_FILES = sorted(path for path in CONFIG_ROOT.rglob('*.json')
                      if path.name in CONFIG_NAMES)


def enum_markers(value):
    if isinstance(value, dict):
        if value.get('_type') in ('enum.IntEnum', 'enum.Enum'):
            yield value['_type'], value['instance'], value['value']
        for nested in value.values():
            yield from enum_markers(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from enum_markers(nested)


@pytest.mark.parametrize('path', CONFIG_FILES,
                         ids=lambda path: str(path.relative_to(CONFIG_ROOT)))
def test_existing_configuration_retains_model_and_enum_type_names(path):
    raw = json.loads(path.read_text())
    model = BaseModel._deserialise(raw)
    assert isinstance(model, BaseModel)
    assert type(model).__module__.startswith('sdk.models.')
    serialised = model.to_dict()
    assert serialised['_type'] == raw['_type']
    assert set(enum_markers(raw)) <= set(enum_markers(serialised))
    restored = BaseModel._deserialise(serialised)
    assert type(restored) is type(model)
    assert restored.to_dict() == serialised
