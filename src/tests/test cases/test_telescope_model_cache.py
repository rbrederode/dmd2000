"""One timer-driven model snapshot supplies every WEB status caller."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from api import tm_web
from env.events import ResponseEvent
from ipc.message import APIMessage
from models.telescope import TelescopeModel
from web.model_cache import TelescopeModelCache, POLL_TIMER, REQUEST_PREFIX, POLL_SECONDS
from web.model_snapshot import telescope_snapshot
from test_tm_web import tm_stub, web_stub


def reply(request, payload=None, status='success'):
    return tm_web.configuration_response(request.get_json_api_header(), ResponseEvent(
        status, 'Snapshot received', payload=payload if payload is not None else {
            'telescope_model': {'tel_mgr': {'id': 'tm001'}}, 'profile': 'local', 'snapshot_at': datetime.now(timezone.utc).isoformat()}))


def complete(cache):
    action = cache.poll(True, 10)
    request = action.msgs_to_remote[0]
    response = reply(request)
    cache.complete(response.get_json_api_header(), response.get_api_call())
    return request


def test_model_protocol_round_trip_and_get_only():
    request = tm_web.model_request('req-id')
    request.add_echo_api_header()
    response = reply(request)
    tm_web.TM_WEB().validate(response.get_json_api_header())
    assert tm_web.get_request_id(response.get_json_api_header()) == 'req-id'
    assert response.get_api_call()['property'] == 'telescope_model'
    invalid = request.get_json_api_header().copy()
    invalid['api_call'] = dict(invalid['api_call'], action_code='set')
    with pytest.raises(Exception):
        tm_web.TM_WEB().validate(invalid)


def test_tm_serves_full_snapshot_without_config_event_and_redacts_credentials():
    tm = tm_stub()
    tm.app_model.arguments = {'profile': 'local', 'api_token': 'do-not-send', 'nested': {'password': 'hidden', 'normal': 4}}
    request = tm_web.model_request('snapshot')
    action = tm.process_web_msg(SimpleNamespace(), request.get_json_api_header(), request.get_api_call(), None)
    response = action.msgs_to_remote[0].get_api_call()
    assert response['status'] == 'success'
    payload = response['payload']
    assert payload['profile'] == 'local'
    assert set(payload['telescope_model']) == set(tm.telmodel.to_dict())
    args = payload['telescope_model']['tel_mgr']['app']['arguments']
    assert args['api_token'] == args['nested']['password'] == '[REDACTED]'
    assert args['nested']['normal'] == 4
    assert tm.app_model.arguments['api_token'] == 'do-not-send'
    assert tm.queue.empty()


def test_poll_is_single_inflight_and_fresh_cache_is_shared_detached():
    cache = TelescopeModelCache()
    request = complete(cache)
    first = cache.response(True)
    assert first.status == 'success' and first.payload['stale'] is False
    first.payload['telescope_model']['tel_mgr']['id'] = 'changed-by-caller'
    assert cache.response(True).payload['telescope_model']['tel_mgr']['id'] == 'tm001'
    pending = cache.poll(True, 10)
    assert len(pending.msgs_to_remote) == 1
    assert cache.poll(True, 10).is_empty()
    # Late/duplicate responses to the previous query must not finish the new one.
    previous = reply(request)
    assert cache.complete(previous.get_json_api_header(), previous.get_api_call()).is_empty()
    assert cache.pending is not None


def test_cache_retains_stale_snapshot_on_timeout_disconnect_and_age(monkeypatch):
    import web.model_cache as module
    cache = TelescopeModelCache()
    assert cache.response(False).code == 'model_unavailable'
    complete(cache)
    later = cache.received_monotonic + 36
    monkeypatch.setattr(module.time, 'monotonic', lambda: later)
    assert cache.response(True).payload['stale'] is True
    request = cache.poll(True, 1).msgs_to_remote[0]
    cache.timeout(tm_web.get_request_id(request.get_json_api_header()))
    assert 'timed out' in cache.response(True).payload['last_error']
    assert cache.response(True).payload['telescope_model']
    cache.disconnected()
    assert cache.response(False).payload['tm_connected'] is False
    assert cache.poll(False, 1).is_empty()
    complete(cache)
    assert cache.response(True).payload['stale'] is False


def test_web_starts_immediately_and_uses_periodic_timer_without_http_trigger():
    web = web_stub()
    web.tm_endpoint.start = Mock()
    init = web.process_init()
    web.tm_endpoint.start.assert_called_once_with()
    assert init.timer_actions[0].name == POLL_TIMER
    assert init.timer_actions[0].timer_action == 30000
    connected = web.process_tm_connected(SimpleNamespace(remote_conn=web._tm_connection))
    [query] = [msg for msg in connected.msgs_to_remote if msg.get_api_call()['msg_type'] == 'req']
    assert web.telescope_model_response().status == 'error'
    response = reply(query)
    web.process_tm_msg(None, response.get_json_api_header(), response.get_api_call(), None)
    assert web.telescope_model_response().payload['stale'] is False
    periodic = web.process_timer_event(SimpleNamespace(name=POLL_TIMER, timer_cancelled=False))
    assert periodic.timer_actions[-1].timer_action == POLL_SECONDS * 1000
    assert len(periodic.msgs_to_remote) == 1
    for _ in range(4):
        assert web.telescope_model_response().status == 'success'
    assert web._model_cache().pending == tm_web.get_request_id(periodic.msgs_to_remote[0].get_json_api_header())


def test_web_model_cache_http_auth_and_repeated_reads_do_not_query_tm(monkeypatch, tmp_path):
    monkeypatch.setenv('WEBHOOK_SECRET', 'model-test-secret')
    monkeypatch.chdir(tmp_path)
    path = Path(__file__).resolve().parents[2] / 'web/web_server.py'
    spec = importlib.util.spec_from_file_location('web_model_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.limiter.enabled = False
    web = web_stub()
    module.app.extensions['web_application'] = web
    client = module.app.test_client()
    assert client.get('/api/telescope/model').status_code == 401
    headers = {'X-Webhook-Token': 'model-test-secret'}
    assert client.get('/api/telescope/model', headers=headers).status_code == 503
    complete(web._model_cache())
    for _ in range(3):
        response = client.get('/api/telescope/model', headers=headers)
        assert response.status_code == 200 and response.json['payload']['stale'] is False
    assert web.queue.empty()
    web.tm_endpoint.connected = False
    assert client.get('/api/telescope/model', headers=headers).json['payload']['stale'] is True
    web.telescope_model_response = Mock(return_value=ResponseEvent('success', 'Fresh snapshot', payload={'telescope_model': {'fresh': True}}))
    fresh = client.get('/api/telescope/model?refresh=1', headers=headers)
    assert fresh.status_code == 200 and fresh.json['payload']['telescope_model']['fresh']
    web.telescope_model_response.assert_called_once_with(refresh=True)
    assert client.get('/api/telescope/model?refresh=invalid', headers=headers).status_code == 400
    assert web.telescope_model_response.call_count == 1


def test_model_response_survives_large_wire_round_trip_and_reconnect():
    request = tm_web.model_request('large')
    message = reply(request, {'telescope_model': {'data': 'x' * 100000}, 'profile': 'local'})
    encoded = message.to_data()
    decoded = APIMessage()
    decoded.from_data(encoded)
    tm_web.TM_WEB().validate(decoded.get_json_api_header())
    assert decoded.get_api_call()['payload']['telescope_model']['data'] == 'x' * 100000
    web = web_stub()
    event = SimpleNamespace(remote_conn=web._tm_connection)
    first = web.process_tm_connected(event)
    old = next(msg for msg in first.msgs_to_remote if msg.get_api_call()['msg_type'] == 'req')
    # Reconnection replaces the outstanding request even if the old disconnect was delayed.
    second = web.process_tm_connected(event)
    new = next(msg for msg in second.msgs_to_remote if msg.get_api_call()['msg_type'] == 'req')
    assert tm_web.get_request_id(old.get_json_api_header()) != tm_web.get_request_id(new.get_json_api_header())
    late = reply(old)
    assert web.process_tm_msg(None, late.get_json_api_header(), late.get_api_call(), None).is_empty()
    fresh = reply(new)
    web.process_tm_msg(None, fresh.get_json_api_header(), fresh.get_api_call(), None)
    assert web.telescope_model_response().payload['stale'] is False


def test_control_refresh_supersedes_older_snapshot_in_flight():
    cache = TelescopeModelCache()
    old_request = cache.poll(True, 10).msgs_to_remote[0]
    old_id = cache.pending
    fresh = cache.poll(True, 10, force=True)
    assert fresh.timer_actions[0].name == REQUEST_PREFIX + old_id
    assert fresh.timer_actions[0].timer_action == -1
    assert cache.pending != old_id
    old_response = reply(old_request)
    assert cache.complete(old_response.get_json_api_header(), old_response.get_api_call()).is_empty()
    assert cache.snapshot is None
    fresh_response = reply(fresh.msgs_to_remote[0])
    cache.complete(fresh_response.get_json_api_header(), fresh_response.get_api_call())
    assert cache.response(True).payload['stale'] is False


@pytest.mark.parametrize('failure', ['timeout', 'disconnect', 'error'])
def test_fresh_snapshot_waiter_reports_failure_instead_of_returning_old_data(failure):
    from concurrent.futures import Future
    cache = TelescopeModelCache()
    complete(cache)
    waiter = Future()
    action = cache.poll(True, 10, force=True, waiter=waiter)
    assert not waiter.done()
    if failure == 'timeout':
        cache.timeout(cache.pending)
    elif failure == 'disconnect':
        cache.disconnected()
    else:
        response = reply(action.msgs_to_remote[0], status='error')
        cache.complete(response.get_json_api_header(), response.get_api_call())
    assert waiter.result(timeout=1).status == 'error'
    assert cache.snapshot is not None
    assert cache.waiters == []


def test_manual_refresh_waits_for_new_tm_reply_and_updates_shared_cache():
    from concurrent.futures import ThreadPoolExecutor
    web = web_stub()
    complete(web._model_cache())
    with ThreadPoolExecutor(max_workers=1) as worker:
        waiting = worker.submit(web.telescope_model_response, refresh=True)
        callback = web.queue.get(timeout=1)
        action = callback.callback()
        assert not waiting.done()
        request = action.msgs_to_remote[0]
        response = reply(request, {'telescope_model': {'new_snapshot': True}})
        web.process_tm_msg(None, response.get_json_api_header(), response.get_api_call(), None)
        result = waiting.result(timeout=1)
    assert result.status == 'success'
    assert result.payload['telescope_model'] == {'new_snapshot': True}
    assert web.telescope_model_response().payload['telescope_model'] == {'new_snapshot': True}


def test_ui_bridge_forwards_explicit_refresh_parameter(tmp_path, monkeypatch):
    from ui import opt_service
    request = Mock(return_value=SimpleNamespace(status_code=200, headers={}, json=lambda: {'status':'success', 'message':'Fresh snapshot', 'payload':{}}))
    monkeypatch.setattr(opt_service.requests, 'request', request)
    app = opt_service.create_app([tmp_path], tmp_path/'cache', web_token='test')
    try:
        client = app.test_client()
        assert client.get('/api/web/telescope/model?refresh=1').status_code == 200
        assert request.call_args.kwargs['params'] == {'refresh':'1'}
        assert request.call_args.kwargs['timeout'] == (3, 30)
        request.reset_mock()
        assert client.get('/api/web/telescope/model?refresh=wrong').status_code == 400
        request.assert_not_called()
    finally:
        app.extensions['opt_jobs'].close()
