# -*- coding: utf-8 -*-

from datetime import datetime, timezone
from schema import Schema, And

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

        fields = dict(data)
        fields.pop("_type", None)

        return cls(**fields)


class LaunchConfigModel(BaseModel):
    """A class representing a profile's launch definitions."""

    schema = Schema({
        "_type": And(str, lambda v: v == "LaunchConfigModel"),
        "profile": And(str, lambda v: isinstance(v, str) and len(v) > 0),
        "launches": And(list, lambda v: isinstance(v, list) and all(isinstance(item, LaunchModel) for item in v)),
        "last_update": And(datetime, lambda v: isinstance(v, datetime)),
    })

    allowed_transitions = {}

    def __init__(self, **kwargs):
        defaults = {
            "_type": "LaunchConfigModel",
            "profile": "default",
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

        fields = dict(data)
        fields.pop("_type", None)

        launches = fields.get("launches", [])
        fields["launches"] = [
            item if isinstance(item, LaunchModel) else LaunchModel.from_dict(item)
            for item in launches
        ]

        return cls(**fields)


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
