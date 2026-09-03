import importlib.util
import subprocess
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "dig" / "airspy.py"
SPEC = importlib.util.spec_from_file_location("airspy_demo", MODULE_PATH)
airspy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(airspy)


def test_airspy_capture_returns_complex64_iq(monkeypatch):
    captured_command = None

    monkeypatch.setattr(airspy.shutil, "which", lambda executable: "/fake/airspy_rx")

    def fake_run(command, **kwargs):
        nonlocal captured_command
        captured_command = command
        capture_path = Path(command[command.index("-r") + 1])
        np.array([1.0, -1.0, 0.5, 0.25], dtype=np.float32).tofile(capture_path)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(airspy.subprocess, "run", fake_run)

    radio = airspy.Airspy(
        center_frequency_hz=1420.4058e6,
        sample_rate_hz=3e6,
        executable="airspy_rx",
    )
    samples = radio.read_samples(2)

    assert samples.dtype == np.complex64
    np.testing.assert_array_equal(samples, np.array([1.0 - 1.0j, 0.5 + 0.25j]))
    assert captured_command[captured_command.index("-f") + 1] == "1420.405800000"
    assert captured_command[captured_command.index("-a") + 1] == "3000000"


def test_airspy_rejects_invalid_settings(monkeypatch):
    monkeypatch.setattr(airspy.shutil, "which", lambda executable: "/fake/airspy_rx")
    radio = airspy.Airspy(executable="airspy_rx")

    for invalid_frequency in (0, 23.9e6, 1.91e9):
        try:
            radio.set_center_frequency(invalid_frequency)
        except ValueError:
            pass
        else:
            raise AssertionError("Expected invalid frequency to raise ValueError")

    try:
        radio.set_sample_rate(0)
    except ValueError:
        pass
    else:
        raise AssertionError("Expected invalid sample rate to raise ValueError")
