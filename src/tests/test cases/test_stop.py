import sys
from pathlib import Path

import pytest

from util import stop


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_build_stop_command_uses_profile_dm_endpoint():
    command = stop.build_stop_command(
        REPO_ROOT, "local", "dish001", timeout=2.5
    )

    assert command == [
        sys.executable,
        "-m",
        "util.cmd_app",
        "--host",
        "127.0.0.1",
        "--port",
        "60003",
        "--system",
        "dm",
        "--timeout",
        "2.5",
        "stop",
        "dish001",
    ]


def test_build_stop_command_honours_endpoint_overrides():
    command = stop.build_stop_command(
        REPO_ROOT,
        "local",
        "dish001",
        host="dm.example.test",
        port=61003,
    )

    assert command[command.index("--host") + 1] == "dm.example.test"
    assert command[command.index("--port") + 1] == "61003"


def test_build_stop_command_rejects_unknown_dish():
    with pytest.raises(ValueError, match="Dish 'missing'"):
        stop.build_stop_command(REPO_ROOT, "local", "missing")


def test_print_command_does_not_send_stop(monkeypatch, capsys):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called")

    monkeypatch.setattr(stop.subprocess, "run", unexpected_run)

    assert stop.main([
        "--profile",
        "local",
        "--print-command",
        "dish001",
    ]) == 0

    output = capsys.readouterr().out
    assert "-m util.cmd_app" in output
    assert output.rstrip().endswith("stop dish001")
