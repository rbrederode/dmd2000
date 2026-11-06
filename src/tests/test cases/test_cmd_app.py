import pytest

from api import protocol as dmd_protocol
from api.cmd_api import ACTION_CODE_COMMAND, CommandAPI
from api.cmd_registry import CommandRegistry
from util.cmd_app import build_arg_parser, construct_request, normalise_command_config


@pytest.mark.parametrize('mode, expected_status', [
    ('success', 0), ('rejected', 1), ('connection_timeout', 1), ('response_timeout', 1),
])
def test_main_delivers_response_to_in_process_caller_and_cleans_up(monkeypatch, mode, expected_status):
    from unittest.mock import Mock
    from util import cmd_app

    real_driver = cmd_app.CommandUtilityDriver
    drivers = []

    def make_driver(*args, **kwargs):
        driver = real_driver(*args, **kwargs)
        drivers.append(driver)
        return driver

    client = Mock()
    processor = Mock()
    target_reply = {'msg_type': 'rsp', 'action_code': 'get', 'property': 'trace',
                    'status': 'success' if mode == 'success' else 'error',
                    'value': 'ON', 'message': 'Target result'}

    def send(request):
        if mode != 'response_timeout':
            drivers[0]._process_response(target_reply)

    client.send.side_effect = send
    monkeypatch.setattr(cmd_app, 'CommandUtilityDriver', make_driver)
    monkeypatch.setattr(cmd_app, 'TCPClient', Mock(return_value=client))
    monkeypatch.setattr(cmd_app, 'AppProcessor', Mock(return_value=processor))
    monkeypatch.setattr(cmd_app, '_wait_for_connection', lambda *_: mode != 'connection_timeout')
    received = []
    status = cmd_app.main(['--system', 'tm', '--port', '60002', '--timeout', '0.01',
                           'get', 'trace'], response_callback=received.append)
    assert status == expected_status
    assert len(received) == 1
    if 'timeout' in mode:
        assert received[0]['error'] == 'timeout'
    else:
        assert received[0] == target_reply
    client.stop.assert_called_once()
    processor.stop.assert_called_once()
    processor.join.assert_called_once()


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
