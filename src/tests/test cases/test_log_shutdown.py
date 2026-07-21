import logging
from unittest.mock import Mock

from util.log import AppRoutingHandler, PerModuleTimedRotatingHandler


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


def test_closed_per_module_handler_does_not_create_late_file_handler(tmp_path):
    handler = PerModuleTimedRotatingHandler(str(tmp_path))
    handler.close()
    handler._handler_for = Mock(side_effect=AssertionError("must not create a handler after shutdown"))

    handler.emit(_record())

    handler._handler_for.assert_not_called()


def test_closed_app_routing_handler_does_not_create_late_file_handler(tmp_path):
    handler = AppRoutingHandler(str(tmp_path))
    handler.close()
    handler._handler_for_app = Mock(side_effect=AssertionError("must not create a handler after shutdown"))

    handler.emit(_record())

    handler._handler_for_app.assert_not_called()
