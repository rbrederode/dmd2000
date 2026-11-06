"""TM-WEB validation, serialized config completion and timer-owned requests."""

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from queue import Queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api.tm_web import TM_WEB, configuration_request, configuration_response
from env.app import App
from env.app_processor import AppProcessor
from env.events import CallbackEvent, ConfigEvent, ResponseEvent, TimerEvent
from ipc.action import Action
from models.app import AppModel
from models.comms import CommunicationStatus
from models.telescope import TelescopeModel
from models.web import WebServerModel
from tm.tm import TelescopeManager
from util.timer import Timer, TimerManager
from util.xbase import XAPIValidationFailed
from web.web import WebServer, HTTPConfigRequest, TIMER_PREFIX


@pytest.fixture
def timers(monkeypatch):
    manager = TimerManager()
    monkeypatch.setattr(Timer, 'manager', manager)
    return manager


def web_stub():
    web = WebServer.__new__(WebServer)
    web._stopped = True  # This test owns the minimal instance's resources.
    web._accepting_requests = True
    web._requests_lock = threading.RLock()
    web._lock = threading.Lock()
    web.avail_logger = Mock()
    web.web_model = WebServerModel()
    web.app_model = web.web_model.app
    web.app_model.app_running = True
    web.queue = Queue()
    web.tm_endpoint = SimpleNamespace(connected=True, client_socket=object(), send=Mock())
    web.tm_api = TM_WEB()
    web.get_args = lambda: SimpleNamespace(request_timeout=0.2)
    web.get_interface = lambda _: (web.tm_api, web.tm_endpoint, None)
    web.get_app_processor_state = Mock()
    return web


def tm_stub():
    tm = TelescopeManager.__new__(TelescopeManager)
    tm.stop = lambda: None
    tm.telmodel = TelescopeModel()
    tm.app_model = tm.telmodel.tel_mgr.app
    tm.queue = Queue()
    tm._web_connections = set()
    tm._web_connections_lock = threading.Lock()
    tm.oet = SimpleNamespace(start_next_obs_timer=Mock())
    return tm


@pytest.mark.parametrize('category', ['DIG', 'DSH', 'ODT', 'OET'])
def test_protocol_roundtrip_preserves_request_id_and_result(category):
    request = configuration_request(category, {'_type': 'Config'}, 'request-1')
    reply = configuration_response(request.get_json_api_header(), ResponseEvent(
        'error', 'Invalid configuration', code='invalid_request', payload={'category': category}))
    TM_WEB().validate(reply.get_json_api_header())
    assert reply.get_to_system() == 'web'
    assert reply.get_echo_data() == {'request_id': 'request-1'}
    assert reply.get_api_call()['code'] == 'invalid_request'


@pytest.mark.parametrize('change', [
    {'from': 'cmd'}, {'to': 'sdp'}, {'api_version': '2.0'}, {'echo_data': {}},
    {'api_call': {'msg_type': 'adv', 'action_code': 'set', 'property': 'config'}},
])
def test_invalid_protocol_messages_are_rejected(change):
    request = configuration_request('ODT', {'_type': 'ObsList', 'obs_list': []}, 'request-1')
    message = request.get_json_api_header() | change
    with pytest.raises(XAPIValidationFailed):
        TM_WEB().validate(message)


def test_config_future_resolves_after_actions_are_scheduled():
    future = Future()
    event = ConfigEvent(category='ODT', new_config={}, rsp_future=future)
    driver = SimpleNamespace(process_config=lambda _: Action())
    processor = AppProcessor(event_q=Queue(), driver=driver)
    scheduled = []

    def perform(action):
        assert not future.done()
        scheduled.append(True)

    processor.performActions = perform
    processor.process_config_event(event)
    assert scheduled == [True]
    assert future.result().status == 'success'
    assert future.result().payload['phase'] == 'actions_scheduled'


def test_config_future_receives_logged_error_and_actions_are_not_dispatched():
    driver = SimpleNamespace(app_model=AppModel(), trace=None)

    def process(event):
        App.set_last_err(driver, 'Digitiser is not connected')
        return Action()

    driver.process_config = process
    processor = AppProcessor(event_q=Queue(), driver=driver)
    processor.performActions = Mock()
    future = Future()
    processor.process_config_event(ConfigEvent(category='DIG', rsp_future=future))
    assert future.result().status == 'error'
    assert future.result().message == 'Digitiser is not connected'
    processor.performActions.assert_not_called()


def test_config_exception_completes_future():
    driver = SimpleNamespace(process_config=Mock(side_effect=ValueError('Invalid observation')))
    processor = AppProcessor(event_q=Queue(), driver=driver)
    future = Future()
    with pytest.raises(ValueError):
        processor.process_config_event(ConfigEvent(category='ODT', rsp_future=future))
    assert future.result().status == 'error'
    assert 'Invalid observation' in future.result().message


def test_action_scheduling_failure_preserves_logged_context():
    driver = SimpleNamespace(app_model=AppModel(), trace=None)
    driver.process_config = lambda _: Action().set_msg_to_remote('invalid message')
    driver.set_last_err = lambda message: App.set_last_err(driver, message)
    processor = AppProcessor(event_q=Queue(), driver=driver)
    future = Future()
    processor.process_config_event(ConfigEvent(category='ODT', rsp_future=future))
    assert future.result().status == 'error'
    assert 'not an APIMessage' in future.result().message


def test_config_processing_blocks_parallel_events_until_actions_are_scheduled():
    entered, release, other_ran = threading.Event(), threading.Event(), threading.Event()
    future = Future()

    def configure(event):
        entered.set()
        assert release.wait(2)
        return Action()

    config_processor = AppProcessor(event_q=Queue(), driver=SimpleNamespace(process_config=configure))
    other = AppProcessor(event_q=Queue(), driver=SimpleNamespace())
    thread = threading.Thread(target=config_processor.process_config_event,
                              args=(ConfigEvent(category='ODT', rsp_future=future),))
    thread.start()
    try:
        assert entered.wait(1)
        other.get_queue().put(CallbackEvent(lambda: (other_ran.set(), Action())[1]))
        other.start()
        assert not other_ran.wait(0.05)
        assert not future.done()
        release.set()
        assert future.result(timeout=1).status == 'success'
        assert other_ran.wait(1)
    finally:
        release.set()
        thread.join(timeout=2)
        other.stop()
        if other.is_alive():
            other.join(timeout=2)


def test_tm_queues_config_and_sends_reply_on_original_connection_after_completion():
    tm = tm_stub()
    endpoint, connection = object(), object()
    event = SimpleNamespace(local_sap=endpoint, remote_conn=connection, remote_addr=('127.0.0.1', 1))
    request = configuration_request('ODT', {'_type': 'ObsList', 'obs_list': []}, 'request-1')
    assert tm.process_web_msg(event, request.get_json_api_header(), request.get_api_call(), None).is_empty()
    config = tm.queue.get_nowait()
    assert isinstance(config, ConfigEvent)
    assert not config.rsp_future.done()
    processor = AppProcessor(event_q=tm.queue, driver=tm)
    processor.process_config_event(config)
    callback = tm.queue.get_nowait()
    assert isinstance(callback, CallbackEvent)
    assert callback.remote_conn is connection
    assert callback.local_sap is endpoint
    response = callback.callback().msgs_to_remote[0]
    assert response.get_api_call()['status'] == 'success'
    assert response.get_echo_data()['request_id'] == 'request-1'


@pytest.mark.parametrize('category, config', [
    ('DIG', {'dig_id': 'missing'}), ('DSH', {'dsh_id': 'missing'}),
    ('ODT', {'_type': 'WrongModel'}), ('OET', {'_type': 'ObservationReset', 'obs_id': 'missing'}),
])
def test_tm_rejects_invalid_configuration_with_context(category, config):
    tm = tm_stub()
    processor = AppProcessor(event_q=tm.queue, driver=tm)
    future = Future()
    with pytest.raises(ValueError):
        processor.process_config_event(ConfigEvent(category=category, new_config=config, rsp_future=future))
    assert future.result().status == 'error'


def test_disconnected_submission_is_never_queued(timers):
    web = web_stub()
    web.tm_endpoint.connected = False
    result, _ = web.submit_configuration('ODT', {'_type': 'ObsList', 'obs_list': []})
    assert result.code == 'not_connected'
    assert web.queue.empty()
    assert timers.heap == []


def test_timer_is_the_request_registry_and_reply_cancels_it(timers):
    web = web_stub()
    processor = AppProcessor(event_q=web.queue, driver=web)
    with ThreadPoolExecutor(max_workers=1) as worker:
        waiting = worker.submit(web.submit_configuration, 'ODT', {'_type': 'ObsList', 'obs_list': []})
        submission = web.queue.get(timeout=1)
        processor.process_event(submission)
        [timer] = web._request_timers()
        assert isinstance(timer.user_ref, HTTPConfigRequest)
        request = web.tm_endpoint.send.call_args.args[0]
        reply = configuration_response(request.get_json_api_header(), ResponseEvent('success', 'Actions scheduled'))
        web.process_tm_msg(None, reply.get_json_api_header(), reply.get_api_call(), None)
        result, request_id = waiting.result(timeout=1)
    assert result.message == 'Actions scheduled'
    assert timer.name == TIMER_PREFIX + request_id
    assert not timer.is_active()
    assert web.tm_endpoint.send.call_count == 1


def test_timeout_completes_upstream_without_retransmission_and_ignores_late_reply(timers):
    web = web_stub()
    processor = AppProcessor(event_q=web.queue, driver=web)
    with ThreadPoolExecutor(max_workers=1) as worker:
        waiting = worker.submit(web.submit_configuration, 'ODT', {'_type': 'ObsList', 'obs_list': []})
        processor.process_event(web.queue.get(timeout=1))
        [timer] = web._request_timers()
        timer.queue()
        processor.process_event(web.queue.get(timeout=1))
        result, _ = waiting.result(timeout=1)
        request = web.tm_endpoint.send.call_args.args[0]
        late = configuration_response(request.get_json_api_header(), ResponseEvent('success', 'Late'))
        web.process_tm_msg(None, late.get_json_api_header(), late.get_api_call(), None)
    assert result.code == 'timeout'
    assert 'unknown' in result.message
    assert web.tm_endpoint.send.call_count == 1


def test_disconnect_fails_inflight_request_and_does_not_replay_on_reconnect(timers):
    web = web_stub()
    processor = AppProcessor(event_q=web.queue, driver=web)
    with ThreadPoolExecutor(max_workers=1) as worker:
        waiting = worker.submit(web.submit_configuration, 'ODT', {'_type': 'ObsList', 'obs_list': []})
        processor.process_event(web.queue.get(timeout=1))
        web.tm_endpoint.connected = False
        web.process_tm_disconnected(SimpleNamespace(remote_conn=None))
        assert waiting.result(timeout=1)[0].code == 'connection_lost'
        web.tm_endpoint.connected = True
        web.process_tm_connected(SimpleNamespace(remote_conn=web.tm_endpoint.client_socket))
    assert web.tm_endpoint.send.call_count == 1


def test_web_model_roundtrip_and_default_command_port():
    model = WebServerModel.from_dict(WebServerModel().to_dict())
    assert model.app.app_name == 'web'
    assert model.app.app_cmd_port == 60010


def test_web_sends_status_immediately_after_connecting():
    web = web_stub()
    action = web.process_tm_connected(SimpleNamespace(remote_conn=web.tm_endpoint.client_socket))
    [message] = action.msgs_to_remote
    TM_WEB().validate(message.get_json_api_header())
    call = message.get_api_call()
    assert (call['msg_type'], call['action_code'], call['property']) == ('adv', 'set', 'status')
    assert call['value']['_type'] == 'WebServerModel'
    assert call['value']['app']['app_running'] is True
    assert call['value']['tm_connected']['value'] == 'ESTABLISHED'
    assert message.get_from_system() == 'web' and message.get_to_system() == 'tm'
    assert message.get_echo_data() is None
    assert not action.timer_actions
    web.get_app_processor_state.assert_called_once_with()


def test_periodic_status_uses_current_model_and_requires_connection():
    web = web_stub()
    assert web.process_status_event(None).is_empty()
    web.process_tm_connected(SimpleNamespace(remote_conn=web.tm_endpoint.client_socket))
    web.app_model.app_debug = True
    [message] = web.process_status_event(None).msgs_to_remote
    assert message.get_api_call()['value']['app']['app_debug'] is True
    web.tm_endpoint.connected = False
    assert web.process_status_event(None).is_empty()


def test_tm_stores_web_status_without_queuing_config_or_reply():
    web, tm = web_stub(), tm_stub()
    [message] = web.process_tm_connected(SimpleNamespace(remote_conn=web.tm_endpoint.client_socket)).msgs_to_remote
    # Actual dispatch adds the API header echo wrapper. Status needs no request ID.
    message.add_echo_api_header()
    TM_WEB().validate(message.get_json_api_header())
    action = tm.process_web_msg(SimpleNamespace(remote_conn=object()), message.get_json_api_header(),
                               message.get_api_call(), None)
    assert action.is_empty()
    assert tm.queue.empty()
    assert tm.telmodel.web_svr.app.app_running is True
    assert tm.telmodel.web_svr.last_update == datetime.fromisoformat(message.get_timestamp().replace('Z', '+00:00'))
    assert tm.telmodel.web_svr.tm_connected == CommunicationStatus.ESTABLISHED
    assert tm.telmodel.tel_mgr.web_connected == CommunicationStatus.ESTABLISHED


@pytest.mark.parametrize('change', [
    {'from': 'tm', 'to': 'web'},
    {'api_call': {'msg_type': 'adv', 'action_code': 'get', 'property': 'status'}},
    {'api_call': {'msg_type': 'adv', 'action_code': 'set', 'property': 'status', 'value': {}}},
])
def test_invalid_status_advice_is_rejected(change):
    message = web_stub()._construct_status_adv_to_tm()
    with pytest.raises(XAPIValidationFailed):
        TM_WEB().validate(message.get_json_api_header() | change)


def test_web_connection_flags_remain_consistent_with_multiple_clients():
    tm = tm_stub()
    a, b = object(), object()
    tm.process_web_connected(SimpleNamespace(remote_conn=a, remote_addr=('host', 1)))
    tm.process_web_connected(SimpleNamespace(remote_conn=b, remote_addr=('host', 2)))
    tm.process_web_disconnected(SimpleNamespace(remote_conn=a, remote_addr=('host', 1)))
    assert tm.telmodel.web_svr.tm_connected == CommunicationStatus.ESTABLISHED
    assert tm.telmodel.tel_mgr.web_connected == CommunicationStatus.ESTABLISHED
    tm.process_web_disconnected(SimpleNamespace(remote_conn=b, remote_addr=('host', 2)))
    assert tm.telmodel.web_svr.tm_connected == CommunicationStatus.NOT_ESTABLISHED
    assert tm.telmodel.tel_mgr.web_connected == CommunicationStatus.NOT_ESTABLISHED


def test_concurrent_responses_are_matched_by_their_own_timers(timers):
    web = web_stub()
    processor = AppProcessor(event_q=web.queue, driver=web)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(web.submit_configuration, 'ODT', {'_type': 'ObsList', 'obs_list': []})
        processor.process_event(web.queue.get(timeout=1))
        a = web.tm_endpoint.send.call_args.args[0]
        second = workers.submit(web.submit_configuration, 'OET', {'_type': 'ObservationReset', 'obs_id': 'obs1'})
        processor.process_event(web.queue.get(timeout=1))
        b = web.tm_endpoint.send.call_args.args[0]
        assert len(web._request_timers()) == 2
        reply_b = configuration_response(b.get_json_api_header(), ResponseEvent('success', 'Second'))
        web.process_tm_msg(None, reply_b.get_json_api_header(), reply_b.get_api_call(), None)
        assert second.result(timeout=1)[0].message == 'Second'
        assert not first.done()
        reply_a = configuration_response(a.get_json_api_header(), ResponseEvent('success', 'First'))
        web.process_tm_msg(None, reply_a.get_json_api_header(), reply_a.get_api_call(), None)
        assert first.result(timeout=1)[0].message == 'First'


@pytest.mark.parametrize('tm_available', [True, False])
def test_real_web_application_http_tcp_config_completion_timeout_and_shutdown(tmp_path, timers, tm_available):
    """Run the production WebServer/Waitress lifecycle against a minimal live TM."""
    import os
    from pathlib import Path
    import socket
    import subprocess
    import sys
    import requests
    from env.events import DataEvent
    from ipc.tcp_server import TCPServer
    from models.comms import InterfaceType

    pytest.importorskip('waitress')
    tm = tm_stub()
    endpoint = TCPServer(description='web', queue=tm.queue, host='127.0.0.1', port=0)
    tm_port = endpoint.server_socket.getsockname()[1]
    tm.get_interface = lambda _: (TM_WEB(), endpoint, InterfaceType.APP_APP)
    processor = AppProcessor(name='test-tm-web', event_q=tm.queue, driver=tm)
    if tm_available:
        endpoint.start()
    processor.start()

    def free_port():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            return sock.getsockname()[1]

    http_port, cmd_port = free_port(), free_port()
    src = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env['WEBHOOK_SECRET'] = 'test-secret'
    env['WEB_COMMAND_ENDPOINTS'] = '{"web":{"host":"127.0.0.1","port":' + str(cmd_port) + '}}'
    env['PYTHONPATH'] = str(src) + os.pathsep + env.get('PYTHONPATH', '')
    logfile = tmp_path / 'web-test.log'
    headers = {'X-Webhook-Token': 'test-secret'}
    url = f'http://127.0.0.1:{http_port}'
    with logfile.open('w') as output:
        process = subprocess.Popen([
            sys.executable, '-m', 'web.web_server', '--profile_dir', str(tmp_path),
            '--tm_port', str(tm_port), '--http_port', str(http_port), '--cmd_port', str(cmd_port),
            '--request_timeout', '0.5', '--headless',
        ], cwd=src, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                assert process.poll() is None, logfile.read_text()
                try:
                    if requests.get(url + '/health', timeout=0.2).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.02)
            else:
                pytest.fail(logfile.read_text())

            request = {'event': 'alston-rt.ui.odt', 'message': {'_type': 'ObsList', 'obs_list': []}}
            response = requests.post(url + '/webhook', json=request, headers=headers, timeout=3)
            assert response.status_code == 200, response.text + logfile.read_text()

            own_command = requests.post(url + '/webhook', json={
                'event': 'alston-rt.ui.cmd', 'message': {'app': 'WEB', 'cmd': 'Resync'},
            }, headers=headers, timeout=5)
            assert own_command.status_code == 200
            assert own_command.json()['status'] == 'success', own_command.text + logfile.read_text()

            if not tm_available:
                assert response.json()['code'] == 'not_connected'
                assert requests.get(url + '/health', timeout=1).status_code == 200
                process.terminate()
                process.wait(timeout=8)
                assert process.returncode == 0, logfile.read_text()
                return

            assert response.json()['status'] == 'success', response.text + logfile.read_text()
            assert tm.telmodel.web_svr.app.app_running is True
            assert tm.telmodel.web_svr.app.arguments['http_port'] == http_port
            assert response.json()['payload']['phase'] == 'actions_scheduled'
            tm.oet.start_next_obs_timer.assert_called_once()

            invalid = requests.post(url + '/webhook', json={
                'event': 'alston-rt.ui.dig', 'message': {'dig_id': 'unknown'},
            }, headers=headers, timeout=3)
            assert invalid.status_code == 200
            assert invalid.json()['status'] == 'error'
            assert 'known dig_id' in invalid.json()['message']

            processor.stop()
            processor.join(timeout=2)
            timeout = requests.post(url + '/webhook', json=request, headers=headers, timeout=3)
            assert timeout.status_code == 200
            assert timeout.json()['code'] == 'timeout'
            with tm.queue.mutex:
                assert sum(isinstance(event, DataEvent) for event in tm.queue.queue) == 1
            assert requests.get(url + '/health', timeout=1).status_code == 200

            # Outstanding requests also complete during clean signal shutdown.
            with ThreadPoolExecutor(max_workers=1) as worker:
                pending = worker.submit(requests.post, url + '/webhook', json=request, headers=headers, timeout=3)
                deadline = time.monotonic() + 1
                while tm.queue.qsize() < 2 and time.monotonic() < deadline:
                    time.sleep(0.01)
                process.terminate()
                assert pending.result(timeout=3).json()['code'] == 'shutting_down'
            process.wait(timeout=8)
            assert process.returncode == 0, logfile.read_text()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            processor.stop()
            processor.join(timeout=2)
            endpoint.stop()
            endpoint.server_socket.close()
            endpoint.sel.close()
