"""Exercise authentication, log redaction and TM forwarding without network calls."""

import hashlib
import hmac
import importlib.util
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
    monkeypatch.setattr(module.requests, 'post', post)
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
    assert response.status_code == 200
    for value in [*headers.values(), 'private-payload']:
        assert value not in caplog.text
    assert '[REDACTED]' in caplog.text


def test_valid_hmac_is_forwarded_without_logging_signature(server, caplog):
    module, client, post = server
    body = b'{"event":"test"}'
    signature = hmac.new(module.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    with caplog.at_level(logging.INFO):
        response = client.post('/webhook', data=body, content_type='application/json',
                               headers={'X-Hub-Signature-256': 'sha256=' + signature})
    assert response.status_code == 200
    assert signature not in caplog.text
    post.assert_called_once_with('http://localhost:5001/webhook', json={'event': 'test'},
                                 timeout=5, allow_redirects=False)


@pytest.mark.parametrize('error, status', [
    (requests.exceptions.ConnectionError(), 502),
    (requests.exceptions.Timeout(), 504),
    (requests.exceptions.ConnectTimeout(), 504),
    (requests.exceptions.RequestException(), 502),
])
def test_forwarding_errors_are_reported(server, error, status):
    module, client, post = server
    post.side_effect = error
    response = client.post('/webhook', json={'event': 'test'},
                           headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == status
    assert response.json['status'] == 'error'


@pytest.mark.parametrize('upstream_status', [301, 400, 401, 500, 503])
def test_tm_rejection_is_reported(server, upstream_status):
    module, client, post = server
    post.return_value.status_code = upstream_status
    response = client.post('/webhook', json={'event': 'test'},
                           headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 502
    assert response.json['upstream_status'] == upstream_status
    assert response.json['status'] == 'error'


@pytest.mark.parametrize('status', [200, 400, 500, 502, 504])
def test_command_result_is_preserved_through_proxy(server, status):
    module, client, post = server
    body = {'status': 'success' if status == 200 else 'error',
            'request_id': 'test-request', 'response': {'message': 'Target reply'}}
    post.return_value = SimpleNamespace(status_code=status, json=lambda: body)
    payload = {'event': 'alston-rt.ui.cmd', 'message': {'app': 'TM', 'cmd': 'Trace'}}
    response = client.post('/webhook', json=payload,
                           headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == status
    assert response.json == body
    post.assert_called_once_with('http://localhost:5001/webhook', json=payload,
                                 timeout=20, allow_redirects=False)


@pytest.mark.parametrize('status, body', [(302, {}), (200, []), (200, {})])
def test_invalid_command_upstream_result_is_502(server, status, body):
    module, client, post = server
    post.return_value = SimpleNamespace(status_code=status, json=lambda: body)
    response = client.post('/webhook', json={'event': 'alston-rt.ui.cmd', 'message': {}},
                           headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 502


@pytest.mark.parametrize('body', ['{broken', '[]', '{}'])
def test_invalid_payload_is_400_and_not_forwarded(server, body):
    module, client, post = server
    response = client.post('/webhook', data=body, content_type='application/json',
                           headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 400
    post.assert_not_called()


def test_catalogue_requires_authentication_before_contacting_tm(server, monkeypatch):
    module, client, _ = server
    get = Mock()
    monkeypatch.setattr(module.requests, 'get', get)
    for headers in [{}, {'X-Webhook-Token': 'wrong'}]:
        assert client.get('/api/observations', headers=headers).status_code == 401
    get.assert_not_called()


def test_catalogue_proxy_end_to_end(server, monkeypatch, tmp_path):
    from queue import Queue
    from tm.web_api import WebAPIHandler

    module, client, _ = server
    (tmp_path / 'obs001-obs.json').write_text('{"_type":"ObsModel", "obs_id":"obs001"}')
    tm = WebAPIHandler(Queue(), scan_store_provider=lambda: tmp_path).app.test_client()

    def forward(url, **kwargs):
        assert url == 'http://localhost:5001/api/observations'
        assert kwargs == {'timeout': 5, 'allow_redirects': False}
        response = tm.get('/api/observations')
        return SimpleNamespace(status_code=response.status_code, json=lambda: response.json)

    monkeypatch.setattr(module.requests, 'get', forward)
    response = client.get('/api/observations', headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 200
    assert response.json['count'] == 1
    assert response.json['observations'][0]['id'] == 'obs001'


@pytest.mark.parametrize('error, status', [
    (requests.exceptions.ConnectionError(), 502),
    (requests.exceptions.Timeout(), 504),
])
def test_catalogue_proxy_connection_errors(server, monkeypatch, error, status):
    module, client, _ = server
    monkeypatch.setattr(module.requests, 'get', Mock(side_effect=error))
    response = client.get('/api/observations', headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == status
    assert response.json['status'] == 'error'


@pytest.mark.parametrize('upstream_status, payload, expected', [
    (400, {}, 400), (503, {}, 503), (500, {}, 502), (302, {}, 502), (200, [], 502), (200, {}, 502),
])
def test_catalogue_proxy_upstream_errors(server, monkeypatch, upstream_status, payload, expected):
    module, client, _ = server
    get = Mock(return_value=SimpleNamespace(status_code=upstream_status, json=lambda: payload))
    monkeypatch.setattr(module.requests, 'get', get)
    response = client.get('/api/observations', headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == expected


def test_catalogue_proxy_forwards_explicit_directory_and_returns_nested_folders(server, monkeypatch, tmp_path):
    from queue import Queue
    from tm.web_api import WebAPIHandler

    module, client, _ = server
    root = tmp_path / 'Observation archive'
    nested = root / '2026' / 'November'
    nested.mkdir(parents=True)
    (nested / 'obs001-obs.json').write_text('{"_type":"ObsModel", "obs_id":"obs001"}')
    tm = WebAPIHandler(Queue()).app.test_client()

    def forward(url, **kwargs):
        assert url == 'http://localhost:5001/api/observations'
        assert kwargs == {'params': {'directory': str(root)}, 'timeout': 5, 'allow_redirects': False}
        response = tm.get('/api/observations', query_string=kwargs['params'])
        return SimpleNamespace(status_code=response.status_code, json=lambda: response.json)

    get = Mock(side_effect=forward)
    monkeypatch.setattr(module.requests, 'get', get)
    # Authentication is still checked before a client-supplied path is forwarded.
    response = client.get('/api/observations', query_string={'directory': str(root)})
    assert response.status_code == 401
    get.assert_not_called()
    response = client.get('/api/observations', query_string={'directory': str(root)},
                          headers={'X-Webhook-Token': module.WEBHOOK_SECRET})
    assert response.status_code == 200
    assert response.json['count'] == 1
    assert response.json['observations'][0]['folder'] == '2026/November'
