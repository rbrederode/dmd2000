import os
from pathlib import Path
import shlex
import subprocess

import pytest


LAUNCHER = Path(__file__).resolve().parents[3] / "scripts/launch/launch_app.sh"


def make_environment(repo, name):
    binaries = repo / name / "bin"
    binaries.mkdir(parents=True)
    (binaries / "activate").write_text(f"export PATH={shlex.quote(str(binaries))}:$PATH\n")
    interpreter = binaries / "python"
    interpreter.write_text(f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(name)}\n")
    interpreter.chmod(0o755)


def launch_command(repo):
    return subprocess.run(
        ["bash", str(LAUNCHER), "--title", "ws001", "--session-dir", "src",
         "--repo-root", str(repo), "--no-keep-open", "--print-command", "--", "python", "-m", "ws.ws"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.mark.parametrize("environments, expected", [
    ([".venv"], ".venv"),
    (["venv"], "venv"),
    (["venv", ".venv"], ".venv"),
])
def test_new_shell_uses_repository_environment_with_spaces_in_path(tmp_path, environments, expected):
    repo = tmp_path / "MacBook project"
    (repo / "src").mkdir(parents=True)
    for name in environments:
        make_environment(repo, name)
    command = launch_command(repo)
    environment = dict(os.environ, PATH="/usr/bin:/bin")
    environment.pop("VIRTUAL_ENV", None)
    result = subprocess.run(["bash", "-c", command], check=True, env=environment,
                            capture_output=True, text=True)
    assert result.stdout.endswith(expected + "\n")


def test_repository_pyenv_configuration_retains_precedence(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / ".python-version").write_text("project-python\n")
    make_environment(tmp_path, ".venv")
    command = launch_command(tmp_path)
    assert "unset PYENV_VERSION VIRTUAL_ENV" in command
    assert "source " not in command


def test_no_repository_environment_leaves_interpreter_selection_unchanged(tmp_path):
    (tmp_path / "src").mkdir()
    command = launch_command(tmp_path)
    assert "source " not in command
    assert " && python -m ws.ws" in command
