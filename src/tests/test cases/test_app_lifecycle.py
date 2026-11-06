import ast
from contextlib import ExitStack
from pathlib import Path
from queue import Queue
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
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
    app.get_args = mock.Mock(return_value=SimpleNamespace(web_api_threads=10))
    app.telmodel = SimpleNamespace(sdp=SimpleNamespace(app=SimpleNamespace(arguments=None)))
    app.start.side_effect = app.request_stop
    thread = mock.Mock()
    webhook = mock.Mock()
    api_options = {}

    def make_api(**kwargs):
        api_options.update(kwargs)
        return webhook

    namespace = {
        constructor: lambda: app,
        "logger": mock.Mock(), "threading": thread,
        "WebAPIHandler": make_api, "ExitStack": ExitStack,
    }
    exec(compile(ast.Module(body=[main], type_ignores=[]), str(path), "exec"), namespace)
    with mock.patch("env.app.logging.shutdown") as shutdown:
        namespace["main"]()
    app.start.assert_called_once_with()
    app.stop.assert_called_once_with()
    shutdown.assert_called_once_with()
    thread.Thread.assert_not_called()
    if constructor == "TelescopeManager":
        assert api_options['threads'] == 10
        webhook.start.assert_called_once_with()
        webhook.stop.assert_called_once_with()
        assert api_options['telmodel'] is app.telmodel
        assert api_options['event_queue'] is app.get_queue.return_value


def test_status_thread_exits_on_shutdown_before_cleanup_clears_running(caplog):
    app = lifecycle_app()
    app.app_model.app_running = True
    app.status_update_event = mock.Mock()
    app.status_update_event.is_update_pending.return_value = False
    app.heartbeat = mock.Mock()
    app.report_availability = mock.Mock()
    waits = []

    def wait(timeout):
        waits.append(timeout)
        app.status_update_event.is_update_pending.return_value = True
        app.request_stop()
        # A second iteration would reproduce the old busy loop. Bound this test.
        if len(waits) > 1:
            app.app_model.app_running = False
        return True

    app.wait_for_stop = wait
    with caplog.at_level('INFO', logger='env.app'):
        app.run()
    assert app.app_model.app_running  # Main-thread cleanup has not started yet.
    assert waits == [30]
    app.status_update_event.enqueue.assert_called_once_with(app.queue)
    app.heartbeat.assert_called_once_with()
    assert 'Debug Info' not in caplog.text


def test_status_thread_does_no_work_when_shutdown_already_requested():
    app = lifecycle_app()
    app.app_model.app_running = True
    app.heartbeat = mock.Mock()
    app.request_stop()
    app.run()
    app.heartbeat.assert_not_called()


def test_status_thread_preserves_pending_update_diagnostics(caplog):
    app = lifecycle_app()
    app.app_model.app_running = True
    app.status_update_event = mock.Mock()
    app.status_update_event.is_update_pending.return_value = True
    app.status_update_event.get_dequeued_count.return_value = 1
    app.status_update_event.get_millis_since_update_enqueued.return_value = 30000
    app.processors = []
    app.heartbeat = mock.Mock()
    app.report_availability = mock.Mock()
    app.wait_for_stop = lambda timeout: (app.request_stop() or True)
    with caplog.at_level('INFO', logger='env.app'):
        app.run()
    assert 'Debug Info' in caplog.text
    assert 'pending for 30000 ms' in caplog.text
