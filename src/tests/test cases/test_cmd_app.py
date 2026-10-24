import pytest

from api import protocol as dmd_protocol
from api.cmd_api import ACTION_CODE_COMMAND, CommandAPI
from api.cmd_registry import CommandRegistry
from util.cmd_app import build_arg_parser, construct_request, normalise_command_config


def parse(*command_args):
    registry = CommandRegistry.load(app_name=dmd_protocol.DM)
    return build_arg_parser(registry=registry).parse_args([
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
        (
            ("stop", "dish001"),
            {
                "msg_type": "req",
                "action_code": ACTION_CODE_COMMAND,
                "command": "stop",
                "value": "dish001",
            },
        ),
    ],
)
def test_cmd_app_constructs_each_valid_command_form(command_args, expected_call):
    args = parse(*command_args)
    registry = CommandRegistry.load(app_name=dmd_protocol.DM)
    request = construct_request(args, CommandAPI(registry=registry))

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

    sdp_registry = CommandRegistry.load(app_name=dmd_protocol.SDP)
    with pytest.raises(SystemExit):
        build_arg_parser(registry=sdp_registry).parse_args([
            "--system", "sdp",
            "--port", "60004",
            "stop", "dish001",
        ])


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
        (
            {"app": "DM", "cmd": "STOP", "value": "dish001"},
            ("stop", None, "dish001"),
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
        {"app": "DM", "cmd": "Stop"},
        {"app": "DM", "cmd": "Stop", "property": "Mode", "value": "dish001"},
        {"app": "SDP", "cmd": "Stop", "value": "dish001"},
    ],
)
def test_normalise_command_config_rejects_invalid_commands(config):
    with pytest.raises(ValueError):
        normalise_command_config(config)
