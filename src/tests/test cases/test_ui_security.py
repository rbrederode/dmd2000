"""Exercise real username/password sessions, roles and CSRF on the private UI."""
import re
from types import SimpleNamespace

import pytest
from ui.opt_service import create_app
from web.security import init_security, save_user, User, db

PASSWORD = 'test-password-12345'


@pytest.fixture
def secured(tmp_path):
    app = create_app([tmp_path], tmp_path / 'cache', web_token='private')
    app.testing = True
    security = init_security(app, tmp_path / 'auth')
    with app.app_context():
        for role in ('admin', 'operator', 'viewer'):
            save_user(security.datastore, {'username': role, 'password': PASSWORD, 'role': role})
    yield app, security
    app.extensions['opt_jobs'].close()
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def form_token(response):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', response.text).group(1)


def login(client, username='admin', password=PASSWORD):
    token = form_token(client.get('/login'))
    response = client.post('/login', data={'username': username, 'password': password, 'csrf_token': token})
    assert response.status_code == 302, response.text
    identity = client.get('/api/auth/me')
    assert identity.status_code == 200
    return identity.json, {'X-CSRFToken': identity.json['csrf_token']}


def test_private_pages_and_apis_require_login_and_native_form_csrf(secured):
    app, _ = secured
    client = app.test_client()
    assert client.get('/').status_code == 302
    assert client.get('/auth.js').status_code == 302
    assert client.get('/styles.css').status_code == 200
    assert client.get('/api/auth/me').status_code == 401
    assert client.get('/api/opt/config').status_code == 401
    assert client.post('/login', data={'username': 'admin', 'password': PASSWORD}).status_code == 400
    token = form_token(client.get('/login'))
    wrong = client.post('/login', data={'username': 'admin', 'password': 'wrong', 'csrf_token': token})
    assert wrong.status_code == 200 and b'ui-auth-error' in wrong.data
    assert client.get('/api/auth/me').status_code == 401
    identity, _ = login(client)
    assert identity['username'] == 'admin' and identity['can_manage_users']
    from util.version import get_version_info
    assert identity['version'] == get_version_info()
    assert 'password' not in identity and 'fs_uniquifier' not in identity
    assert client.get('/').status_code == 200
    assert client.get('/register').status_code == 404
    assert client.get('/logout').status_code in (404, 405)
    assert client.get('/api/auth/me').status_code == 200
    assert client.post('/logout', json={}).status_code == 400
    assert client.post('/login', data={}, headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.get('/login', headers={'Host': 'evil.test'}).status_code == 403


def test_viewer_reads_operator_writes_and_admin_manages_users(secured, monkeypatch):
    from ui import opt_service
    app, _ = secured
    calls = []
    def forward(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {'status': 'success', 'message': 'OK', 'payload': {}})
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    viewer = app.test_client()
    identity, headers = login(viewer, 'viewer')
    assert not identity['can_edit'] and not identity['can_manage_users']
    assert viewer.get('/api/web/dishes').status_code == 200
    assert viewer.patch('/api/web/dishes/dish001', json={'diameter': 2}, headers=headers).status_code == 403
    assert viewer.post('/api/opt/process', json={}, headers=headers).status_code == 403
    assert viewer.get('/api/admin/users').status_code == 403
    operator = app.test_client()
    identity, headers = login(operator, 'operator')
    assert identity['can_edit'] and not identity['can_manage_users']
    assert operator.patch('/api/web/dishes/dish001', json={'diameter': 2}).status_code == 400
    assert operator.patch('/api/web/dishes/dish001', json={'diameter': 2}, headers=headers).status_code == 200
    assert operator.get('/api/admin/users').status_code == 403
    assert len(calls) == 2


def test_admin_accounts_last_admin_guard_and_session_revocation(secured):
    app, security = secured
    client = app.test_client()
    identity, headers = login(client)
    users = client.get('/api/admin/users').json['users']
    assert len(users) == 3 and all(set(u) == {'id', 'username', 'roles', 'active'} for u in users)
    admin_id = identity['id']
    assert client.patch(f'/api/admin/users/{admin_id}', json={'active': False}, headers=headers).status_code == 400
    assert client.patch(f'/api/admin/users/{admin_id}', json={'role': 'viewer'}, headers=headers).status_code == 400
    created = client.post('/api/admin/users', json={'username': 'second', 'password': PASSWORD, 'role': 'admin'}, headers=headers)
    assert created.status_code == 201
    assert client.post('/api/admin/users', json={'username': 'SECOND', 'password': PASSWORD}, headers=headers).status_code == 400
    assert client.post('/api/admin/users', json={'username': 'weak', 'password': 'short'}, headers=headers).status_code == 400
    second = app.test_client()
    login(second, 'second')
    user_id = created.json['user']['id']
    assert client.patch(f'/api/admin/users/{user_id}', json={'password': 'another-password-123'}, headers=headers).status_code == 200
    assert second.get('/api/auth/me').status_code == 401
    login(second, 'second', 'another-password-123')
    assert client.patch(f'/api/admin/users/{user_id}', json={'active': False}, headers=headers).status_code == 200
    assert second.get('/api/auth/me').status_code == 401
    with app.app_context():
        assert security.datastore.find_user(username='second').email is None
        assert security.datastore.find_user(username='second').password.startswith('$argon2')


def test_password_change_logout_and_persistence(secured, tmp_path):
    app, _ = secured
    client = app.test_client()
    _, headers = login(client, 'viewer')
    page = client.get('/change')
    assert page.status_code == 200
    changed = client.post('/change', data={'csrf_token': form_token(page), 'password': PASSWORD,
        'new_password': 'changed-password-6789', 'new_password_confirm': 'changed-password-6789'})
    assert changed.status_code == 302, changed.text
    # Password changes retain or renew this session; get the current CSRF token.
    _, headers = login(app.test_client(), 'viewer', 'changed-password-6789')
    client2 = app.test_client()
    _, headers = login(client2, 'viewer', 'changed-password-6789')
    assert client2.post('/logout', json={}, headers=headers).status_code == 200
    assert client2.get('/api/auth/me').status_code == 401
    second_app = create_app([tmp_path], tmp_path / 'cache2')
    try:
        init_security(second_app, tmp_path / 'auth')
        login(second_app.test_client(), 'viewer', 'changed-password-6789')
        assert second_app.secret_key == app.secret_key
    finally:
        second_app.extensions['opt_jobs'].close()
        with second_app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_login_attempts_are_throttled(secured):
    client = secured[0].test_client()
    page = client.get('/login')
    token = form_token(page)
    for _ in range(9):
        result = client.post('/login', data={'username': 'missing', 'password': PASSWORD, 'csrf_token': token})
        assert result.status_code == 200
    assert client.post('/login', data={'username': 'missing', 'password': PASSWORD, 'csrf_token': token}).status_code == 429


@pytest.mark.parametrize('username', ['test_user', 'test.user', 'test-user', 'Test_User'])
def test_created_user_can_sign_in_after_admin_logout(secured, username):
    client = secured[0].test_client()
    _, headers = login(client)
    created = client.post('/api/admin/users', json={
        'username': username, 'password': PASSWORD, 'role': 'viewer'}, headers=headers)
    assert created.status_code == 201
    assert created.json['user']['active'] is True
    assert client.post('/logout', json={}, headers=headers).status_code == 200
    identity, _ = login(client, username.upper())
    assert identity['username'] == username.lower()
    assert not identity['can_edit']


def test_operator_commands_use_webhook_and_require_roles_csrf(secured, monkeypatch):
    from ui import opt_service
    app, _ = secured
    calls = []
    def forward(method, url, **options):
        calls.append((method, url, options))
        return SimpleNamespace(status_code=200, json=lambda: {'status': 'success', 'message': 'Tracing set to ON'})
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    viewer = app.test_client()
    _, viewer_headers = login(viewer, 'viewer')
    payload = {'app': 'web', 'cmd': 'trace', 'value': 'ON'}
    assert viewer.post('/api/commands', json=payload, headers=viewer_headers).status_code == 403
    operator = app.test_client()
    _, headers = login(operator, 'operator')
    assert operator.post('/api/commands', json=payload).status_code == 400
    assert not calls
    response = operator.post('/api/commands', json=payload, headers=headers)
    assert response.status_code == 200 and response.json['message'] == 'Tracing set to ON'
    assert len(calls) == 1
    method, url, options = calls[0]
    assert method == 'POST' and url.endswith('/webhook')
    assert options['json'] == {'event': 'alston-rt.ui.cmd', 'message': payload}
    assert options['headers'] == {'X-Webhook-Token': 'private'}
    assert options['allow_redirects'] is False
    for invalid in [{'app': 'tm', 'cmd': 'stop'}, {'app': 'tm', 'cmd': 'trace', 'value': 'bad'},
                    {'app': 'tm', 'cmd': 'resync', 'host': 'external'}, {'app': '../tm', 'cmd': 'resync'}]:
        assert operator.post('/api/commands', json=invalid, headers=headers).status_code == 400
    assert len(calls) == 1
    def timeout(*args, **kwargs):
        raise opt_service.requests.exceptions.Timeout()
    monkeypatch.setattr(opt_service.requests, 'request', timeout)
    response = operator.post('/api/commands', json=payload, headers=headers)
    assert response.status_code == 502 and response.json['code'] == 'outcome_unknown'


def test_dish_controls_require_operator_and_csrf(secured, monkeypatch):
    from ui import opt_service
    app, _ = secured
    calls = []
    def forward(*args, **kwargs):
        calls.append(kwargs['json'])
        return SimpleNamespace(status_code=200, json=lambda: {'status': 'success', 'message': 'Scheduled'})
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    viewer = app.test_client()
    _, viewer_headers = login(viewer, 'viewer')
    for path, payload in [('/api/dish-control/dish001', {'mode': 'STOW'}),
                          ('/api/commands', {'app': 'dm', 'cmd': 'stop', 'value': 'dish001'})]:
        assert viewer.post(path, json=payload, headers=viewer_headers).status_code == 403
    operator = app.test_client()
    _, headers = login(operator, 'operator')
    assert operator.post('/api/dish-control/dish001', json={'mode': 'STOW'}).status_code == 400
    assert not calls
    assert operator.post('/api/dish-control/dish001', json={'mode': 'STOW'}, headers=headers).status_code == 200
    assert len(calls) == 1

    assert operator.post('/api/commands', json={'app': 'dm', 'cmd': 'stop', 'value': 'dish001'}, headers=headers).status_code == 200
    assert len(calls) == 2


def test_live_digitiser_controls_require_operator_csrf_and_same_origin(secured, monkeypatch):
    from ui import opt_service
    app, _ = secured
    calls = []
    def forward(*args, **kwargs):
        calls.append(kwargs['json'])
        return SimpleNamespace(status_code=200, json=lambda: {'status': 'success', 'message': 'Scheduled'})
    monkeypatch.setattr(opt_service.requests, 'request', forward)
    viewer = app.test_client()
    _, viewer_headers = login(viewer, 'viewer')
    route, body = '/api/digitiser-control/dig003', {'gain': 17}
    assert viewer.post(route, json=body, headers=viewer_headers).status_code == 403
    operator = app.test_client()
    _, headers = login(operator, 'operator')
    assert operator.post(route, json=body).status_code == 400
    assert operator.post(route, json=body, headers={**headers, 'Origin': 'https://elsewhere.example'}).status_code == 403
    assert not calls
    assert operator.post(route, json=body, headers=headers).status_code == 200
    assert calls[0]['event'] == 'alston-rt.ui.dig'
