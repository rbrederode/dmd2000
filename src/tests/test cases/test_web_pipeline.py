"""Editing ordered pipeline maps uses selected profile files, without SDP."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.pipeline import PipelineConfig, StepConfig, StepType
from web.pipeline_store import PipelineProfileStore


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv('WEBHOOK_SECRET', 'pipeline-secret')
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / 'profiles' / 'first'
    profile.mkdir(parents=True)
    data = PipelineConfig(steps_map={
        'default': [StepConfig(step=StepType.DC_SPIKE, params={'context': 'spr'}),
                    StepConfig(step=StepType.QA, params={'context': 'mpr', 'window_frac': 0.2})],
        'dig001': [StepConfig(step='custom_step', params={'context': 'cal'})]}).to_dict()
    data['custom_metadata'] = 'preserve'
    data['steps_map']['default'][1]['legacy_field'] = 'preserve'
    path = profile / 'PipelineConfig.json'
    path.write_text(json.dumps(data))
    spec = importlib.util.spec_from_file_location('pipeline_web_under_test',
        Path(__file__).resolve().parents[2] / 'web/web_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    module.limiter.enabled = False
    module.app.extensions['web_application'] = SimpleNamespace(profile_dir=profile)
    return module.app.test_client(), path, {'X-Webhook-Token': 'pipeline-secret'}


def test_list_maps_and_ordered_steps(api):
    client, _, headers = api
    response = client.get('/api/pipeline', headers=headers)
    assert response.status_code == 200
    payload = response.json['payload']
    assert payload['profile'] == 'first' and payload['count'] == 2
    assert 'BANDPASS_FILTER' in payload['step_types']
    assert [step['index'] for step in payload['steps_map']['default']] == [0, 1]
    result = client.get('/api/pipeline/steps-maps/default/steps', headers=headers)
    assert result.json['payload']['steps'][1]['step'] == 'QA'
    assert client.get('/api/pipeline/steps-maps/dig001', headers=headers).json['payload']['steps'][0]['step'] == 'custom_step'
    # Explicit map editing never silently falls back to the default map.
    assert client.get('/api/pipeline/steps-maps/missing', headers=headers).status_code == 404


def test_create_empty_and_populated_maps(api):
    client, path, headers = api
    response = client.post('/api/pipeline/steps-maps', headers=headers, json={'name': 'dig002'})
    assert response.status_code == 201 and response.json['payload']['steps'] == []
    assert client.get(response.headers['Location'], headers=headers).status_code == 200
    response = client.post('/api/pipeline/steps-maps', headers=headers, json={
        'name': 'dig003', 'steps': [{'step': 'load', 'params': {'context': 'cal'}}]})
    assert response.status_code == 201
    assert response.json['payload']['steps'][0]['step'] == 'LOAD'
    assert client.post('/api/pipeline/steps-maps', headers=headers, json={'name': 'dig003'}).status_code == 409
    model = PipelineConfig.load_from_disk(input_dir=str(path.parent), filename=path.name)
    assert model.steps_map['dig003'][0].step == StepType.LOAD


def test_add_insert_edit_and_preserve_other_configuration(api):
    client, path, headers = api
    before = json.loads(path.read_text())
    base = '/api/pipeline/steps-maps/default/steps'
    added = client.post(base, headers=headers, json={'step': 'RFI_FLAG', 'params': {'context': 'cal', 'threshold': 5}})
    assert added.status_code == 201 and added.json['payload']['step']['index'] == 2
    assert client.get(added.headers['Location'], headers=headers).status_code == 200
    inserted = client.post(base, headers=headers, json={'index': 1, 'step': 'LOAD', 'params': {'context': 'cal'}})
    assert [step['step'] for step in inserted.json['payload']['steps']] == ['DC_SPIKE', 'LOAD', 'QA', 'RFI_FLAG']
    edited = client.patch(base + '/2', headers=headers, json={'params': {'window_frac': 0.4}})
    assert edited.status_code == 200
    assert edited.json['payload']['step']['params'] == {'context': 'mpr', 'window_frac': 0.4}
    after = json.loads(path.read_text())
    assert after['custom_metadata'] == before['custom_metadata']
    assert after['steps_map']['dig001'] == before['steps_map']['dig001']
    assert after['steps_map']['default'][2]['legacy_field'] == 'preserve'


def test_stale_etag_prevents_editing_shifted_index(api):
    client, path, headers = api
    base = '/api/pipeline/steps-maps/default/steps'
    revision = client.get(base, headers=headers).headers['ETag']
    assert client.post(base, headers=headers, json={'index': 0, 'step': 'NOP'}).status_code == 201
    before = path.read_bytes()
    response = client.patch(base + '/1', headers=dict(headers, **{'If-Match': revision}), json={'step': 'LOAD'})
    assert response.status_code == 412 and response.json['code'] == 'version_conflict'
    assert path.read_bytes() == before


@pytest.mark.parametrize('method, url, body', [
    ('GET', '/api/pipeline', None),
    ('POST', '/api/pipeline/steps-maps', {'name': 'new'}),
    ('POST', '/api/pipeline/steps-maps/default/steps', {'step': 'NOP'}),
    ('PATCH', '/api/pipeline/steps-maps/default/steps/0', {'params': {'context': 'cal'}}),
])
def test_authentication_precedes_file_access(api, method, url, body):
    client, path, _ = api
    before = path.read_bytes()
    assert client.open(url, method=method, json=body).status_code == 401
    assert path.read_bytes() == before


@pytest.mark.parametrize('url, body', [
    ('/api/pipeline/steps-maps', {'name': 'bad/name'}),
    ('/api/pipeline/steps-maps', {'name': 'new', 'steps': {}}),
    ('/api/pipeline/steps-maps', {'name': 'new', 'steps': [{'params': {}}]}),
    ('/api/pipeline/steps-maps/default/steps', {'step': ''}),
    ('/api/pipeline/steps-maps/default/steps', {'step': 3}),
    ('/api/pipeline/steps-maps/default/steps', {'step': 'QA', 'params': []}),
    ('/api/pipeline/steps-maps/default/steps', {'step': 'QA', 'index': True}),
    ('/api/pipeline/steps-maps/default/steps', {'step': 'QA', 'index': -1}),
    ('/api/pipeline/steps-maps/default/steps', {'step': 'QA', 'index': 99}),
    ('/api/pipeline/steps-maps/default/steps', {'step': 'QA', 'typo': True}),
    ('/api/pipeline/steps-maps/default/steps', {'step': 'QA', 'params': {'threshold': float('nan')}}),
])
def test_invalid_changes_leave_pipeline_unchanged(api, url, body):
    client, path, headers = api
    before = path.read_bytes()
    assert client.post(url, headers=headers, json=body).status_code == 400
    assert path.read_bytes() == before


def test_missing_step_and_new_file(api):
    client, path, headers = api
    assert client.patch('/api/pipeline/steps-maps/default/steps/99', headers=headers, json={'step': 'NOP'}).status_code == 404
    path.unlink()
    assert client.get('/api/pipeline', headers=headers).status_code == 404
    response = client.post('/api/pipeline/steps-maps', headers=headers, json={'name': 'default', 'steps': []})
    assert response.status_code == 201 and path.is_file()
    assert json.loads(path.read_text())['steps_map'] == {'default': []}


def test_profile_switch_isolates_pipeline_edits(api):
    client, path, headers = api
    second = path.parent.parent / 'second'
    second.mkdir()
    (second / path.name).write_text(path.read_text())
    assert client.put('/api/profile', headers=headers, json={'profile': 'second'}).status_code == 200
    before = path.read_bytes()
    response = client.post('/api/pipeline/steps-maps', headers=headers, json={'name': 'new'})
    assert response.json['payload']['profile'] == 'second'
    assert path.read_bytes() == before
    assert 'new' in json.loads((second / path.name).read_text())['steps_map']


def test_atomic_write_failure_preserves_pipeline(api, monkeypatch):
    import web.profile_store as module
    client, path, headers = api
    before = path.read_bytes()
    def fail(*args):
        raise OSError('replace failed')
    monkeypatch.setattr(module.os, 'replace', fail)
    assert client.post('/api/pipeline/steps-maps', headers=headers, json={'name': 'new'}).status_code == 503
    assert path.read_bytes() == before
    assert not list(path.parent.glob('.PipelineConfig-*'))


def test_concurrent_step_additions_preserve_ordered_entries(api):
    _, path, _ = api
    def add(index):
        return PipelineProfileStore(path.parent).save({'step': 'NOP', 'params': {'tag': index}}, name='default')
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(add, range(6)))
    result, _ = PipelineProfileStore(path.parent).read('default')
    assert result['count'] == 8
    assert {step['params']['tag'] for step in result['steps'][2:]} == set(range(6))


def test_delete_pipeline_step_and_map_preserves_unrelated_content(api):
    client, path, headers = api
    base = '/api/pipeline/steps-maps/default'
    before = path.read_bytes()
    assert client.delete(base + '/steps/0').status_code == 401
    assert client.delete(base + '/steps/0', headers=dict(headers, **{'If-Match': '"stale"'})).status_code == 412
    assert client.delete(base + '/steps/0', headers=dict(headers, **{'X-Config-Profile': 'wrong'})).status_code == 409
    assert path.read_bytes() == before
    revision = client.get(base, headers=headers).headers['ETag']
    response = client.delete(base + '/steps/0', headers=dict(headers, **{'If-Match': revision}))
    assert response.status_code == 200 and response.json['payload']['deleted_index'] == 0
    assert response.json['payload']['steps'][0]['step'] == 'QA'
    assert response.json['payload']['steps'][0]['index'] == 0
    assert client.delete(base + '/steps/99', headers=headers).status_code == 404
    assert client.delete(base, headers=headers).status_code == 200
    after = json.loads(path.read_text())
    assert list(after['steps_map']) == ['dig001']
    assert after['custom_metadata'] == 'preserve'
    assert client.delete(base, headers=headers).status_code == 404
