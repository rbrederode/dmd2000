"""Relocated SDK utilities retain repository paths and direct script support."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from sdk.util import launch, log, log_config, stop, version


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_sdk_paths_still_resolve_repository_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert version._repo_root() == REPO_ROOT
    assert log_config.REPO_ROOT == REPO_ROOT
    assert Path(log.project_root) == REPO_ROOT
    assert launch.resolve_repo_root(None) == REPO_ROOT
    assert stop.resolve_repo_root() == REPO_ROOT
    assert launch.SRC_DIR == REPO_ROOT / 'src'


@pytest.mark.parametrize('tool', ['launch', 'stop', 'lab_profiler', 'milkyway_plot'])
def test_relocated_scripts_support_direct_execution_without_pythonpath(tmp_path, tool):
    environment = os.environ.copy()
    environment.pop('PYTHONPATH', None)
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / 'src' / 'sdk' / 'util' / f'{tool}.py'), '--help'],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert 'usage:' in result.stdout
