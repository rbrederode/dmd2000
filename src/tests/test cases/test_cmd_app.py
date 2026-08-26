import pytest

from api import protocol as dmd_protocol
from api.command import CommandAPI
from util.cmd_app import build_arg_parser, construct_request, normalise_command_config


def parse(*command_args):
    return build_arg_parser().parse_args([
        "--system", "dm",
        "--port", "60002",
        *command_args,
    ])


@pytest.mark.parametrize(
    ("command_args", "expected_call"),
    [
        (
            ("set", "trace", "on"),
            {"msg_type": "req", "action_code": "set", "property": "trace", "value": "ON"},
        ),
        (
            ("get", "debug"),
            {"msg_type": "req", "action_code": "get", "property": "debug"},
        ),
        (
            ("resync",),
            {"msg_type": "req", "action_code": "resync"},
        ),
    ],
)
def test_cmd_app_constructs_each_valid_command_form(command_args, expected_call):
    args = parse(*command_args)
    request = construct_request(args, CommandAPI())

    assert request.get_from_system() == dmd_protocol.CMD
    assert request.get_to_system() == dmd_protocol.DM
    assert request.get_api_call() == expected_call


def test_cmd_app_requires_valid_arguments_for_each_action():
    with pytest.raises(SystemExit):
        parse("set", "trace")

    with pytest.raises(SystemExit):
        parse("get", "status")

    with pytest.raises(SystemExit):
        parse("resync", "trace")


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"cmd": "Trace", "value": "On"}, ("set", "trace", "ON")),
        ({"cmd": "Debug", "value": "off"}, ("set", "debug", "OFF")),
        ({"cmd": "Trace"}, ("get", "trace", None)),
        ({"cmd": "Get", "property": "Debug"}, ("get", "debug", None)),
        (
            {"cmd": "Set", "property": "Trace", "value": "On"},
            ("set", "trace", "ON"),
        ),
        ({"cmd": "Resync"}, ("resync", None, None)),
        ({"cmd": "Resync", "value": "On"}, ("resync", None, None)),
        (
            {"cmd": "Resync", "property": "Trace", "value": "On"},
            ("resync", None, None),
        ),
    ],
)
def test_normalise_command_config(config, expected):
    assert normalise_command_config(config) == expected


@pytest.mark.parametrize(
    "config",
    [
        {},
        [],
        {"cmd": "Restart"},
        {"cmd": "Set", "property": "Trace", "value": "Maybe"},
        {"cmd": "Get", "property": "Trace", "value": "On"},
    ],
)
def test_normalise_command_config_rejects_invalid_commands(config):
    with pytest.raises(ValueError):
        normalise_command_config(config)
