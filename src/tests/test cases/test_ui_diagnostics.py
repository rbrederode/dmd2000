"""Private diagnostic files use selected storage and never accept arbitrary paths."""
import json
from pathlib import Path
import re

import pytest
from ui import diagnostics
from ui.opt_service import create_app
from web.security import init_security, save_user, db


@pytest.fixture
def console(tmp_path):
    root = tmp_path / 'logs'
    (root / 'app').mkdir(parents=True)
    (root / 'trace').mkdir()
    (root / 'app/tm.log').write_text('first line\nlast line <script>\n')
    (root / 'app/dig.log').write_text('shared digitiser log\n')
    (root / 'trace/tm.html').write_text('<!doctype html><html><head></head><body><details><summary>Message</summary>Full trace</details></body></html>')
    (root / 'trace/dig001.html').write_text('<html><head></head><body>dig001 only</body></html>')
    (root / 'trace/dig003.html').write_text('<html><head></head><body>dig003 only</body></html>')
    selection = {'profile': 'local', 'log_dir': str(root)}
    app = create_app([tmp_path], tmp_path / 'cache', profile_provider=lambda: dict(selection))
    app.testing = True
    security = init_security(app, tmp_path / 'auth')
    with app.app_context():
        save_user(security.datastore, {'username': 'viewer', 'role': 'viewer', 'password': 'viewer-password-123'})
    client = app.test_client()
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', client.get('/login').text).group(1)
    assert client.post('/login', data={'username': 'viewer', 'password': 'viewer-password-123', 'csrf_token': token}).status_code == 302
    yield app, client, root, selection
    app.extensions['opt_jobs'].close()
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def test_diagnostics_require_session_and_viewers_can_read_and_download(console):
    app, client, root, _ = console
    assert app.test_client().get('/api/diagnostics/tm/log').status_code == 401
    response = client.get('/api/diagnostics/tm/log?profile=local')
    assert response.status_code == 200
    data = response.json['payload']
    assert data['content'] == (root / 'app/tm.log').read_text()
    assert data['file'] == 'app/tm.log' and data['profile'] == 'local'
    assert data['format'] == 'text' and not data['truncated']
    assert response.headers['Cache-Control'] == 'no-store'
    download = client.get('/api/diagnostics/tm/log?download=1')
    assert download.data == (root / 'app/tm.log').read_bytes()
    assert 'attachment' in download.headers['Content-Disposition']
    assert download.mimetype == 'application/octet-stream'
    download.close()


def test_trace_ids_and_shared_digitiser_logs(console):
    _, client, _, _ = console
    first = client.get('/api/diagnostics/dig/trace?entity=dig001').json['payload']
    second = client.get('/api/diagnostics/dig/trace?entity=dig003').json['payload']
    assert first['file'] == 'trace/dig001.html' and 'dig001 only' in first['content']
    assert second['file'] == 'trace/dig003.html' and 'dig003 only' in second['content']
    assert 'dig003' not in first['content']
    shared = client.get('/api/diagnostics/dig/log?entity=dig001').json['payload']
    assert shared['file'] == 'app/dig.log' and shared['shared_log']
    assert first['format'] == 'html'
    assert client.get('/api/diagnostics/dig/trace').status_code == 404
    assert client.get('/api/diagnostics/dig/trace?entity=../tm').status_code == 400
    assert client.get('/api/diagnostics/dig/trace?entity=dig001.html').status_code == 400


@pytest.mark.parametrize('application,entity', [
    ('tm', 'tm001'), ('dm', 'dm001'), ('sdp', 'sdp001'),
    ('ws', 'ws002'), ('web', 'web001'),
])
def test_all_application_traces_use_entity_ids(console, application, entity):
    _, client, root, _ = console
    (root / 'trace' / (entity + '.html')).write_text('instance ' + entity)
    response = client.get(f'/api/diagnostics/{application}/trace?entity={entity}')
    assert response.status_code == 200
    assert response.json['payload']['file'] == f'trace/{entity}.html'
    assert response.json['payload']['content'] == 'instance ' + entity
    assert client.get(f'/api/diagnostics/{application}/trace?entity=../tm').status_code == 400


@pytest.mark.parametrize('query', ['', '?entity=', '?entity=%3Cundefined%3E'])
def test_trace_without_defined_entity_uses_application_name(console, query):
    _, client, _, _ = console
    response = client.get('/api/diagnostics/tm/trace' + query)
    assert response.status_code == 200
    assert response.json['payload']['file'] == 'trace/tm.html'


def test_profile_changes_are_guarded_and_follow_selected_root(console, tmp_path):
    _, client, _, selection = console
    other = tmp_path / 'other'
    (other / 'app').mkdir(parents=True)
    (other / 'app/tm.log').write_text('other profile log')
    selection.update(profile='other', log_dir=str(other))
    assert client.get('/api/diagnostics/tm/log?profile=local').status_code == 409
    data = client.get('/api/diagnostics/tm/log?profile=other').json['payload']
    assert data['content'] == 'other profile log'


@pytest.mark.parametrize('relative', ['app/tm.log', 'trace/tm.html', 'app'])
def test_symlinks_cannot_expose_files_outside_root(console, tmp_path, relative):
    _, client, root, _ = console
    target = tmp_path / 'private'
    target.mkdir()
    (target / 'tm.log').write_text('secret')
    (target / 'tm.html').write_text('secret')
    link = root / relative
    if link.is_dir():
        for path in link.iterdir():
            path.unlink()
        link.rmdir()
        link.symlink_to(target, target_is_directory=True)
    else:
        link.unlink()
        link.symlink_to(target / link.name)
    kind = 'trace' if relative.startswith('trace') else 'log'
    response = client.get('/api/diagnostics/tm/' + kind)
    assert response.status_code == 403
    assert 'secret' not in response.text


def test_tail_large_logs_and_refuse_oversize_trace_preview(console, monkeypatch):
    _, client, root, _ = console
    monkeypatch.setattr(diagnostics, 'LOG_TAIL_BYTES', 12)
    (root / 'app/tm.log').write_text('old entry\nnew entry\n')
    data = client.get('/api/diagnostics/tm/log').json['payload']
    assert data['content'] == 'new entry\n' and data['truncated']
    monkeypatch.setattr(diagnostics, 'TRACE_MAX_BYTES', 10)
    response = client.get('/api/diagnostics/tm/trace')
    assert response.status_code == 413 and response.json['code'] == 'file_too_large'
    download = client.get('/api/diagnostics/tm/trace?download=1')
    assert download.data == (root / 'trace/tm.html').read_bytes()
    download.close()


def test_missing_files_and_unsupported_resources(console):
    _, client, _, _ = console
    missing = client.get('/api/diagnostics/ws/trace')
    assert missing.status_code == 404 and missing.json['code'] == 'not_found'
    assert client.get('/api/diagnostics/credentials/log').status_code == 404
    assert client.get('/api/diagnostics/tm/users.sqlite').status_code == 404
    assert client.get('/api/diagnostics/tm/log', headers={'Host': 'public.example'}).status_code == 403
