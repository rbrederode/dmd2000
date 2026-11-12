"""TM observation catalogue searches an explicit directory or the live SDP store."""

import json
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

from tm.web_api import WebAPIHandler


@pytest.mark.parametrize('command', ['Trace', 'Debug', 'Resync'])
def test_embedded_api_rejects_cmd_webhooks_without_queuing(command):
    queue = Queue()
    client = WebAPIHandler(queue).app.test_client()
    response = client.post('/webhook', json={
        'event': 'alston-rt.ui.cmd', 'message': {'app': 'TM', 'cmd': command},
    })
    assert response.status_code == 400
    assert response.json['status'] == 'error'
    assert response.json['code'] == 'invalid_request'
    assert 'web_server or cmd_app' in response.json['message']
    assert queue.empty()


@pytest.mark.parametrize('category', ['dig', 'dsh', 'odt', 'oet'])
def test_embedded_api_still_queues_configuration_events(category):
    queue = Queue()
    client = WebAPIHandler(queue).app.test_client()
    config = {'_type': 'Configuration'}
    response = client.post('/webhook', json={
        'event': f'alston-rt.ui.{category}', 'message': json.dumps(config),
    })
    assert response.status_code == 200
    event = queue.get_nowait()
    assert event.category == category.upper()
    assert event.new_config == config


def model_with_store(path=None):
    return SimpleNamespace(sdp=SimpleNamespace(app=SimpleNamespace(
        arguments=None if path is None else {'scan_store_dir': path})))


class ModelReadProbe:
    def __init__(self, getter):
        self.getter = getter

    @property
    def sdp(self):
        return self.getter()


def test_model_is_not_read_at_api_initialization_or_health_check():
    provider = Mock(return_value=None)
    handler = WebAPIHandler(Queue(), telmodel=ModelReadProbe(provider))
    client = handler.app.test_client()
    assert client.get('/health').status_code == 200
    provider.assert_not_called()
    assert client.get('/api/observations').status_code == 503
    provider.assert_called_once_with()


def test_catalogue_uses_live_sdp_store_and_reads_metadata_only(tmp_path):
    model = model_with_store()
    handler = WebAPIHandler(Queue(), telmodel=model)
    assert handler.telmodel is model
    client = handler.app.test_client()
    assert client.get('/api/observations').status_code == 503
    model.sdp = model_with_store(str(tmp_path)).sdp
    metadata = {'_type': 'ObsModel', 'obs_id': 'obs:001', 'title': 'Test observation',
                'dsh_id': 'dish003', 'obs_state': {'value': 'COMPLETE'},
                'start_dt': {'value': '2026-11-05T12:00:00+00:00'}}
    (tmp_path / 'obs001-obs.json').write_text(json.dumps(metadata))
    (tmp_path / 'obs001-spr.csv').write_text('not observation metadata')
    response = client.get('/api/observations')
    assert response.status_code == 200
    assert response.json['status'] == 'success'
    assert response.json['message'] == 'Found 1 observations'
    expected = {
        'id': 'obs:001', 'filename': 'obs001-obs.json', 'folder': '.', 'title': 'Test observation',
        'dish': 'dish003', 'state': 'COMPLETE', 'started_at': '2026-11-05T12:00:00+00:00',
    }
    assert response.json['payload']['count'] == 1
    entries = response.json['payload']['observations']
    assert len(entries) == 1
    assert {key: entries[0][key] for key in expected} == expected
    assert entries[0]['completed'] is True
    assert entries[0]['scan_count'] == 0
    assert 'target_scans' not in entries[0]
    # Status messages can replace the SDP submodel on the same telescope model.
    replacement = tmp_path / 'replacement'
    replacement.mkdir()
    model.sdp = model_with_store(replacement).sdp
    assert client.get('/api/observations').json['payload'] == {'count': 0, 'observations': []}


def test_explicit_directory_overrides_sdp_store(tmp_path):
    configured = tmp_path / 'configured'
    configured.mkdir()
    (tmp_path / 'outside-obs.json').write_text('{"_type":"ObsModel"}')
    client = WebAPIHandler(Queue(), telmodel=model_with_store(configured)).app.test_client()
    response = client.get('/api/observations', query_string={'directory': str(tmp_path)})
    assert response.status_code == 200
    assert response.json['payload']['count'] == 1
    assert response.json['payload']['observations'][0]['filename'] == 'outside-obs.json'
    assert response.json['payload']['observations'][0]['folder'] == '.'


def test_explicit_directory_works_without_consulting_sdp_and_searches_recursively(tmp_path):
    nested = tmp_path / '2026' / 'November'
    nested.mkdir(parents=True)
    for folder in (tmp_path, nested):
        (folder / 'same-obs.json').write_text('{"_type":"ObsModel", "obs_id":"same"}')
        (folder / 'same-spr.csv').write_text('not metadata')
    provider = Mock(side_effect=AssertionError('Must not consult SDP for an explicit directory'))
    client = WebAPIHandler(Queue(), telmodel=ModelReadProbe(provider)).app.test_client()
    response = client.get('/api/observations', query_string={'directory': str(tmp_path)})
    assert response.status_code == 200
    entries = response.json['payload']['observations']
    assert response.json['payload']['count'] == 2
    assert [entry['folder'] for entry in entries] == ['2026/November', '.']
    assert all(entry['filename'] == 'same-obs.json' for entry in entries)
    provider.assert_not_called()


def test_empty_store_returns_success(tmp_path):
    client = WebAPIHandler(Queue(), telmodel=model_with_store(tmp_path)).app.test_client()
    assert client.get('/api/observations').json == {
        'status': 'success', 'message': 'Found 0 observations',
        'payload': {'count': 0, 'observations': []},
    }


def test_empty_explicit_directory_returns_400_without_sdp_fallback(tmp_path):
    provider = Mock(return_value=tmp_path)
    client = WebAPIHandler(Queue(), telmodel=ModelReadProbe(provider)).app.test_client()
    response = client.get('/api/observations?directory=')
    assert response.status_code == 400
    assert response.json == {'status': 'error', 'message': 'Directory must not be empty',
                             'code': 'invalid_request'}
    provider.assert_not_called()


def test_missing_explicit_directory_returns_503_without_sdp_fallback(tmp_path):
    client = WebAPIHandler(Queue(), telmodel=model_with_store(tmp_path)).app.test_client()
    response = client.get('/api/observations', query_string={'directory': str(tmp_path / 'missing')})
    assert response.status_code == 503
    assert response.json['status'] == 'error'
    assert response.json['code'] == 'directory_unavailable'
    assert isinstance(response.json['message'], str)


def test_unexpected_catalogue_failure_returns_response_event():
    provider = Mock(side_effect=RuntimeError('private implementation details'))
    client = WebAPIHandler(Queue(), telmodel=ModelReadProbe(provider)).app.test_client()
    response = client.get('/api/observations')
    assert response.status_code == 500
    assert response.json == {'status': 'error', 'message': 'Internal server error', 'code': 'internal_error'}


def test_missing_store_returns_503(tmp_path):
    client = WebAPIHandler(Queue(), telmodel=model_with_store(tmp_path / 'missing')).app.test_client()
    assert client.get('/api/observations').status_code == 503


def test_unreadable_directory_returns_503(tmp_path, monkeypatch):
    import os

    def denied(self):
        raise PermissionError

    monkeypatch.setattr(os, 'scandir', denied)
    client = WebAPIHandler(Queue(), telmodel=model_with_store(tmp_path)).app.test_client()
    assert client.get('/api/observations').status_code == 503


def test_unreadable_subtree_returns_error_instead_of_partial_catalogue(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    (tmp_path / 'valid-obs.json').write_text('{"_type":"ObsModel"}')
    scandir = os.scandir

    def denied_subtree(path):
        if Path(path) == blocked:
            raise PermissionError('Subdirectory is unreadable')
        return scandir(path)

    monkeypatch.setattr(os, 'scandir', denied_subtree)
    client = WebAPIHandler(Queue(), telmodel=model_with_store(tmp_path)).app.test_client()
    response = client.get('/api/observations')
    assert response.status_code == 503
    assert 'observations' not in response.json


def test_bad_metadata_does_not_break_catalogue_and_symlinks_are_skipped(tmp_path):
    root = tmp_path / 'store'
    root.mkdir()
    outside = tmp_path / 'outside-obs.json'
    outside.write_text('{"_type":"ObsModel"}')
    (root / 'linked-obs.json').symlink_to(outside)
    (root / 'linked-folder').symlink_to(tmp_path, target_is_directory=True)
    (root / 'nested').mkdir()
    (root / 'nested' / 'hidden-obs.json').write_text(outside.read_text())
    (root / 'broken-obs.json').write_text('{unfinished')
    (root / 'wrong-obs.json').write_text('[]')
    (root / 'valid-obs.json').write_text('{"_type":"ObsModel", "obs_id":"valid"}')
    client = WebAPIHandler(Queue(), telmodel=model_with_store(root)).app.test_client()
    response = client.get('/api/observations')
    assert response.status_code == 200
    assert response.json['payload']['count'] == 4
    entries = response.json['payload']['observations']
    assert [entry['id'] for entry in entries] == ['broken', 'hidden', 'valid', 'wrong']
    assert 'error' in entries[0] and 'error' in entries[3]
    assert entries[1]['folder'] == 'nested'
    assert 'error' not in entries[2]
