"""Web command dispatch is independent of TM and its embedded HTTP API."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest

from models.dig import DigitiserList, DigitiserModel
from models.sdp import ScienceDataProcessorModel
from web import cmd_handler as module
from web.cmd_handler import CommandHandler


@pytest.mark.parametrize('command, args', [
    ({'app': 'SDP', 'cmd': 'Trace', 'value': 'On'}, ['set', 'trace', 'ON']),
    ({'app': 'SDP', 'cmd': 'Debug', 'value': 'Off'}, ['set', 'debug', 'OFF']),
    ({'app': 'SDP', 'cmd': 'Resync', 'value': ''}, ['resync']),
    ({'app': 'SDP', 'cmd': 'Trace'}, ['get', 'trace']),
    ({'app': 'SDP', 'cmd': 'Get', 'property': 'Debug'}, ['get', 'debug']),
])
def test_commands_use_cmd_app_directly_and_return_target_reply(tmp_path, monkeypatch, command, args):
    captured = []
    target_reply = {'status': 'success', 'message': 'Applied', 'value': 'ON'}

    def run(argv, *, response_callback):
        captured.append(argv)
        response_callback(target_reply)
        return 0

    monkeypatch.setattr(module.cmd_app, 'main', run)
    handler = CommandHandler(profile_dir=tmp_path)
    result = handler.execute(json.dumps(command))
    assert captured == [['--host', '127.0.0.1', '--port', '60004', '--system', 'sdp',
                         '--timeout', '5.0', '--cmd_config', str(handler.command_config), *args]]
    assert result.status == 'success'
    assert result.message == 'Applied'
    assert result.payload == {'app': 'SDP', 'response': target_reply}


def test_profile_endpoint_is_reloaded_for_each_command(tmp_path, monkeypatch):
    path = tmp_path / 'ScienceDataProcessorModel.json'
    sdp = ScienceDataProcessorModel()
    sdp.app.app_cmd_host = '192.0.2.10'
    sdp.app.app_cmd_port = 61004
    path.write_text(json.dumps(sdp.to_dict()))
    calls = []

    def run(argv, *, response_callback):
        calls.append(argv)
        response_callback({'status': 'success', 'message': 'OK'})
        return 0

    monkeypatch.setattr(module.cmd_app, 'main', run)
    handler = CommandHandler(profile_dir=tmp_path)
    assert handler.execute({'app': 'SDP', 'cmd': 'Resync'}).status == 'success'
    sdp.app.app_cmd_host = '192.0.2.11'
    path.write_text(json.dumps(sdp.to_dict()))
    assert handler.execute({'app': 'SDP', 'cmd': 'Resync'}).status == 'success'
    assert calls[0][:4] == ['--host', '192.0.2.10', '--port', '61004']
    assert calls[1][:4] == ['--host', '192.0.2.11', '--port', '61004']


def test_digitiser_endpoint_is_resolved_from_profile(tmp_path, monkeypatch):
    digitiser = DigitiserModel(dig_id='dig003')
    digitiser.app.app_cmd_host = '192.0.2.30'
    digitiser.app.app_cmd_port = 61006
    (tmp_path / 'DigitiserList.json').write_text(json.dumps(DigitiserList(dig_list=[digitiser]).to_dict()))
    main = Mock(return_value=1)
    monkeypatch.setattr(module.cmd_app, 'main', main)
    CommandHandler(profile_dir=tmp_path).execute({'app': 'DIG003', 'cmd': 'Resync'})
    assert main.call_args.args[0][:6] == ['--host', '192.0.2.30', '--port', '61006', '--system', 'dig']


def test_older_profile_without_command_endpoint_uses_model_defaults(tmp_path, monkeypatch):
    (tmp_path / 'ScienceDataProcessorModel.json').write_text(json.dumps({
        '_type': 'ScienceDataProcessorModel', 'app': {'_type': 'AppModel'},
    }))
    main = Mock(return_value=1)
    monkeypatch.setattr(module.cmd_app, 'main', main)
    CommandHandler(profile_dir=tmp_path).execute({'app': 'SDP', 'cmd': 'Resync'})
    assert main.call_args.args[0][:4] == ['--host', '127.0.0.1', '--port', '60004']


def test_unknown_digitiser_is_rejected_without_sending(tmp_path, monkeypatch):
    (tmp_path / 'DigitiserList.json').write_text(json.dumps(DigitiserList().to_dict()))
    main = Mock()
    monkeypatch.setattr(module.cmd_app, 'main', main)
    result = CommandHandler(profile_dir=tmp_path).execute({'app': 'unknown', 'cmd': 'Resync'})
    assert result.code == 'invalid_request'
    main.assert_not_called()


def test_registered_command_preserves_target_value(tmp_path, monkeypatch):
    main = Mock(return_value=1)
    monkeypatch.setattr(module.cmd_app, 'main', main)
    CommandHandler(profile_dir=tmp_path).execute({'app': 'DM', 'cmd': 'STOP', 'value': 'Dish003'})
    assert main.call_args.args[0][-2:] == ['stop', 'Dish003']


def test_explicit_server_endpoint_works_without_profile_or_tm(tmp_path, monkeypatch):
    main = Mock(return_value=1)
    monkeypatch.setattr(module.cmd_app, 'main', main)
    handler = CommandHandler(profile_dir=tmp_path / 'not-mounted',
                             endpoint_overrides={'SDP': {'host': 'sdp.example.test', 'port': 62004}})
    handler.execute({'app': 'SDP', 'cmd': 'Resync'})
    assert main.call_args.args[0][:4] == ['--host', 'sdp.example.test', '--port', '62004']


@pytest.mark.parametrize('response, status, expected_code', [
    ({'status': 'error', 'message': 'Rejected'}, 1, 'command_failed'),
    ({'status': 'error', 'message': 'No reply', 'error': 'timeout'}, 1, 'timeout'),
])
def test_target_error_and_timeout_are_preserved(tmp_path, monkeypatch, response, status, expected_code):
    def run(argv, *, response_callback):
        response_callback(response)
        return status

    monkeypatch.setattr(module.cmd_app, 'main', run)
    result = CommandHandler(profile_dir=tmp_path).execute({'app': 'SDP', 'cmd': 'Resync'})
    assert result.status == 'error'
    assert result.code == expected_code
    assert result.message == response['message']
    assert result.payload['response'] == response


@pytest.mark.parametrize('command', [
    {'app': 'SDP', 'cmd': 'invalid'}, {'app': 'SDP', 'cmd': 'Trace', 'value': 'invalid'},
    {'cmd': 'Trace'}, [], None, '{broken',
])
def test_invalid_commands_do_not_open_connections(tmp_path, monkeypatch, command):
    main = Mock()
    monkeypatch.setattr(module.cmd_app, 'main', main)
    result = CommandHandler(profile_dir=tmp_path).execute(command)
    assert result.status == 'error'
    assert result.code == 'invalid_request'
    main.assert_not_called()


def test_profile_unavailable_is_reported_without_sending_command(tmp_path, monkeypatch):
    main = Mock()
    monkeypatch.setattr(module.cmd_app, 'main', main)
    result = CommandHandler(profile_dir=tmp_path / 'missing').execute({'app': 'SDP', 'cmd': 'Resync'})
    assert result.status == 'error'
    assert result.code == 'internal_error'
    main.assert_not_called()


@pytest.mark.parametrize('error', [RuntimeError('network failure'), SystemExit(2)])
def test_command_failures_return_response_event(tmp_path, monkeypatch, error):
    monkeypatch.setattr(module.cmd_app, 'main', Mock(side_effect=error))
    result = CommandHandler(profile_dir=tmp_path).execute({'app': 'SDP', 'cmd': 'Resync'})
    assert result.status == 'error'
    assert result.code == 'command_failed'


def test_concurrent_commands_keep_their_own_replies(tmp_path, monkeypatch):
    ready = Barrier(2)

    def run(argv, *, response_callback):
        ready.wait(timeout=3)
        response_callback({'status': 'success', 'message': argv[-1]})
        return 0

    monkeypatch.setattr(module.cmd_app, 'main', run)
    handler = CommandHandler(profile_dir=tmp_path)
    with ThreadPoolExecutor(max_workers=2) as workers:
        trace = workers.submit(handler.execute, {'app': 'SDP', 'cmd': 'Trace'})
        debug = workers.submit(handler.execute, {'app': 'SDP', 'cmd': 'Debug'})
        assert trace.result(timeout=3).message == 'trace'
        assert debug.result(timeout=3).message == 'debug'
