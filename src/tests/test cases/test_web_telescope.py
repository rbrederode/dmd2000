"""The observatory editor persists only validated, profile-independent attributes."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def telescope_api(monkeypatch, tmp_path):
    from web import telescope_store
    monkeypatch.setattr(telescope_store, 'CONFIG_DIR', tmp_path)
    monkeypatch.setenv('WEBHOOK_SECRET', 'telescope-test-secret')
    path = tmp_path / 'telescope.json'
    path.write_text(json.dumps(dict(_type='TelescopeConfig', name='Reference site', latitude=53.234123, longitude=-2.305485, height=77.)))
    spec = importlib.util.spec_from_file_location('telescope_web_server_under_test',
        Path(__file__).resolve().parents[2] / 'web/web_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    module.limiter.enabled = False
    module.app.extensions['web_application'] = SimpleNamespace(profile_dir=tmp_path / 'rotation')
    return module, module.app.test_client(), path, {'X-Webhook-Token':'telescope-test-secret'}


def test_read_and_save_shared_attributes_without_live_tm_or_profile_writes(telescope_api):
    module, client, path, headers = telescope_api
    response = client.get('/api/telescope/config', headers=headers)
    assert response.status_code == 200
    assert response.json['payload']['scope'] == 'observatory'
    data = response.json['payload']['config']
    data.update(name='Renamed Observatory', longitude=0, height=None)
    revision = response.headers['ETag']
    module.app.extensions['web_application'].profile_dir = path.parent / 'other-profile'
    response = client.put('/api/telescope/config', json=data, headers={**headers, 'If-Match':revision, 'X-Config-Profile':'other-profile'})
    assert response.status_code == 200
    assert response.json['payload']['phase'] == 'stored'
    assert response.headers['ETag'] != revision
    assert json.loads(path.read_text()) == {'_type':'TelescopeConfig', **data}
    assert set(data) == {'name', 'latitude', 'longitude', 'height'}
    assert not (path.parent / 'other-profile').exists()
    assert client.get('/api/telescope/config', headers=headers).json['payload']['config'] == data


def test_save_requires_revision_and_rejects_another_session_change(telescope_api):
    _, client, path, headers = telescope_api
    response = client.get('/api/telescope/config', headers=headers)
    data, etag = response.json['payload']['config'], response.headers['ETag']
    before = path.read_bytes()
    assert client.put('/api/telescope/config', json=data, headers=headers).status_code == 428
    assert path.read_bytes() == before
    path.write_text(json.dumps({'_type':'TelescopeConfig', **data, 'name':'Other session'}))
    before = path.read_bytes()
    result = client.put('/api/telescope/config', json=data, headers={**headers, 'If-Match':etag})
    assert result.status_code == 412 and result.json['code'] == 'version_conflict'
    assert path.read_bytes() == before


@pytest.mark.parametrize('changes', [{'name':''}, {'latitude':91}, {'longitude':-181},
    {'latitude':True}, {'height':float('inf')}, {'longitude':'0'}, {'sdp':{}}, {'profile':'rotation'}])
def test_invalid_attributes_leave_file_unchanged(telescope_api, changes):
    _, client, path, headers = telescope_api
    result = client.get('/api/telescope/config', headers=headers)
    data = result.json['payload']['config'] | changes
    before = path.read_bytes()
    result = client.put('/api/telescope/config', json=data, headers={**headers, 'If-Match':result.headers['ETag']})
    assert result.status_code == 400
    assert path.read_bytes() == before


def test_authentication_and_atomic_write_failure(telescope_api, monkeypatch):
    _, client, path, headers = telescope_api
    assert client.get('/api/telescope/config').status_code == 401
    assert client.put('/api/telescope/config', json={}).status_code == 401
    result = client.get('/api/telescope/config', headers=headers)
    data = result.json['payload']['config'] | {'name':'New name'}
    before = path.read_bytes()
    def fail(*args):
        raise PermissionError('test write failure')
    monkeypatch.setattr('web.profile_store.os.replace', fail)
    response = client.put('/api/telescope/config', json=data, headers={**headers, 'If-Match':result.headers['ETag']})
    assert response.status_code == 503 and path.read_bytes() == before
    assert not list(path.parent.glob('.telescope-*'))
