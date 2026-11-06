# -*- coding: utf-8 -*-

from datetime import datetime, timezone
from pathlib import Path
from schema import Schema, And, Or

from models.base import BaseModel


class LaunchModel(BaseModel):
    """A class representing how to launch one configured application/entity."""

    schema = Schema({
        "_type": And(str, lambda v: v == "LaunchModel"),
        "app_name": And(str, lambda v: isinstance(v, str) and len(v) > 0),
        "entity_id": And(str, lambda v: isinstance(v, str) and len(v) > 0),
        "env": And(
            dict,
            lambda v: isinstance(v, dict)
            and all(isinstance(k, str) and len(k) > 0 for k in v.keys())
            and all(isinstance(val, str) for val in v.values()),
        ),
        "program": And(str, lambda v: isinstance(v, str) and len(v) > 0),
        "args": And(list, lambda v: isinstance(v, list) and all(isinstance(item, str) for item in v)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "LaunchModel",
            "app_name": "app",
            "entity_id": "app001",
            "env": {},
            "program": "python",
            "args": [],
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise TypeError(f"LaunchModel.from_dict expects a dict, got {type(data).__name__}")

        fields = {key: BaseModel._deserialise(value) for key, value in data.items() if key != "_type"}

        return cls(**fields)


class LaunchConfigModel(BaseModel):
    """A class representing a profile's launch definitions."""

    schema = Schema({
        "_type": And(str, lambda v: v == "LaunchConfigModel"),
        "profile": And(str, lambda v: isinstance(v, str) and len(v) > 0),
        "scan_store_dir": Or(None, And(str, lambda v: bool(v.strip()))),
        "launches": And(list, lambda v: isinstance(v, list) and all(isinstance(item, LaunchModel) for item in v)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "LaunchConfigModel",
            "profile": "default",
            "scan_store_dir": None,
            "launches": [],
            "last_update": datetime.now(timezone.utc),
        }

        for key, value in defaults.items():
            if key not in kwargs:
                kwargs.setdefault(key, value)

        super().__init__(**kwargs)

    def get_launch_by_entity_id(self, entity_id: str) -> LaunchModel:
        for launch in self.launches:
            if launch.entity_id == entity_id:
                return launch
        return None

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise TypeError(f"LaunchConfigModel.from_dict expects a dict, got {type(data).__name__}")

        fields = {key: BaseModel._deserialise(value) for key, value in data.items() if key != "_type"}

        launches = fields.get("launches", [])
        fields["launches"] = [
            item if isinstance(item, LaunchModel) else LaunchModel.from_dict(item)
            for item in launches
        ]

        return cls(**fields)


def load_scan_store_dir(profile="default", profile_dir=None):
    """Read the shared storage default without requiring a running application."""
    directory = Path(profile_dir).expanduser() if profile_dir is not None else Path(__file__).resolve().parents[1] / "config" / profile
    if not (directory / "LaunchConfig.json").exists():
        return None
    config = LaunchConfigModel.load_from_disk(input_dir=str(directory), filename="LaunchConfig.json")
    return config.scan_store_dir


if __name__ == "__main__":
    import pprint

    launch = LaunchModel(
        app_name="dig",
        entity_id="dig001",
        env={"GPIOZERO_PIN_FACTORY": "mock"},
        program="python",
        args=["dig/dig.py", "--profile", "jodrell", "--entity_id", "dig001"],
        last_update=datetime.now(timezone.utc),
    )
    config = LaunchConfigModel(profile="jodrell", launches=[launch], last_update=datetime.now(timezone.utc))

    pprint.pprint(config.to_dict())
