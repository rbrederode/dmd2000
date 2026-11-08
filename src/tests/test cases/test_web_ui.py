"""The production WEB process owns distinct public API and private UI listeners."""
import os
import json
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

import pytest
import requests
from flask import Flask
from web.security import init_security, save_user, db


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize('stop_signal', [signal.SIGINT, signal.SIGTERM])
def test_combined_web_private_ui_and_public_api_shutdown(tmp_path, stop_signal):
    api_port, ui_port, cmd_port, tm_port = [free_port() for _ in range(4)]
    state = tmp_path / 'auth'
    logs = tmp_path / 'diagnostic-logs'
    (logs / 'app').mkdir(parents=True)
    (logs / 'trace').mkdir()
    (logs / 'app/tm.log').write_text('live test application log\n')
    (logs / 'trace/tm.html').write_text('<html><head></head><body><details>live test trace</details></body></html>')
    (tmp_path / 'LaunchConfig.json').write_text(json.dumps({
        '_type': 'LaunchConfigModel', 'profile': 'test', 'log_dir': str(logs), 'launches': []}))
    auth_app = Flask('bootstrap')
    security = init_security(auth_app, state)
    with auth_app.app_context():
        save_user(security.datastore, {'username': 'admin', 'password': 'test-password-123', 'role': 'admin'})
        db.session.remove()
        db.engine.dispose()
    src = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONPATH': str(src), 'WEBHOOK_SECRET': 'test-web-secret'}
    env['WEB_COMMAND_ENDPOINTS'] = json.dumps({'web': {'host': '127.0.0.1', 'port': cmd_port}})
    log = tmp_path / 'combined.log'
    api = f'http://127.0.0.1:{api_port}'
    ui = f'http://127.0.0.1:{ui_port}'
    with log.open('w') as output:
        process = subprocess.Popen([sys.executable, '-m', 'web.web_server', '--profile_dir', str(tmp_path),
            '--http_port', str(api_port), '--ui_port', str(ui_port), '--cmd_port', str(cmd_port),
            '--tm_port', str(tm_port), '--ui_state_dir', str(state), '--ui_data_root', str(tmp_path),
            '--ui_cache_dir', str(tmp_path / 'cache'), '--headless'],
            cwd=src, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert process.poll() is None, log.read_text()
                try:
                    if requests.get(ui + '/login', timeout=.3).status_code == 200:
                        break
                except requests.RequestException:
                    time.sleep(.05)
            else:
                pytest.fail(log.read_text())
            assert requests.get(api + '/health', timeout=2).status_code == 200
            assert requests.get(ui + '/', allow_redirects=False, timeout=2).status_code == 302
            assert requests.get(ui + '/api/auth/me', timeout=2).status_code == 401
            assert requests.get(ui + '/api/diagnostics/tm/log', timeout=2).status_code == 401
            session = requests.Session()
            page = session.get(ui + '/login', timeout=2)
            token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text).group(1)
            result = session.post(ui + '/login', data={'username': 'admin', 'password': 'test-password-123', 'csrf_token': token}, timeout=3)
            assert result.status_code == 200 and 'auth.js' in result.text
            identity = session.get(ui + '/api/auth/me', timeout=2).json()
            assert identity['can_manage_users']
            command = session.post(ui + '/api/commands', headers={'X-CSRFToken': identity['csrf_token']},
                json={'app': 'web', 'cmd': 'trace', 'value': 'ON'}, timeout=8)
            assert command.status_code == 200 and command.json()['status'] == 'success', command.text
            assert session.get(ui + '/api/diagnostics/web/trace', timeout=2).status_code == 200
            assert session.get(ui + '/api/web/profiles', timeout=3).status_code == 200
            assert session.get(ui + '/api/opt/config', timeout=2).status_code == 200
            diagnostic = session.get(ui + '/api/diagnostics/tm/log', timeout=2)
            assert diagnostic.status_code == 200 and diagnostic.json()['payload']['content'] == 'live test application log\n'
            trace = session.get(ui + '/api/diagnostics/tm/trace', timeout=2)
            assert trace.status_code == 200 and 'live test trace' in trace.json()['payload']['content']
            download = session.get(ui + '/api/diagnostics/tm/log?download=1', timeout=2)
            assert download.content == (logs / 'app/tm.log').read_bytes()
            # Port-sharing cookies must never grant access to the public token API.
            assert session.get(api + '/api/profiles', timeout=2).status_code == 401
            for path in ('/', '/login', '/api/opt/config', '/api/admin/users', '/auth.js', '/api/diagnostics/tm/log', '/api/commands'):
                assert session.get(api + path, timeout=2).status_code == 404
            assert requests.get(ui + '/login', headers={'Host': 'public.example'}, timeout=2).status_code == 403
            assert session.post(ui + '/api/admin/users', json={'username': 'other', 'password': 'other-password-123'}, timeout=2).status_code == 400
            response = session.post(ui + '/api/admin/users', headers={'X-CSRFToken': identity['csrf_token']}, json={
                'username': 'other', 'password': 'other-password-123', 'role': 'viewer'}, timeout=3)
            assert response.status_code == 201
            process.send_signal(stop_signal)
            process.wait(timeout=10)
            assert process.returncode == 0, log.read_text()
            assert 'Exception in thread' not in log.read_text(), log.read_text()
            assert 'Bad file descriptor' not in log.read_text(), log.read_text()
            assert 'Error stopping WEB listener' not in log.read_text(), log.read_text()
            for port in (api_port, ui_port):
                with socket.socket() as sock:
                    assert sock.connect_ex(('127.0.0.1', port)) != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
