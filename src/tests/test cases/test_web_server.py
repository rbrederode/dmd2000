"""Exercise authentication, direct command dispatch and TCP configuration requests."""

import hashlib
import hmac
import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv('WEBHOOK_SECRET', 'test-webhook-secret')
    monkeypatch.chdir(tmp_path)
    path = Path(__file__).resolve().parents[2] / 'web' / 'web_server.py'
    spec = importlib.util.spec_from_file_location('web_server_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    module.limiter.enabled = False
    post = Mock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(requests, 'post', post)
    return module, module.app.test_client(), post


@pytest.mark.parametrize('headers', [
    {},
    {'X-Webhook-Token': 'wrong-token'},
    {'X-Webhook-Signature': 'sha256=bad-signature'},
    {'X-Webhook-Signature': 'non-ascii-\u00e9'},
])
def test_authentication_failures_are_401_and_not_forwarded(server, headers):
    _, client, post = server
    response = client.post('/webhook', json={'event': 'test'}, headers=headers)
    assert response.status_code == 401
    assert response.json['status'] == 'error'
    post.assert_not_called()


def test_credentials_and_payload_are_not_logged(server, caplog):
    module, client, _ = server
    headers = {
        'X-Webhook-Token': module.WEBHOOK_SECRET,
        'Authorization': 'Bearer private-authorization',
        'Cookie': 'private-cookie',
        'X-Api-Key': 'private-api-key',
    }
    with caplog.at_level(logging.DEBUG):
        response = client.post('/webhook', json={'event': 'test', 'message': 'private-payload'},
                               headers=headers)
    assert response.status_code == 400
    for value in [*headers.values(), 'private-payload']:
        assert value not in caplog.text
    assert '[REDACTED]' in caplog.text


def test_valid_hmac_submits_configuration_without_logging_signature(server, caplog):
    from env.events import ResponseEvent
    module, client, post = server
    runtime = SimpleNamespace(submit_configuration=Mock(return_value=(
        ResponseEvent('success', 'Actions scheduled'), 'request-1')))
    module.app.extensions['web_application'] = runtime
    body = b'{"event":"alston-rt.ui.odt","message":{"_type":"ObsList","obs_list":[]}}'
    signature = hmac.new(module.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    with caplog.at_level(logging.INFO):
        response = client.post('/webhook', data=body, content_type='application/json',
                               headers={'X-Hub-Signature-256': 'sha256=' + signature})
    assert response.status_code == 200
    assert signature not in caplog.text
    runtime.submit_configuration.assert_called_once_with('ODT', {'_type': 'ObsList', 'obs_list': []})
    post.assert_not_called()


@pytest.mark.parametrize('status, code', [(200, None), (400, 'invalid_request'),
                                        (500, 'internal_error'), (200, 'command_failed'), (200, 'timeout')])
def test_command_result_is_returned_without_contacting_tm_http(server, monkeypatch, status, code):
    from env.events import ResponseEvent

    module, client, post = server
    result = ResponseEvent('success' if code is None else 'error', 'Target reply',
                           payload={'app': 'TM', 'response': {'message': 'Target reply'}}, code=code)
    execute = Mock(return_value=result)
    monkeypatch.setattr(module.cmd_handler, 'execute', execute)
    payload = {'event': 'alston-rt.ui.cmd', 'message': {'app': 'TM', 'cmd': 'Trace'}}
    response = client.post('/webhook', json=payload,
                           headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == status
    assert response.json['request_id']
    assert {key: value for key, value in response.json.items() if key != 'request_id'} == result.to_dict()
    execute.assert_called_once_with(payload['message'])
    post.assert_not_called()


@pytest.mark.parametrize('body', ['{broken', [], None])
def test_invalid_command_message_is_400_without_contacting_tm(server, body):
    module, client, post = server
    response = client.post('/webhook', json={'event': 'alston-rt.ui.cmd', 'message': body},
                           headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 400
    assert response.json['code'] == 'invalid_request'
    post.assert_not_called()


def test_commands_require_authentication_before_execution(server, monkeypatch):
    module, client, post = server
    execute = Mock()
    monkeypatch.setattr(module.cmd_handler, 'execute', execute)
    response = client.post('/webhook', json={'event': 'alston-rt.ui.cmd',
                                           'message': {'app': 'SDP', 'cmd': 'Resync'}})
    assert response.status_code == 401
    execute.assert_not_called()
    post.assert_not_called()


@pytest.mark.parametrize('category', ['dig', 'dsh', 'odt', 'oet'])
def test_configuration_submits_to_web_application_without_tm_http(server, monkeypatch, category):
    from env.events import ResponseEvent
    module, client, post = server
    runtime = SimpleNamespace(submit_configuration=Mock(return_value=(
        ResponseEvent('success', 'Actions scheduled'), 'request-1')))
    module.app.extensions['web_application'] = runtime
    config = {'_type': 'Configuration'}
    response = client.post('/webhook', json={
        'event': 'alston-rt.ui.' + category, 'message': json.dumps(config),
    }, headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 200
    assert response.json['status'] == 'success'
    assert response.json['request_id'] == 'request-1'
    runtime.submit_configuration.assert_called_once_with(category.upper(), config)
    post.assert_not_called()


@pytest.mark.parametrize('encoded', [False, True])
def test_flattened_observation_rows_are_rejected_before_tcp_submission(server, encoded):
    module, client, post = server
    runtime = SimpleNamespace(submit_configuration=Mock())
    module.app.extensions['web_application'] = runtime
    rows = [['_type', 'ObsList'], ['obs_list[0].obs_id', 'obs001']]
    response = client.post('/webhook', json={
        'event': 'alston-rt.ui.odt', 'message': json.dumps(rows) if encoded else rows,
    }, headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 400
    assert response.json['message'] == 'Configuration message must be an object'
    runtime.submit_configuration.assert_not_called()
    post.assert_not_called()


def test_resync_uses_real_command_tcp_endpoint_without_tm_http(server, monkeypatch, tmp_path):
    from queue import Queue
    from api.cmd_api import CommandAPI
    from env.app_processor import AppProcessor
    from ipc.action import Action
    from ipc.tcp_server import TCPServer
    from models.app import AppModel
    from models.comms import InterfaceType
    from web.cmd_handler import CommandHandler

    module, client, post = server
    queue = Queue()
    endpoint = TCPServer(description='cmd', queue=queue, host='127.0.0.1', port=0)
    port = endpoint.server_socket.getsockname()[1]
    api = CommandAPI(app_name='sdp')
    resync = Mock(return_value=Action())
    driver = SimpleNamespace(
        app_model=AppModel(app_name='sdp', app_tracing=False),
        get_interface=lambda _: (api, endpoint, InterfaceType.APP_APP),
        set_last_err=lambda message: message,
        process_resync=resync,
    )
    processor = AppProcessor(name='test-sdp-command-server', event_q=queue, driver=driver)
    monkeypatch.setattr(module, 'cmd_handler', CommandHandler(
        profile_dir=tmp_path, endpoint_overrides={'SDP': {'host': '127.0.0.1', 'port': port}},
        timeout=2,
    ))
    endpoint.start()
    processor.start()
    try:
        response = client.post('/webhook', json={
            'event': 'alston-rt.ui.cmd',
            'message': json.dumps({'app': 'SDP', 'cmd': 'Resync'}),
        }, headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
        assert response.status_code == 200
        assert response.json['status'] == 'success'
        assert response.json['message'] == 'Application configuration resynchronised'
        assert response.json['payload']['app'] == 'SDP'
        assert response.json['payload']['response']['action_code'] == 'resync'
        resync.assert_called_once_with()
        post.assert_not_called()
    finally:
        processor.stop()
        processor.join(timeout=3)
        endpoint.stop()
        endpoint.server_socket.close()


def test_unavailable_tm_returns_json_timeout_from_real_command_client(server, monkeypatch, tmp_path):
    import socket
    from web.cmd_handler import CommandHandler

    module, client, post = server
    # Reserve a local port without listening: no running application can
    # accidentally receive this RESYNC and connections are refused reliably.
    with socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1', 0))
        port = unavailable.getsockname()[1]
        monkeypatch.setattr(module, 'cmd_handler', CommandHandler(
            profile_dir=tmp_path,
            endpoint_overrides={'TM': {'host': '127.0.0.1', 'port': port}}, timeout=0.05,
        ))
        response = client.post('/webhook', json={
            'event': 'alston-rt.ui.cmd', 'message': {'app': 'TM', 'cmd': 'Resync'},
        }, headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 200
    assert response.is_json
    assert response.json['status'] == 'error'
    assert response.json['code'] == 'timeout'
    assert response.json['message'] == 'Timed out connecting to target application'
    assert response.json['payload']['app'] == 'TM'
    assert response.json['request_id']
    post.assert_not_called()


@pytest.mark.parametrize('body', ['{broken', '[]', '{}'])
def test_invalid_payload_is_400_and_not_forwarded(server, body):
    module, client, post = server
    response = client.post('/webhook', data=body, content_type='application/json',
                           headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 400
    post.assert_not_called()


def test_catalogue_requires_authentication_before_reading_storage(server, monkeypatch):
    module, client, _ = server
    listing = Mock()
    monkeypatch.setattr(module, 'list_observations', listing)
    for headers in [{}, {'X-Webhook-Token': 'wrong'}]:
        assert client.get('/api/observations', headers=headers).status_code == 401
    listing.assert_not_called()


def test_catalogue_reads_profile_storage_without_tm(server, monkeypatch, tmp_path):
    from models.launch import LaunchConfigModel
    module, client, _ = server
    store = tmp_path / 'recordings'
    store.mkdir()
    (store / 'obs001-obs.json').write_text('{"_type":"ObsModel", "obs_id":"obs001"}')
    (tmp_path / 'LaunchConfig.json').write_text(json.dumps(
        LaunchConfigModel(profile='test', scan_store_dir=str(store)).to_dict()))
    monkeypatch.setattr(module.cmd_handler, 'profile_dir', tmp_path)
    network = Mock(side_effect=AssertionError('Catalogue must not contact TM'))
    monkeypatch.setattr(requests, 'get', network)
    response = client.get('/api/observations', headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 200
    assert response.json['payload']['count'] == 1
    assert response.json['payload']['observations'][0]['id'] == 'obs001'
    network.assert_not_called()


@pytest.mark.parametrize('directory, status, code', [
    ('', 400, 'invalid_request'), ('missing', 503, 'directory_unavailable'),
])
def test_catalogue_invalid_explicit_directory_does_not_fall_back(server, directory, status, code):
    module, client, _ = server
    response = client.get('/api/observations', query_string={'directory': directory},
                          headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == status
    assert response.json['code'] == code


def test_catalogue_missing_default_returns_response_event(server, monkeypatch, tmp_path):
    module, client, _ = server
    monkeypatch.setattr(module.cmd_handler, 'profile_dir', tmp_path)
    response = client.get('/api/observations', headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 503
    assert response.json['code'] == 'store_not_known'


def test_catalogue_explicit_directory_overrides_runtime_default(server, tmp_path):
    module, client, _ = server
    root = tmp_path / 'Observation archive'
    nested = root / '2026' / 'November'
    nested.mkdir(parents=True)
    (nested / 'obs001-obs.json').write_text('{"_type":"ObsModel", "obs_id":"obs001"}')
    module.app.extensions['web_application'] = SimpleNamespace(scan_store_dir='missing-default')
    response = client.get('/api/observations', query_string={'directory': str(root)},
                          headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 200
    assert response.json['payload']['count'] == 1
    assert response.json['payload']['observations'][0]['folder'] == '2026/November'


def test_catalogue_uses_runtime_storage_default(server, tmp_path):
    module, client, _ = server
    module.app.extensions['web_application'] = SimpleNamespace(scan_store_dir=str(tmp_path))
    response = client.get('/api/observations', headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 200
    assert response.json['payload'] == {'count': 0, 'observations': []}


def test_saved_observation_catalogue_and_scans_are_read_from_disk(server, tmp_path):
    module, client, _ = server
    folder = tmp_path / '2026' / 'November'
    folder.mkdir(parents=True)
    data = {'_type': 'ObsModel', 'obs_id': 'obs001', 'title': 'Rotation curve', 'dsh_id': 'dish003',
            'obs_state': {'value': 'READY'}, 'created': {'value': '2026-11-08T12:00:00Z'},
            'scheduling_block_start': {'value': '2026-11-08T13:00:00Z'},
            'scheduling_block_end': {'value': '2026-11-08T14:00:00Z'},
            'estimated_observation_duration': '00:07:30', 'user_email': 'observer@example.org',
            'target_scans': [{'tgt_idx': 0, 'scans': [{'status': {'value': 'COMPLETE'}, 'gain': 37}]}],
            'token': 'do-not-return'}
    (folder / 'obs001-obs.json').write_text(json.dumps(data))
    module.app.extensions['web_application'] = SimpleNamespace(scan_store_dir=str(tmp_path))
    headers = {'X-Webhook-Token': module.WEBHOOK_SECRET}
    assert client.get('/api/observations/obs001?folder=2026/November').status_code == 401
    catalogue = client.get('/api/observations', headers=headers).json['payload']['observations']
    assert len(catalogue) == 1
    assert catalogue[0]['created'] == '2026-11-08T12:00:00Z'
    assert catalogue[0]['scheduling_block_end'] == '2026-11-08T14:00:00Z'
    assert catalogue[0]['user_email'] == 'observer@example.org'
    assert catalogue[0]['completed'] is True
    assert catalogue[0]['completed_scans'] == catalogue[0]['scan_count'] == 1
    assert 'target_scans' not in catalogue[0]  # Scan lists load on expansion.
    response = client.get('/api/observations/obs001?folder=2026/November', headers=headers)
    assert response.status_code == 200
    assert response.json['payload']['observation']['target_scans'] == data['target_scans']
    assert response.json['payload']['observation']['token'] == '[REDACTED]'
    assert response.headers['Cache-Control'] == 'no-store'


def test_saved_observation_paths_and_invalid_files_are_handled(server, tmp_path):
    module, client, _ = server
    module.app.extensions['web_application'] = SimpleNamespace(scan_store_dir=str(tmp_path))
    headers = {'X-Webhook-Token': module.WEBHOOK_SECRET}
    outside = tmp_path.parent / (tmp_path.name + '-outside')
    outside.mkdir()
    (outside / 'obs001-obs.json').write_text('{"_type":"ObsModel"}')
    (tmp_path / 'linked').symlink_to(outside, target_is_directory=True)
    (tmp_path / 'linked-file-obs.json').symlink_to(outside / 'obs001-obs.json')
    for url in ['/api/observations/obs001?folder=../outside',
                '/api/observations/obs001?folder=/tmp',
                '/api/observations/obs001?folder=linked',
                '/api/observations/linked-file']:
        response = client.get(url, headers=headers)
        assert response.status_code == 400
    assert client.get('/api/observations/missing', headers=headers).status_code == 404
    (tmp_path / 'broken-obs.json').write_text('{incomplete')
    response = client.get('/api/observations/broken', headers=headers)
    assert response.status_code == 503 and response.json['code'] == 'invalid_metadata'
    entries = client.get('/api/observations', headers=headers).json['payload']['observations']
    assert entries[0]['error'] == 'Observation metadata is unavailable or invalid'


def test_history_rejects_a_changed_resource_profile(server, monkeypatch, tmp_path):
    module, client, _ = server
    monkeypatch.setattr(module, '_profiles', lambda: SimpleNamespace(current=SimpleNamespace(directory=tmp_path / 'rotation')))
    response = client.get('/api/observations', headers={
        'X-Webhook-Token': module.WEBHOOK_SECRET, 'X-Config-Profile': 'local'})
    assert response.status_code == 409 and response.json['code'] == 'profile_changed'


def test_completed_scan_csv_download_uses_saved_metadata_and_survives_archive_moves(server, tmp_path):
    module, client, _ = server
    folder = tmp_path / '2026' / 'November'; folder.mkdir(parents=True)
    scan = {'tgt_idx': 1, 'freq_scan': 2, 'scan_iter': 3, 'status': {'value': 'COMPLETE'},
            'files_prefix': 'recorded-spectrum', 'files_directory': '/old-machine/recordings'}
    data = {'_type': 'ObsModel', 'obs_id': 'observation-id', 'target_scans': [{'tgt_idx': 1, 'scans': [scan]}]}
    (folder / 'stored-filename-obs.json').write_text(json.dumps(data))
    csv = b'frequency,power\n1420000000,12.34\n'
    (folder / 'recorded-spectrum-spr.csv').write_bytes(csv)
    module.app.extensions['web_application'] = SimpleNamespace(scan_store_dir=str(tmp_path))
    headers = {'X-Webhook-Token': module.WEBHOOK_SECRET}
    path = '/api/observations/stored-filename/scans/1/2/3/spr?folder=2026/November'
    assert client.get(path).status_code == 401
    for url in [path, '/api/observations/observation-id/scans/1/2/3/spr']:
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.data == csv
        assert response.mimetype == 'text/csv'
        assert 'attachment' in response.headers['Content-Disposition']
        assert 'recorded-spectrum-spr.csv' in response.headers['Content-Disposition']
        assert response.headers['Cache-Control'] == 'no-store'
        response.close()


def test_scan_download_rejects_incomplete_missing_and_outside_archive_files(server, tmp_path):
    module, client, _ = server
    module.app.extensions['web_application'] = SimpleNamespace(scan_store_dir=str(tmp_path))
    headers = {'X-Webhook-Token': module.WEBHOOK_SECRET}
    scan = {'tgt_idx': 0, 'freq_scan': 0, 'scan_iter': 0, 'status': {'value': 'WIP'}, 'files_prefix': 'test'}
    data = {'_type': 'ObsModel', 'obs_id': 'obs001', 'target_scans': [{'scans': [scan]}]}
    metadata = tmp_path / 'obs001-obs.json'
    def download():
        metadata.write_text(json.dumps(data))
        return client.get('/api/observations/obs001/scans/0/0/0/spr?folder=.', headers=headers)
    assert download().status_code == 409
    scan['status'] = {'value': 'COMPLETE'}
    assert download().status_code == 404
    scan['files_prefix'] = '../private'
    assert download().status_code == 400
    scan['files_prefix'] = 'test'
    outside = tmp_path.parent / (tmp_path.name + '-outside'); outside.mkdir()
    (outside / 'test-spr.csv').write_text('private')
    scan['files_directory'] = str(outside)
    assert download().status_code == 404
    (tmp_path / 'test-spr.csv').symlink_to(outside / 'test-spr.csv')
    assert download().status_code == 404
    scan['files_directory'] = None
    response = client.get('/api/observations/obs001/scans/0/0/0/spr?folder=..', headers=headers)
    assert response.status_code == 400
    assert client.get('/api/observations/obs001/scans/9/0/0/spr?folder=.', headers=headers).status_code == 404


def test_http_catalogue_trace_records_request_response_and_redacts_credentials(server, tmp_path):
    import html
    import re
    from util.html_trace import HTMLTrace
    module, client, _ = server
    trace = HTMLTrace(tmp_path / 'trace', 'web', 'test')
    trace.start()
    module.app.extensions['web_application'] = SimpleNamespace(
        scan_store_dir=str(tmp_path), trace=trace,
        app_model=SimpleNamespace(app_tracing=True))
    response = client.get('/api/observations', headers={
        'X-Webhook-Token': module.WEBHOOK_SECRET, 'Authorization': 'Bearer private-value'})
    trace.stop()
    assert response.status_code == 200
    content = html.unescape(trace.path.read_text())
    assert '[HTTP REQ]' in content and '[HTTP RSP]' in content
    assert 'GET /api/observations' in content
    assert 'status=200' in content and 'elapsed_ms' in content
    assert 'Found 0 observations' in content
    ids = re.findall(r'request_id=([a-f0-9]+)', content)
    assert len(ids) == 2 and ids[0] == ids[1]
    assert module.WEBHOOK_SECRET not in content
    assert 'private-value' not in content
    assert '[REDACTED]' in content


@pytest.mark.parametrize('enabled', [True, False])
def test_http_trace_authentication_errors_and_disabled_tracing(server, tmp_path, enabled):
    from util.html_trace import HTMLTrace
    module, client, _ = server
    trace = HTMLTrace(tmp_path / 'trace', 'web', 'test')
    trace.start()
    module.app.extensions['web_application'] = SimpleNamespace(
        trace=trace, app_model=SimpleNamespace(app_tracing=enabled))
    response = client.get('/api/observations', headers={'X-Webhook-Token': 'wrong-token'})
    trace.stop()
    assert response.status_code == 401
    content = trace.path.read_text()
    assert ('[HTTP REQ]' in content) == enabled
    assert ('status=401' in content) == enabled
    assert 'wrong-token' not in content


def test_http_trace_failure_does_not_break_response(server, tmp_path):
    module, client, _ = server
    module.app.extensions['web_application'] = SimpleNamespace(
        scan_store_dir=str(tmp_path), app_model=SimpleNamespace(app_tracing=True),
        trace=SimpleNamespace(is_started=True, log_http=Mock(side_effect=OSError('disk failure'))))
    response = client.get('/api/observations', headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 200
    assert response.json['status'] == 'success'


def test_completed_scan_signal_png_download_and_archive_guards(server, tmp_path):
    module, client, _ = server
    folder = tmp_path / 'archive'; folder.mkdir()
    scan = {'tgt_idx': 1, 'freq_scan': 2, 'scan_iter': 3, 'status': {'value': 'COMPLETE'},
            'files_prefix': 'recorded', 'files_directory': '/old-machine/recordings'}
    data = {'_type': 'ObsModel', 'obs_id': 'observation-id', 'target_scans': [{'scans': [scan]}]}
    metadata = folder / 'stored-obs.json'
    metadata.write_text(json.dumps(data))
    png = b'\x89PNG\r\n\x1a\nfinal-sample-image'
    image = folder / 'recorded-sigfig.png'; image.write_bytes(png)
    module.app.extensions['web_application'] = SimpleNamespace(scan_store_dir=str(tmp_path))
    headers = {'X-Webhook-Token': module.WEBHOOK_SECRET}
    path = '/api/observations/stored/scans/1/2/3/signal?folder=archive'
    assert client.get(path).status_code == 401
    response = client.get(path, headers=headers)
    assert response.status_code == 200 and response.data == png
    assert response.mimetype == 'image/png'
    assert 'recorded-sigfig.png' in response.headers['Content-Disposition']
    response.close()
    scan['status'] = {'value': 'WIP'}; metadata.write_text(json.dumps(data))
    assert client.get(path, headers=headers).status_code == 409
    scan['status'] = {'value': 'COMPLETE'}; metadata.write_text(json.dumps(data))
    image.unlink()
    assert client.get(path, headers=headers).status_code == 404
    outside = tmp_path.parent / (tmp_path.name + '-outside.png'); outside.write_bytes(png)
    image.symlink_to(outside)
    assert client.get(path, headers=headers).status_code == 404
