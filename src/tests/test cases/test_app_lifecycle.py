import ast
from contextlib import ExitStack
from pathlib import Path
from queue import Queue
import signal
import subprocess
import sys
import threading
import time
from unittest import mock

import pytest

from env.app import App
from models.app import AppModel


def lifecycle_app():
    class TestApp(App):
        def __del__(self):
            pass  # The lifecycle under test owns cleanup.

    app = TestApp.__new__(TestApp)
    app._stop_event = threading.Event()
    app.app_model = AppModel(app_name="test")
    app.queue = Queue()
    app.interfaces = {}
    app.start = mock.Mock()
    app.stop = mock.Mock()
    return app


@pytest.mark.parametrize("failure", [None, "start", "body", "stop"])
def test_lifecycle_always_cleans_up_and_restores_signal_handlers(failure):
    app = lifecycle_app()
    calls = []
    app.start.side_effect = lambda: calls.append("start")

    def stop():
        calls.append("stop")
        if failure == "stop":
            raise RuntimeError("stop failure")

    app.stop.side_effect = stop
    if failure == "start":
        app.start.side_effect = RuntimeError("start failure")
    original = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def run():
        with app.running():
            assert all(signal.getsignal(sig) != handler for sig, handler in original.items())
            if failure == "body":
                raise RuntimeError("body failure")
            app.request_stop()
            assert app.wait_for_stop(0)

    with mock.patch("env.app.logging.shutdown", side_effect=lambda: calls.append("logs")):
        if failure:
            with pytest.raises(RuntimeError, match=f"{failure} failure"):
                run()
        else:
            run()
    app.stop.assert_called_once_with()
    assert calls[-2:] == ["stop", "logs"]
    assert all(signal.getsignal(sig) == handler for sig, handler in original.items())


def test_keyboard_interrupt_uses_same_cleanup_path():
    app = lifecycle_app()
    with mock.patch("env.app.logging.shutdown") as shutdown:
        with app.running():
            raise KeyboardInterrupt
    assert app.stop_requested
    app.stop.assert_called_once_with()
    shutdown.assert_called_once_with()


def test_lifecycle_rejects_worker_thread_signal_registration():
    app = lifecycle_app()
    errors = []

    def run():
        try:
            with app.running():
                pass
        except RuntimeError as error:
            errors.append(str(error))

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=2)
    assert errors == ["App.running() must execute on the main thread"]
    app.start.assert_not_called()


def test_interface_cleanup_continues_after_failure_and_stops_shared_endpoint_once():
    app = lifecycle_app()
    broken = mock.Mock()
    broken.stop.side_effect = OSError("socket failure")
    healthy = mock.Mock()
    app.interfaces = {
        "broken": (None, broken, None),
        "healthy": (None, healthy, None),
        "same endpoint": (None, healthy, None),
    }
    app.stop_interfaces()
    app.stop_interfaces()
    broken.stop.assert_called_once_with()
    healthy.stop.assert_called_once_with()
    assert app.interfaces == {}


def test_component_failure_does_not_prevent_remaining_cleanup():
    app = lifecycle_app()
    app.stop_timer_manager = mock.Mock(side_effect=OSError("timer failure"))
    app.stop_cmd_server = mock.Mock()
    app.stop_processors = mock.Mock()
    app.stop_status_thread = mock.Mock()
    app.stop_interfaces = mock.Mock()
    App.stop(app)
    assert app.stop_requested
    app.stop_processors.assert_called_once_with()
    app.stop_status_thread.assert_called_once_with()
    app.stop_interfaces.assert_called_once_with()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_real_signal_exits_cleanly_and_flushes_buffered_logs(tmp_path, signum):
    ready = tmp_path / "ready"
    logfile = tmp_path / "shutdown.log"
    script = """
import logging
from logging.handlers import MemoryHandler
from pathlib import Path
import sys
import threading
sys.path.insert(0, sys.argv[3])
from env.app import App

target = logging.FileHandler(sys.argv[2])
buffer = MemoryHandler(capacity=100, target=target)
logger = logging.getLogger('lifecycle-test')
logger.setLevel(logging.INFO)
logger.addHandler(buffer)
logger.propagate = False

class TestApp(App):
    def __init__(self):
        self._stop_event = threading.Event()
    def start(self):
        Path(sys.argv[1]).write_text('ready')
    def stop(self):
        logger.info('cleanup completed')

app = TestApp()
with app.running():
    app.wait_for_stop()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(ready), str(logfile), str(Path(__file__).resolve().parents[2])],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), process.communicate(timeout=2)
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert "cleanup completed" in logfile.read_text()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


@pytest.mark.parametrize("relative_path, constructor", [
    ("ws/ws.py", "WeatherStation"),
    ("dig/dig.py", "Digitiser"),
    ("dsh/dm.py", "DM"),
    ("tm/tm.py", "TelescopeManager"),
    ("sdp/sdp.py", "SDP"),
])
def test_headless_entry_points_use_shared_lifecycle(relative_path, constructor):
    # Exercise the real main functions without loading hardware or GUI imports.
    path = Path(__file__).resolve().parents[2] / relative_path
    module = ast.parse(path.read_text())
    main = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    app = lifecycle_app()
    app.is_headless = mock.Mock(return_value=True)
    app.get_queue = mock.Mock(return_value=Queue())
    app.start.side_effect = app.request_stop
    thread = mock.Mock()
    webhook = mock.Mock()
    namespace = {
        constructor: lambda: app,
        "logger": mock.Mock(), "threading": thread,
        "WebhookHandler": lambda **kwargs: webhook, "ExitStack": ExitStack,
    }
    exec(compile(ast.Module(body=[main], type_ignores=[]), str(path), "exec"), namespace)
    with mock.patch("env.app.logging.shutdown") as shutdown:
        namespace["main"]()
    app.start.assert_called_once_with()
    app.stop.assert_called_once_with()
    shutdown.assert_called_once_with()
    thread.Thread.assert_not_called()
    if constructor == "TelescopeManager":
        webhook.start.assert_called_once_with()
        webhook.stop.assert_called_once_with()
