import logging
import threading
from unittest.mock import Mock

from util.log import AppRoutingHandler, get_current_app, set_current_app


def _record() -> logging.LogRecord:
    return logging.LogRecord(
        name="obs.scan",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="late shutdown message",
        args=(),
        exc_info=None,
    )


def test_closed_app_routing_handler_does_not_create_late_file_handler(tmp_path):
    handler = AppRoutingHandler(str(tmp_path))
    handler.close()
    handler._handler_for_app = Mock(side_effect=AssertionError("must not create a handler after shutdown"))

    handler.emit(_record())

    handler._handler_for_app.assert_not_called()


def test_app_context_and_routing_are_shared_by_worker_threads(tmp_path):
    handler = AppRoutingHandler(str(tmp_path))
    previous_app = get_current_app()
    set_current_app("dm")
    worker_context = []

    def emit_from_worker():
        worker_context.append(get_current_app())
        handler.emit(_record())

    worker = threading.Thread(target=emit_from_worker)
    worker.start()
    worker.join()
    handler.close()
    set_current_app(previous_app)

    assert worker_context == ["dm"]
    assert "late shutdown message" in (tmp_path / "dm.log").read_text()
