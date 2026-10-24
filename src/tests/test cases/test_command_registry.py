import json

import pytest

from api.cmd_registry import CommandRegistry
from util.xbase import XAPIValidationFailed


def write_registry(tmp_path, applications):
    path = tmp_path / "CmdRegistry.json"
    path.write_text(json.dumps({
        "_type": "CmdRegistry",
        "applications": applications,
    }))
    return path


def test_registry_loads_commands_for_only_requested_application(tmp_path):
    path = write_registry(tmp_path, {
        "dm": {
            "commands": {
                "stop": {
                    "description": "Stop a dish",
                    "value": {"required": True, "type": "str"},
                },
            },
        },
        "sdp": {"commands": {}},
    })

    dm_registry = CommandRegistry.load("dm", path)
    sdp_registry = CommandRegistry.load("sdp", path)

    assert dm_registry.command_names == ("stop",)
    assert sdp_registry.command_names == ()


def test_registry_validates_configured_value_pattern(tmp_path):
    path = write_registry(tmp_path, {
        "dm": {
            "commands": {
                "stop": {
                    "value": {
                        "required": True,
                        "type": "str",
                        "pattern": "^dish[0-9]+$",
                    },
                },
            },
        },
    })
    registry = CommandRegistry.load("dm", path)

    registry.validate({"command": "stop", "value": "dish001"})
    with pytest.raises(XAPIValidationFailed):
        registry.validate({"command": "stop", "value": "antenna001"})


def test_registry_requires_explicit_handlers(tmp_path):
    path = write_registry(tmp_path, {
        "dm": {
            "commands": {
                "stop": {"value": {"required": True, "type": "str"}},
            },
        },
    })
    registry = CommandRegistry.load("dm", path)

    with pytest.raises(ValueError, match="no handlers"):
        registry.validate_handlers()

    registry.register_handler("stop", lambda api_call: None)
    registry.validate_handlers()
