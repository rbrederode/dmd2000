"""Command HTTP requests wait on per-event replies without holding TM workers."""

from concurrent.futures import Future, ThreadPoolExecutor
from queue import Queue
from threading import Event
from unittest.mock import Mock

import pytest

from env.events import ConfigEvent, ResponseEvent
from models.telescope import TelescopeModel
from tm import tm as tm_module
from tm.tm import TelescopeManager
from tm.web_api import WebAPIHandler


@pytest.fixture
def manager():
    manager = TelescopeManager.__new__(TelescopeManager)
    manager.stop = lambda: None
    manager.set_last_err = lambda message: message
    manager.telmodel = TelescopeModel()
    return manager


def post_command(handler, command='Trace'):
    with handler.app.test_client() as client:
        return client.post('/webhook', json={
            'event': 'alston-rt.ui.cmd',
            'message': {'app': 'TM', 'cmd': command},
        })


@pytest.mark.parametrize('command', ['Trace', 'Debug', 'Resync'])
def test_tm_releases_worker_and_http_waits_for_target_response(manager, monkeypatch, command):
    queue = Queue()
    handler = WebAPIHandler(queue, command_wait_timeout=3)
    started, release, finished = Event(), Event(), Event()
    target_response = {'status': 'success', 'message': 'Applied', 'value': 'ON'}

    def run(argv, *, response_callback):
        started.set()
        try:
            assert release.wait(3)
            response_callback(target_response)
            return 0
        finally:
            finished.set()

    monkeypatch.setattr(tm_module.cmd_app, 'main', run)
    with ThreadPoolExecutor(max_workers=1) as http:
        request = http.submit(post_command, handler, command)
        event = queue.get(timeout=3)
        try:
            manager.process_config(event)
            assert started.wait(1)
            # TM has returned while cmd_app and the HTTP request still wait.
            assert not finished.is_set()
            assert not request.done()
        finally:
            release.set()
        response = request.result(timeout=3)
    assert response.status_code == 200
    assert response.json['payload']['response'] == target_response
    assert response.json['payload']['app'] == 'TM'
    assert response.json['request_id']


def test_concurrent_http_requests_receive_their_own_out_of_order_results():
    queue = Queue()
    handler = WebAPIHandler(queue, command_wait_timeout=3)
    with ThreadPoolExecutor(max_workers=2) as http:
        first = http.submit(post_command, handler, 'Trace')
        a = queue.get(timeout=3)
        second = http.submit(post_command, handler, 'Debug')
        b = queue.get(timeout=3)
        b.rsp_future.set_result(ResponseEvent('success', 'Debug reply', payload={'value': 'debug'}))
        assert second.result(timeout=3).json['payload']['value'] == 'debug'
        assert not first.done()
        a.rsp_future.set_result(ResponseEvent('success', 'Trace reply', payload={'value': 'trace'}))
        assert first.result(timeout=3).json['payload']['value'] == 'trace'


@pytest.mark.parametrize('code, expected_status', [
    ('invalid_request', 400), ('timeout', 504), ('command_failed', 502),
    ('dispatch_failed', 500), ('unrecognised_error', 500),
])
def test_web_api_maps_application_error_codes_to_http(code, expected_status):
    queue = Queue()
    handler = WebAPIHandler(queue, command_wait_timeout=3)
    result = ResponseEvent('error', 'Application error', code=code)
    with ThreadPoolExecutor(max_workers=1) as http:
        request = http.submit(post_command, handler)
        event = queue.get(timeout=3)
        event.rsp_future.set_result(result)
        response = request.result(timeout=3)
    assert response.status_code == expected_status
    assert response.json['status'] == 'error'
    assert response.json['code'] == code
    assert response.json['message'] == 'Application error'


def test_timeout_cancels_a_queued_command_before_dispatch(manager, monkeypatch):
    queue = Queue()
    handler = WebAPIHandler(queue, command_wait_timeout=0.01)
    response = post_command(handler)
    event = queue.get_nowait()
    run = Mock()
    monkeypatch.setattr(tm_module.cmd_app, 'main', run)
    manager.process_config(event)
    assert response.status_code == 504
    assert response.json['payload']['cancelled_before_dispatch'] is True
    assert event.rsp_future.cancelled()
    run.assert_not_called()


def test_running_command_can_finish_after_http_timeout():
    queue = Queue()
    handler = WebAPIHandler(queue, command_wait_timeout=0.1)
    with ThreadPoolExecutor(max_workers=1) as http:
        request = http.submit(post_command, handler)
        event = queue.get(timeout=3)
        assert event.rsp_future.set_running_or_notify_cancel()
        response = request.result(timeout=3)
    assert response.status_code == 504
    assert response.json['payload']['cancelled_before_dispatch'] is False
    assert 'unknown' in response.json['message']
    event.rsp_future.set_result(ResponseEvent('success', 'Completed'))


def test_invalid_command_is_reported_without_starting_cmd_app(manager, monkeypatch):
    run = Mock()
    monkeypatch.setattr(tm_module.cmd_app, 'main', run)
    reply = Future()
    manager.process_config(ConfigEvent(category='CMD', new_config={'app': 'unknown'},
                                       rsp_future=reply))
    result = reply.result(timeout=1)
    assert isinstance(result, ResponseEvent)
    assert result.code == 'invalid_request'
    assert result.status == 'error'
    run.assert_not_called()


@pytest.mark.parametrize('response, expected', [
    ({'status': 'error', 'message': 'Rejected'}, 'command_failed'),
    ({'status': 'error', 'error': 'timeout', 'message': 'No reply'}, 'timeout'),
])
def test_target_errors_reach_future(manager, monkeypatch, response, expected):
    def run(argv, *, response_callback):
        response_callback(response)
        return 1

    monkeypatch.setattr(tm_module.cmd_app, 'main', run)
    reply = Future()
    manager._run_app_command([], 'TM', reply)
    result = reply.result(timeout=1)
    assert isinstance(result, ResponseEvent)
    assert result.status == 'error'
    assert result.code == expected
    assert result.payload['response'] == response


@pytest.mark.parametrize('error', [RuntimeError('connection failed'), SystemExit(2)])
def test_command_thread_failure_always_completes_future(manager, monkeypatch, error):
    monkeypatch.setattr(tm_module.cmd_app, 'main', Mock(side_effect=error))
    reply = Future()
    manager._run_app_command([], 'TM', reply)
    result = reply.result(timeout=1)
    assert result.code == 'command_failed'
    assert result.status == 'error'
