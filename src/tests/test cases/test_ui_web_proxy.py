"""The local frontend forwards dish/profile operations to WEB, keeping secrets server-side."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from models.dsh import DishList, DishModel
from ui import opt_service


@pytest.fixture
def console(tmp_path):
    app = opt_service.create_app([tmp_path], tmp_path / 'cache', web_url='http://127.0.0.1:8000', web_token='private-secret')
    app.testing = True
    yield app, app.test_client()
    app.extensions['opt_jobs'].close()


def test_application_command_catalogue_and_get_queries(console, monkeypatch):
    client = console[1]
    for application in ('tm', 'dm', 'web', 'dig'):
        result = client.get('/api/commands', query_string={'app': application})
        assert result.status_code == 200
        choices = {option['id']: option for option in result.json['payload']['commands']}
        assert {'resync', 'trace-get', 'trace-on', 'trace-off', 'debug-get', 'debug-on', 'debug-off'} <= choices.keys()
        assert ('stop' in choices) == (application == 'dm')
        if application == 'dm':
            assert choices['stop']['value_required'] is True
    assert client.get('/api/commands?app=unknown').status_code == 400
    sent = []
    def forward(method, url, **kwargs):
        sent.append(kwargs['json']['message'])
        return SimpleNamespace(status_code=200, json=lambda: {'status': 'success', 'message': 'Trace is OFF'})
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    result = client.post('/api/commands', json={'app': 'dig003', 'cmd': 'trace'})
    assert result.json['message'] == 'Trace is OFF'
    assert sent == [{'app': 'dig003', 'cmd': 'trace'}]


def test_custom_registered_command_choices_and_validation(console, monkeypatch, tmp_path):
    registry = tmp_path / 'commands.json'
    registry.write_text(json.dumps({'_type': 'CmdRegistry', 'applications': {'sdp': {'commands': {
        'set-limit': {'description': 'Set processing limit', 'value': {'required': True, 'type': 'int'}}}}}}))
    monkeypatch.setenv('WEB_CMD_CONFIG', str(registry))
    client = console[1]
    choices = client.get('/api/commands?app=sdp').json['payload']['commands']
    assert choices[-1]['cmd'] == 'set-limit' and choices[-1]['value_type'] == 'int'
    sent = []
    def forward(method, url, **kwargs):
        sent.append(kwargs['json']['message'])
        return SimpleNamespace(status_code=200, json=lambda: {'status': 'success', 'message': 'Limit updated'})
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    assert client.post('/api/commands', json={'app': 'sdp', 'cmd': 'set-limit', 'value': '4'}).status_code == 200
    for payload in [{'app': 'sdp', 'cmd': 'set-limit'}, {'app': 'sdp', 'cmd': 'set-limit', 'value': 'bad'},
                    {'app': 'tm', 'cmd': 'set-limit', 'value': '4'}]:
        assert client.post('/api/commands', json=payload).status_code == 400
    assert len(sent) == 1


def test_live_digitiser_settings_forward_as_tm_config_requests(console, monkeypatch):
    sent = []
    def forward(method, url, **kwargs):
        sent.append(kwargs['json'])
        return SimpleNamespace(status_code=200, json=lambda: {'status': 'success', 'message': 'Actions scheduled'})
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    response = console[1].post('/api/digitiser-control/dig003', json={'gain': 17, 'freq_correction': -140})
    assert response.status_code == 200
    assert sent == [{'event': 'alston-rt.ui.dig', 'message': {
        'dig_id': 'dig003', 'gain': 17, 'freq_correction': -140}}]
    for body in [{'gain': True}, {'channels': 0}, {'sample_rate': 'bad'}, {'host': 'external'}, {'scanning': 'ON'}]:
        assert console[1].post('/api/digitiser-control/dig003', json=body).status_code == 400
    assert len(sent) == 1


def test_proxy_forwards_authentication_versions_and_profile_guards(console, monkeypatch):
    _, client = console
    calls = []
    def call(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=201, headers={'ETag': '"version"', 'Location': '/api/dishes/dish004'},
            json=lambda: {'status': 'success', 'message': 'Created', 'payload': {'dish': {'dsh_id': 'dish004'}}})
    monkeypatch.setattr(opt_service.requests, 'request', call)
    response = client.post('/api/web/dishes', json={'dsh_id': 'dish004'}, headers={
        'Origin': 'http://localhost', 'If-Match': '"old"', 'X-Config-Profile': 'local',
        'Cookie': 'private-cookie', 'Authorization': 'client-auth'})
    assert response.status_code == 201
    method, url, options = calls[0]
    assert method == 'POST' and url == 'http://127.0.0.1:8000/api/dishes'
    assert options['headers'] == {'X-Webhook-Token': 'private-secret', 'If-Match': '"old"', 'X-Config-Profile': 'local'}
    assert options['allow_redirects'] is False
    assert response.headers['ETag'] == '"version"'
    assert response.headers['Location'] == '/api/web/dishes/dish004'
    assert b'private-secret' not in response.data


@pytest.mark.parametrize('method, url', [('POST', '/api/web/dishes'), ('PATCH', '/api/web/dishes/dish001'), ('PUT', '/api/web/profile')])
def test_proxy_rejects_cross_origin_writes(console, monkeypatch, method, url):
    monkeypatch.setattr(opt_service.requests, 'request', lambda *a, **k: pytest.fail('must not forward'))
    _, client = console
    response = client.open(url, method=method, json={'profile': 'local'}, headers={'Origin': 'https://untrusted.example'})
    assert response.status_code == 403 and response.json['status'] == 'error'


@pytest.mark.parametrize('url', ['/api/web/health', '/api/web/webhook', '/api/web/dishes/dish001/extra', '/api/web/weather-stations'])
def test_proxy_is_not_an_unrestricted_gateway(console, monkeypatch, url):
    monkeypatch.setattr(opt_service.requests, 'request', lambda *a, **k: pytest.fail('must not forward'))
    assert console[1].get(url).status_code == 404


def test_proxy_reports_unknown_save_outcome_on_network_failure(console, monkeypatch):
    def fail(*args, **kwargs):
        raise requests.exceptions.Timeout()
    monkeypatch.setattr(opt_service.requests, 'request', fail)
    response = console[1].post('/api/web/dishes', json={'dsh_id': 'dish004'})
    assert response.status_code == 502
    assert 'may have completed' in response.json['message']


def test_proxy_keeps_application_errors_and_conflicts(console, monkeypatch):
    body = {'status': 'error', 'code': 'version_conflict', 'message': 'Reload before saving'}
    monkeypatch.setattr(opt_service.requests, 'request', lambda *a, **k: SimpleNamespace(
        status_code=412, headers={}, json=lambda: body))
    assert console[1].patch('/api/web/dishes/dish001', json={'diameter': 2}).json == body


def test_ui_to_web_profile_dish_edit_and_creation(console, monkeypatch, tmp_path):
    # Exercise actual WEB handlers, touching only a temporary profile.
    profile = tmp_path / 'profiles' / 'test'
    profile.mkdir(parents=True)
    path = profile / 'DishList.json'
    path.write_text(json.dumps(DishList(dish_list=[DishModel(dsh_id='dish001')]).to_dict()))
    monkeypatch.setenv('WEBHOOK_SECRET', 'private-secret')
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location('web_proxy_target', Path(__file__).resolve().parents[2] / 'web/web_server.py')
    web = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web)
    web.limiter.enabled = False
    web.app.extensions['web_application'] = SimpleNamespace(profile_dir=profile)
    target = web.app.test_client()
    def forward(method, url, **kwargs):
        response = target.open('/api/' + url.split('/api/')[1], method=method,
            headers=kwargs['headers'], json=kwargs['json'] if method != 'GET' else None)
        return SimpleNamespace(status_code=response.status_code, headers=response.headers, json=lambda: response.json)
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    client = console[1]
    assert client.get('/api/web/profiles').json['payload']['profile'] == 'test'
    listed = client.get('/api/web/dishes')
    assert listed.json['payload']['count'] == 1
    headers = {'If-Match': listed.headers['ETag'], 'X-Config-Profile': 'test'}
    created = client.post('/api/web/dishes', headers=headers, json={'dsh_id': 'dish004', 'driver_type': 'DRIFT'})
    assert created.status_code == 201
    edited = client.patch('/api/web/dishes/dish004', headers={'If-Match': created.headers['ETag'], 'X-Config-Profile': 'test'}, json={'short_desc': 'From UI'})
    assert edited.status_code == 200 and edited.json['payload']['dish']['short_desc'] == 'From UI'
    before = path.read_bytes()
    wrong = client.patch('/api/web/dishes/dish004', headers={'X-Config-Profile': 'other'}, json={'diameter': 3})
    assert wrong.status_code == 409 and wrong.json['code'] == 'profile_changed'
    assert path.read_bytes() == before
    assert 'private-secret' not in client.get('/').get_data(as_text=True)


def test_console_reports_missing_credentials_without_forwarding(tmp_path, monkeypatch):
    monkeypatch.delenv('WEBHOOK_SECRET', raising=False)
    monkeypatch.setattr(opt_service.requests, 'request', lambda *a, **k: pytest.fail('must not forward'))
    app = opt_service.create_app([tmp_path], tmp_path / 'cache')
    try:
        response = app.test_client().get('/api/web/profiles')
        assert response.status_code == 503 and response.json['code'] == 'not_configured'
    finally:
        app.extensions['opt_jobs'].close()


@pytest.mark.parametrize('body', [{}, {'status': 'success', 'message': 'No payload'}, [], {'status': 'bad', 'message': 'Bad'}])
def test_console_rejects_invalid_web_responses(console, monkeypatch, body):
    monkeypatch.setattr(opt_service.requests, 'request', lambda *a, **k: SimpleNamespace(
        status_code=200, headers={}, json=lambda: body))
    response = console[1].get('/api/web/profiles')
    assert response.status_code == 502 and response.json['code'] == 'invalid_response'


def test_delete_proxy_needs_no_json_body_but_checks_origin(console, monkeypatch):
    calls = []
    def call(method, url, **kwargs):
        calls.append((method, kwargs))
        return SimpleNamespace(status_code=200, headers={'ETag': '"new"'}, json=lambda: {
            'status': 'success', 'message': 'Deleted', 'payload': {'deleted_id': 'dish001'}})
    monkeypatch.setattr(opt_service.requests, 'request', call)
    _, client = console
    assert client.delete('/api/web/dishes/dish001', headers={'Origin': 'https://untrusted.example'}).status_code == 403
    assert not calls
    response = client.delete('/api/web/dishes/dish001', headers={
        'Origin': 'http://localhost', 'If-Match': '"old"', 'X-Config-Profile': 'local'})
    assert response.status_code == 200
    assert calls[0][0] == 'DELETE' and calls[0][1]['json'] is None
    assert calls[0][1]['headers']['If-Match'] == '"old"'
    assert client.delete('/api/web/profile').status_code == 404


def test_ui_to_web_digitiser_crud_keeps_credentials_and_profile_on_server(console, monkeypatch, tmp_path):
    from models.dig import DigitiserList, DigitiserModel
    profile = tmp_path / 'profiles' / 'test'
    profile.mkdir(parents=True)
    path = profile / 'DigitiserList.json'
    path.write_text(json.dumps(DigitiserList(dig_list=[DigitiserModel(dig_id='dig001')]).to_dict()))
    monkeypatch.setenv('WEBHOOK_SECRET', 'private-secret')
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location('web_digitiser_proxy_target', Path(__file__).resolve().parents[2] / 'web/web_server.py')
    web = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web)
    web.limiter.enabled = False
    web.app.extensions['web_application'] = SimpleNamespace(profile_dir=profile)
    target = web.app.test_client()
    forwarded = []
    def forward(method, url, **kwargs):
        forwarded.append((method, kwargs['headers']))
        response = target.open('/api/' + url.split('/api/')[1], method=method,
            headers=kwargs['headers'], json=kwargs['json'] if method not in ('GET', 'DELETE') else None)
        return SimpleNamespace(status_code=response.status_code, headers=response.headers, json=lambda: response.json)
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    client = console[1]
    listed = client.get('/api/web/digitisers', headers={'X-Config-Profile': 'test'})
    assert listed.status_code == 200 and listed.json['payload']['count'] == 1
    headers = {'X-Config-Profile': 'test', 'If-Match': listed.headers['ETag']}
    created = client.post('/api/web/digitisers', headers=headers, json={
        'dig_id': 'dig004', 'local_host': '127.0.0.2', 'sdr_type': 'rtlstream',
        'sdr_config': {'ring_seconds': 10}, 'temp_type': 'bme280',
        'temp_config': {'bus_number': 1, 'address': 118}, 'temp_max': 45})
    assert created.status_code == 201
    assert created.headers['Location'] == '/api/web/digitisers/dig004'
    read = client.get(created.headers['Location'], headers={'X-Config-Profile': 'test'})
    assert read.json['payload']['digitiser']['local_host'] == '127.0.0.2'
    headers['If-Match'] = read.headers['ETag']
    edited = client.patch('/api/web/digitisers/dig004', headers=headers, json={'gain': 23})
    assert edited.status_code == 200 and edited.json['payload']['digitiser']['gain'] == 23.0
    before = path.read_bytes()
    stale = client.delete('/api/web/digitisers/dig004', headers=headers)
    assert stale.status_code == 412
    wrong = client.delete('/api/web/digitisers/dig004', headers={'X-Config-Profile': 'other'})
    assert wrong.status_code == 409 and path.read_bytes() == before
    headers['If-Match'] = edited.headers['ETag']
    deleted = client.delete('/api/web/digitisers/dig004', headers=headers)
    assert deleted.status_code == 200 and deleted.json['payload']['deleted_id'] == 'dig004'
    assert client.get('/api/web/digitisers').json['payload']['count'] == 1
    assert all(item[1]['X-Webhook-Token'] == 'private-secret' for item in forwarded)
    assert b'private-secret' not in read.data


@pytest.mark.parametrize('method, path', [('POST', '/api/web/digitisers'), ('PATCH', '/api/web/digitisers/dig001'), ('DELETE', '/api/web/digitisers/dig001')])
def test_digitiser_proxy_rejects_cross_origin_mutations(console, monkeypatch, method, path):
    monkeypatch.setattr(opt_service.requests, 'request', lambda *a, **k: pytest.fail('must not forward'))
    response = console[1].open(path, method=method, json={'gain': 20} if method != 'DELETE' else None,
                              headers={'Origin': 'https://untrusted.example'})
    assert response.status_code == 403


@pytest.mark.parametrize('field,value', [('mode', 'STOW'), ('capability', 'OPERATE_FULL')])
def test_live_dish_settings_forward_only_the_selected_property(console, monkeypatch, field, value):
    calls = []
    body = {'status': 'success', 'message': 'DSH configuration processed; actions scheduled',
            'payload': {'phase': 'actions_scheduled'}}
    def forward(method, url, **options):
        calls.append((method, url, options))
        return SimpleNamespace(status_code=200, json=lambda: body)
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    response = console[1].post('/api/dish-control/dish002', json={field: value})
    assert response.json == body
    assert len(calls) == 1
    method, url, options = calls[0]
    assert method == 'POST' and url.endswith('/webhook')
    assert options['json'] == {'event': 'alston-rt.ui.dsh', 'message': {'dsh_id': 'dish002', field: value}}
    assert options['headers'] == {'X-Webhook-Token': 'private-secret'}
    assert options['allow_redirects'] is False


@pytest.mark.parametrize('data', [{}, {'mode': 'invalid'}, {'mode': 5}, {'capability': None},
                                {'mode': 'STOW', 'target': {}}, {'mode': 'STOW', 'capability': 'STANDBY'}])
def test_live_dish_settings_reject_invalid_payloads(console, monkeypatch, data):
    monkeypatch.setattr(opt_service.requests, 'request', lambda *a, **k: pytest.fail('must not forward'))
    assert console[1].post('/api/dish-control/dish002', json=data).status_code == 400


def test_direct_dish_stop_is_restricted_to_dm_and_returns_its_response(console, monkeypatch):
    calls = []
    def forward(method, url, **options):
        calls.append(options['json'])
        return SimpleNamespace(status_code=200, json=lambda: {'status': 'error', 'message': 'Unknown dish'})
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    response = console[1].post('/api/commands', json={'app': 'dm', 'cmd': 'stop', 'value': 'dish002'})
    assert response.json == {'status': 'error', 'message': 'Unknown dish'}
    assert calls == [{'event': 'alston-rt.ui.cmd', 'message': {'app': 'dm', 'cmd': 'stop', 'value': 'dish002'}}]
    for payload in [{'app': 'tm', 'cmd': 'stop', 'value': 'dish002'}, {'app': 'dm', 'cmd': 'stop'},
                    {'app': 'dm', 'cmd': 'stop', 'value': '../dish002'}]:
        assert console[1].post('/api/commands', json=payload).status_code == 400
    assert len(calls) == 1


def test_live_dish_control_reports_unknown_outcome_without_retry(console, monkeypatch):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(args)
        raise requests.exceptions.Timeout()
    monkeypatch.setattr(opt_service.requests, 'request', timeout)
    response = console[1].post('/api/dish-control/dish002', json={'mode': 'STOW'})
    assert response.status_code == 502 and response.json['code'] == 'outcome_unknown'
    assert len(calls) == 1
