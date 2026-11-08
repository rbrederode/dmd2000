"""Profile-wide log roots are available before logging handlers are constructed."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from util.xbase import XAPIValidationFailed
from models.launch import LaunchConfigModel, LaunchModel
from util.log_config import load_log_dir, resolve_log_dir, runtime_log_dir
from util.launch import build_launch_command


def test_log_root_model_round_trip_and_legacy_defaults():
    model = LaunchConfigModel(profile='test', log_dir='shared/logs')
    assert LaunchConfigModel.from_dict(model.to_dict()).log_dir == 'shared/logs'
    assert LaunchConfigModel.from_dict({'profile': 'old', 'launches': []}).log_dir is None
    with pytest.raises(XAPIValidationFailed):
        LaunchConfigModel(profile='test', log_dir=' ')


def test_log_paths_resolve_from_repo_and_expand_home(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    profile = repo / 'src/config/local'
    profile.mkdir(parents=True)
    (profile / 'LaunchConfig.json').write_text(json.dumps({'log_dir': 'shared/logs'}))
    monkeypatch.chdir(tmp_path)
    assert load_log_dir('local', repo_root=repo) == str(repo / 'shared/logs')
    assert load_log_dir('missing', repo_root=repo) == str(repo / 'logs')
    assert resolve_log_dir('~/test-logs') == Path.home() / 'test-logs'
    assert resolve_log_dir(str(tmp_path / 'absolute'), repo) == tmp_path / 'absolute'


def test_early_logging_root_cli_and_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr('util.log_config.REPO_ROOT', tmp_path)
    profile = tmp_path / 'profile'
    profile.mkdir()
    (profile / 'LaunchConfig.json').write_text('{"log_dir":"profile-logs"}')
    args = ['--profile_dir', str(profile)]
    assert runtime_log_dir(args, {}) == str(tmp_path / 'profile-logs')
    assert runtime_log_dir(args, {'DMD_LOG_DIR': 'environment-logs'}) == str(tmp_path / 'environment-logs')
    assert runtime_log_dir(args + ['--log_dir', 'cli-logs'], {'DMD_LOG_DIR': 'environment-logs'}) == str(tmp_path / 'cli-logs')


def test_launcher_passes_shared_log_root_and_per_app_override(tmp_path, monkeypatch):
    monkeypatch.delenv('DMD_LOG_DIR', raising=False)
    launch = LaunchModel(app_name='dig', entity_id='dig001', args=['-m', 'dig.dig'])
    command = build_launch_command(tmp_path, launch, log_dir='shared logs')
    assert 'DMD_LOG_DIR=' + str(tmp_path / 'shared logs') in command
    launch.env = {'DMD_LOG_DIR': 'special'}
    command = build_launch_command(tmp_path, launch, log_dir='shared logs')
    assert 'DMD_LOG_DIR=' + str(tmp_path / 'special') in command


def test_fresh_process_writes_logs_traces_and_availability_under_profile_root(tmp_path):
    profile = tmp_path / 'profile'
    profile.mkdir()
    root = tmp_path / 'shared logs'
    (profile / 'LaunchConfig.json').write_text(json.dumps({'log_dir': str(root)}))
    src = Path(__file__).resolve().parents[2]
    script = '''
import logging
from pathlib import Path
from util import log
from env.app import App
from models.app import AppModel
from util.html_trace import HTMLTrace
log.set_current_app('tm')
logging.getLogger('test').info('configured application log')
app = App.__new__(App)
app.stop = lambda: None
app.app_model = AppModel(app_name='tm')
app.get_availability_logger().info('configured availability log')
trace = HTMLTrace(Path(log.repo_logs_dir) / 'trace', 'tm', 'v1-test')
trace.start()
trace.stop()
assert App.logs_dir == Path(log.repo_logs_dir)
logging.shutdown()
'''
    environment = dict(os.environ, PYTHONPATH=str(src))
    environment.pop('DMD_LOG_DIR', None)
    result = subprocess.run([sys.executable, '-c', script, '--profile_dir', str(profile)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'configured application log' in (root / 'app/tm.log').read_text()
    assert 'configured availability log' in (root / 'availability/tm.log').read_text()
    assert (root / 'trace/tm.html').exists()


def test_web_profile_log_root_switch_does_not_redirect_running_app_logs(tmp_path):
    from util import log
    from web.profiles import ProfileManager
    root_before = log.repo_logs_dir
    for name in ('first', 'second'):
        directory = tmp_path / name
        directory.mkdir()
        config = LaunchConfigModel(profile=name, log_dir=str(tmp_path / (name + '-logs')))
        (directory / 'LaunchConfig.json').write_text(json.dumps(config.to_dict()))
    manager = ProfileManager(tmp_path / 'first', log_dir=str(tmp_path / 'startup-logs'))
    assert manager.current.log_dir == str(tmp_path / 'startup-logs')
    assert manager.select('second').log_dir == str(tmp_path / 'second-logs')
    assert manager.current.to_dict()['log_dir'] == str(tmp_path / 'second-logs')
    assert manager.reload().log_dir == str(tmp_path / 'second-logs')
    assert log.repo_logs_dir == root_before
