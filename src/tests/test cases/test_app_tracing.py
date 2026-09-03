from datetime import datetime, timezone
import argparse
import gzip
import logging
from queue import Queue
from unittest import mock

import pytest

from api.api import API
from api import protocol as dmd_protocol
from api.command import CommandAPI
from env.app import App, _should_manage_trace_archives
from env.app_processor import AppProcessor
from env.processor import Processor
from env.events import ConnectEvent, DataEvent, DisconnectEvent
from ipc.action import Action
from ipc.message import APIMessage
from dsh.drivers.md01.md01_msg import MD01Msg
from models.app import AppModel
from models.comms import InterfaceType
from util.html_trace import HTMLTrace


class PassthroughAPI(API):
    def get_api_version(self):
        return "1.0"

    def get_legacy_supported_versions(self):
        return []

    def validate(self, api_msg):
        return None

    def translate(self, api_msg, target_version="1.0"):
        return api_msg


class CaptureEndpoint:
    def __init__(self):
        self.sent = []

    def send(self, msg, connection=None):
        data = msg.to_data()
        self.sent.append((msg, connection, data))


class FailingEndpoint(CaptureEndpoint):
    def send(self, msg, connection=None):
        raise RuntimeError("send failed")


class TraceDriver:
    def __init__(self, endpoint, trace_dir, tracing=True):
        self.app_model = AppModel(app_name="receiver", app_tracing=tracing)
        self.endpoint = endpoint
        self.api = PassthroughAPI()
        self.entity_connection_map = {}
        self.last_error = None
        self.trace = HTMLTrace(
            output_dir=trace_dir,
            app_name="receiver",
            app_version="v1.2.3-45",
            server_name="test-host",
        )
        if tracing:
            self.trace.start("Test startup")

    def get_interface(self, system_name):
        return self.api, self.endpoint, InterfaceType.APP_APP

    def set_last_err(self, message):
        self.last_error = message
        return message


def make_message(from_system="sender", to_system="receiver", api_call=None):
    msg = APIMessage()
    msg.set_json_api_header(
        api_version="1.0",
        dt=datetime.now(timezone.utc),
        from_system=from_system,
        to_system=to_system,
        api_call=api_call or {
            "msg_type": "req",
            "action_code": "get",
            "property": "status",
        },
    )
    return msg


def read_trace(driver):
    return driver.trace.path.read_text(encoding="utf-8")


def test_handle_trace_get_and_set_uses_shared_app_model_state(tmp_path):
    driver = TraceDriver(CaptureEndpoint(), tmp_path, tracing=False)
    processor = AppProcessor(name="trace-test", driver=driver)

    set_on = make_message(api_call={
        "msg_type": "req", "action_code": "set", "property": "trace", "value": "ON",
    })
    response = processor._handle_trace_req(set_on, set_on.get_api_call())

    assert driver.app_model.app_tracing is True
    assert response.get_api_call()["msg_type"] == "rsp"
    assert response.get_api_call()["status"] == "success"
    assert response.get_api_call()["value"] == "ON"

    get_trace = make_message(api_call={
        "msg_type": "req", "action_code": "get", "property": "trace",
    })
    response = processor._handle_trace_req(get_trace, get_trace.get_api_call())
    assert response.get_api_call()["value"] == "ON"

    set_off = make_message(api_call={
        "msg_type": "req", "action_code": "set", "property": "trace", "value": "OFF",
    })
    response = processor._handle_trace_req(set_off, set_off.get_api_call())

    assert driver.app_model.app_tracing is False
    assert response.get_api_call()["value"] == "OFF"
    trace_html = read_trace(driver)
    assert "Enabled by API request" in trace_html
    assert "Trace started" in trace_html
    assert "Disabled by API request" in trace_html
    assert "Trace stopped" in trace_html


def test_handle_trace_rejects_unknown_value_without_changing_state(tmp_path):
    driver = TraceDriver(CaptureEndpoint(), tmp_path, tracing=False)
    processor = AppProcessor(name="trace-test", driver=driver)
    request = make_message(api_call={
        "msg_type": "req", "action_code": "set", "property": "trace", "value": "INVALID",
    })

    response = processor._handle_trace_req(request, request.get_api_call())

    assert driver.app_model.app_tracing is False
    assert response.get_api_call()["status"] == "error"
    assert response.get_api_call()["value"] == "OFF"


def test_handle_debug_uses_application_wide_state_across_processors(tmp_path):
    driver = TraceDriver(CaptureEndpoint(), tmp_path, tracing=False)
    first_processor = AppProcessor(name="debug-1", driver=driver)
    second_processor = AppProcessor(name="debug-2", driver=driver)
    original_level = logging.getLogger().level

    try:
        set_on = make_message(api_call={
            "msg_type": "req", "action_code": "set", "property": "debug", "value": "ON",
        })
        response = first_processor._handle_debug_req(set_on, set_on.get_api_call())

        assert driver.app_model.app_debug is True
        assert logging.getLogger().level == logging.DEBUG
        assert response.get_api_call()["status"] == "success"
        assert response.get_api_call()["value"] == "ON"

        get_debug = make_message(api_call={
            "msg_type": "req", "action_code": "get", "property": "debug",
        })
        response = second_processor._handle_debug_req(get_debug, get_debug.get_api_call())
        assert response.get_api_call()["value"] == "ON"

        set_off = make_message(api_call={
            "msg_type": "req", "action_code": "set", "property": "debug", "value": "OFF",
        })
        response = second_processor._handle_debug_req(set_off, set_off.get_api_call())
        assert driver.app_model.app_debug is False
        assert logging.getLogger().level == logging.INFO
        assert response.get_api_call()["value"] == "OFF"
    finally:
        logging.getLogger().setLevel(original_level)


def test_command_endpoint_selects_command_api_and_replies_on_same_connection(tmp_path):
    endpoint = CaptureEndpoint()
    endpoint.description = dmd_protocol.CMD
    driver = TraceDriver(endpoint, tmp_path, tracing=False)
    driver.api = CommandAPI()
    processor = AppProcessor(name="command-test", driver=driver)
    connection = object()
    request = make_message(
        from_system=dmd_protocol.CMD,
        api_call={
            "msg_type": "req",
            "action_code": "set",
            "property": "debug",
            "value": "ON",
        },
    )

    original_level = logging.getLogger().level
    try:
        event = DataEvent(
            endpoint,
            connection,
            ("127.0.0.1", 61000),
            request.to_data(),
            datetime.now(timezone.utc),
        )
        assert processor.process_event(event) is True
        assert driver.app_model.app_debug is True
        response, response_connection, _ = endpoint.sent[0]
        assert response_connection is connection
        assert response.get_to_system() == dmd_protocol.CMD
        assert response.get_api_call()["status"] == dmd_protocol.STATUS_SUCCESS
    finally:
        logging.getLogger().setLevel(original_level)


def test_resync_command_calls_process_resync_handler(tmp_path):
    class ResyncDriver(TraceDriver):
        def __init__(self, endpoint, trace_dir):
            super().__init__(endpoint, trace_dir, tracing=False)
            self.resync_count = 0

        def process_resync(self):
            self.resync_count += 1

    driver = ResyncDriver(CaptureEndpoint(), tmp_path)
    processor = AppProcessor(name="resync-test", driver=driver)
    request = make_message(api_call={
        "msg_type": "req",
        "action_code": "resync",
    })

    with mock.patch.object(Processor, "single_thread") as single_thread, \
            mock.patch.object(Processor, "free_thread") as free_thread:
        response = processor._handle_resync_req(request)

    assert driver.resync_count == 1
    assert response.get_api_call()["status"] == dmd_protocol.STATUS_SUCCESS
    single_thread.assert_called_once_with()
    free_thread.assert_called_once_with()


def test_resync_command_frees_processors_when_handler_fails(tmp_path):
    class FailingResyncDriver(TraceDriver):
        def process_resync(self):
            raise RuntimeError("configuration reload failed")

    driver = FailingResyncDriver(CaptureEndpoint(), tmp_path, tracing=False)
    processor = AppProcessor(name="resync-test", driver=driver)
    request = make_message(api_call={
        "msg_type": "req",
        "action_code": "resync",
    })

    with mock.patch.object(Processor, "single_thread") as single_thread, \
            mock.patch.object(Processor, "free_thread") as free_thread:
        response = processor._handle_resync_req(request)

    assert response.get_api_call()["status"] == dmd_protocol.STATUS_ERROR
    assert "configuration reload failed" in response.get_api_call()["message"]
    single_thread.assert_called_once_with()
    free_thread.assert_called_once_with()


def test_app_registers_command_server_as_cmd_interface():
    app = App.__new__(App)
    app.app_model = AppModel(
        app_name=dmd_protocol.DM,
        app_cmd_host="127.0.0.1",
        app_cmd_port=60002,
    )
    app.queue = Queue()
    app.interfaces = {}

    class FakeTCPServer:
        def __init__(self, description, queue, host, port):
            self.description = description
            self.queue = queue
            self.host = host
            self.port = port
            self.started = False
            self.stopped = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    with mock.patch("env.app.TCPServer", FakeTCPServer):
        app.start_cmd_server()
        api, endpoint, interface_type = app.get_interface(dmd_protocol.CMD)

        assert isinstance(api, CommandAPI)
        assert endpoint.description == dmd_protocol.CMD
        assert endpoint.host == "127.0.0.1"
        assert endpoint.port == 60002
        assert endpoint.started is True
        assert interface_type == InterfaceType.APP_APP

        app.stop_cmd_server()
        assert dmd_protocol.CMD not in app.interfaces


def test_receive_hook_writes_clickable_flow_and_response(tmp_path):
    endpoint = CaptureEndpoint()
    driver = TraceDriver(endpoint, tmp_path)
    processor = AppProcessor(name="trace-test", driver=driver)
    msg = make_message(api_call={
        "msg_type": "req", "action_code": "get", "property": "trace",
    })
    connection = object()
    received_at = datetime(2026, 8, 15, 12, 34, 56, 789000, timezone.utc)
    event = DataEvent(
        local_sap=endpoint,
        remote_conn=connection,
        remote_addr=("127.0.0.1", 50000),
        data=msg.to_data(),
        timestamp=received_at,
    )

    assert processor.process_event(event) is True

    trace_html = read_trace(driver)
    assert '<details class="message rx">' in trace_html
    assert "2026-08-15 12:34:56.789 UTC" in trace_html
    assert "[REQ GET]" in trace_html
    assert "Received from sender" in trace_html
    assert "property=trace" in trace_html
    assert '<details class="message tx">' in trace_html
    response, response_connection, response_data = endpoint.sent[0]
    assert response_connection is connection
    assert response_data == response.msg_data
    assert response.get_api_call()["msg_type"] == "rsp"
    assert response.get_api_call()["value"] == "ON"


def test_send_hook_traces_wire_data_after_single_serialization(tmp_path):
    endpoint = CaptureEndpoint()
    driver = TraceDriver(endpoint, tmp_path)
    processor = AppProcessor(name="trace-test", driver=driver)
    msg = make_message(from_system="receiver", to_system="sender")

    with mock.patch.object(msg, "to_data", wraps=msg.to_data) as to_data:
        processor.performActions(Action().set_msg_to_remote(msg))

    trace_html = read_trace(driver)
    assert endpoint.sent[0][0] is msg
    assert endpoint.sent[0][2] == msg.msg_data
    assert to_data.call_count == 1
    assert "[REQ GET]" in trace_html
    assert "Sent to sender" in trace_html
    assert "Data (hex)" in trace_html
    assert "API header" in trace_html


def test_html_trace_uses_protocol_summary_for_md01_messages(tmp_path):
    trace = HTMLTrace(tmp_path, "dm", "v1", server_name="host")
    trace.start("Test startup")
    command = MD01Msg()
    command.set_cmd(MD01Msg.CMD_STATUS)
    command.to_data()
    response = MD01Msg()
    response.from_data(bytes.fromhex("57030600000a030600000a20"))

    trace.log_message("TX", command, "MD01 dish001 (192.0.2.1:23)")
    trace.log_message("RX", response, "MD01 dish001 (192.0.2.1:23)")

    trace_html = trace.path.read_text(encoding="utf-8")
    assert "[MD01 STATUS]" in trace_html
    assert "[MD01 RESPONSE]" in trace_html
    assert "Sent to MD01 dish001 (192.0.2.1:23)" in trace_html
    assert "Received from MD01 dish001 (192.0.2.1:23)" in trace_html
    assert "bytes=13" in trace_html
    assert "bytes=12" in trace_html
    assert "Data (hex)" in trace_html


def test_connect_and_disconnect_events_are_written_to_html_trace(tmp_path):
    endpoint = CaptureEndpoint()
    endpoint.description = "tm"
    driver = TraceDriver(endpoint, tmp_path)
    processor = AppProcessor(name="trace-test", driver=driver)
    remote_addr = ("192.0.2.10", 55000)
    connected_at = datetime(2026, 8, 15, 12, 34, 56, 789000, timezone.utc)
    disconnected_at = datetime(2026, 8, 15, 12, 35, 1, 123000, timezone.utc)

    assert processor.process_event(
        ConnectEvent(endpoint, object(), remote_addr, connected_at)
    ) is True
    assert processor.process_event(
        DisconnectEvent(endpoint, object(), remote_addr, disconnected_at)
    ) is True

    trace_html = read_trace(driver)
    assert '<div class="connection connected">' in trace_html
    assert "2026-08-15 12:34:56.789 UTC" in trace_html
    assert '<span class="direction">CX</span>' in trace_html
    assert "Connected service access point tm to (&#x27;192.0.2.10&#x27;, 55000)" in trace_html
    assert '<div class="connection disconnected">' in trace_html
    assert "2026-08-15 12:35:01.123 UTC" in trace_html
    assert '<span class="direction">DCX</span>' in trace_html
    assert "Disconnected service access point tm from (&#x27;192.0.2.10&#x27;, 55000)" in trace_html


def test_trace_hooks_are_silent_when_tracing_is_disabled(tmp_path):
    endpoint = CaptureEndpoint()
    driver = TraceDriver(endpoint, tmp_path, tracing=False)
    processor = AppProcessor(name="trace-test", driver=driver)
    msg = make_message(from_system="receiver", to_system="sender")

    processor.performActions(Action().set_msg_to_remote(msg))

    assert endpoint.sent
    assert not driver.trace.path.exists()


def test_send_exception_prevents_tx_trace(tmp_path):
    endpoint = FailingEndpoint()
    driver = TraceDriver(endpoint, tmp_path)
    processor = AppProcessor(name="trace-test", driver=driver)
    msg = make_message(from_system="receiver", to_system="sender")

    try:
        processor.performActions(Action().set_msg_to_remote(msg))
    except RuntimeError as e:
        assert str(e) == "send failed"
    else:
        raise AssertionError("send exception did not propagate")

    assert '<details class="message tx">' not in read_trace(driver)


def test_html_trace_header_escapes_content_and_records_lifecycle(tmp_path):
    trace = HTMLTrace(
        output_dir=tmp_path,
        app_name="tm<script>",
        app_version="v1<&>",
        server_name="host<&>",
    )
    trace.start("Application <startup>")
    trace.stop("Application <shutdown>")

    trace_html = trace.path.read_text(encoding="utf-8")
    assert "<!doctype html>" in trace_html
    assert "tm&lt;script&gt;" in trace_html
    assert "v1&lt;&amp;&gt;" in trace_html
    assert "host&lt;&amp;&gt;" in trace_html
    assert "Application &lt;startup&gt;" in trace_html
    assert "Application &lt;shutdown&gt;" in trace_html
    assert "Trace started" in trace_html
    assert "Trace stopped" in trace_html


def test_html_trace_writes_expandable_exception_with_stack_trace(tmp_path):
    trace = HTMLTrace(
        output_dir=tmp_path,
        app_name="tm",
        app_version="v1",
        server_name="host",
    )
    trace.start("Test startup")

    try:
        raise ValueError("invalid <dish> configuration")
    except ValueError as exception:
        trace.log_exception(exception, "Failed to load <configuration>")

    trace_html = trace.path.read_text(encoding="utf-8")
    assert '<details class="message exception">' in trace_html
    assert "[EXCEPTION ValueError]" in trace_html
    assert "Failed to load &lt;configuration&gt;" in trace_html
    assert "invalid &lt;dish&gt; configuration" in trace_html
    assert "Traceback (most recent call last):" in trace_html
    assert "test_html_trace_writes_expandable_exception_with_stack_trace" in trace_html
    assert "raise ValueError(&quot;invalid &lt;dish&gt; configuration&quot;)" in trace_html


def test_html_trace_writes_expandable_last_error_with_75_character_preview(tmp_path):
    trace = HTMLTrace(tmp_path, "tm", "v1", server_name="host")
    trace.start("Test startup")
    error_message = "Dish <driver> failed\nwhile applying configuration " + ("x" * 40)
    expected_preview = " ".join(error_message.split())[:75]

    trace.log_last_error_msg(error_message)

    trace_html = trace.path.read_text(encoding="utf-8")
    assert '<details class="message error-message"><summary style="border-left:.35rem solid #c62828">' in trace_html
    assert "details.error-message > summary { border-left: .35rem solid #c62828; }" in trace_html
    assert "Error message reported:" not in trace_html
    assert (expected_preview + "...").replace("<", "&lt;").replace(">", "&gt;") in trace_html
    assert "Dish &lt;driver&gt; failed\nwhile applying configuration" in trace_html


def test_html_trace_does_not_ellipsis_short_last_error(tmp_path):
    trace = HTMLTrace(tmp_path, "tm", "v1", server_name="host")
    trace.start("Test startup")

    trace.log_last_error_msg("Short error")

    trace_html = trace.path.read_text(encoding="utf-8")
    assert "Short error..." not in trace_html
    assert "Short error</summary>" in trace_html


def test_app_set_last_err_records_message_in_html_trace(tmp_path):
    app = App.__new__(App)
    app.app_model = AppModel(app_name="tm", app_tracing=True)
    app.queue = Queue()
    app.stop_timer_manager = mock.MagicMock()
    app.stop_processors = mock.MagicMock()
    app.stop_status_thread = mock.MagicMock()
    app.trace = HTMLTrace(tmp_path, "tm", "v1", server_name="host")
    app.trace.start("Application startup")
    error_time = datetime(2026, 8, 15, 16, 30, 1, 250000, timezone.utc)

    result = app.set_last_err("Pointing subsystem unavailable", error_time)

    assert result == "Pointing subsystem unavailable"
    assert app.app_model.last_err_msg == result
    assert app.app_model.last_err_dt == error_time
    trace_html = app.trace.path.read_text(encoding="utf-8")
    assert "2026-08-15 16:30:01.250 UTC" in trace_html
    assert "Error message reported:" not in trace_html
    assert "Pointing subsystem unavailable" in trace_html


def test_app_set_last_err_survives_trace_write_failure():
    app = App.__new__(App)
    app.app_model = AppModel(app_name="tm", app_tracing=True)
    app.queue = Queue()
    app.stop_timer_manager = mock.MagicMock()
    app.stop_processors = mock.MagicMock()
    app.stop_status_thread = mock.MagicMock()
    app.trace = mock.MagicMock()
    app.trace.log_last_error_msg.side_effect = OSError("trace disk full")

    result = app.set_last_err("Original application error")

    assert result == "Original application error"
    assert app.app_model.last_err_msg == result


def test_html_trace_rejects_non_exception_values(tmp_path):
    trace = HTMLTrace(tmp_path, "tm", "v1", server_name="host")
    trace.start("Test startup")

    try:
        trace.log_exception("not an exception")
    except TypeError as exception:
        assert str(exception) == "exception must be a BaseException instance"
    else:
        raise AssertionError("non-exception trace value was accepted")


def test_app_shutdown_writes_trace_stop_marker(tmp_path):
    app = App.__new__(App)
    app.app_model = AppModel(app_name="tm", app_running=True, app_tracing=True)
    app.trace = HTMLTrace(tmp_path, "tm", "v1", server_name="host")
    app.trace.start("Application startup")
    app.queue = Queue()
    app.stop_timer_manager = mock.MagicMock()
    app.stop_processors = mock.MagicMock()
    app.stop_status_thread = mock.MagicMock()

    app.stop()

    trace_html = app.trace.path.read_text(encoding="utf-8")
    assert "Trace stopped" in trace_html
    assert "Application shutdown" in trace_html


def test_html_trace_rotates_by_size_and_retains_archives(tmp_path):
    trace = HTMLTrace(
        output_dir=tmp_path,
        app_name="tm",
        app_version="v1",
        server_name="host",
        max_bytes=1,
        backup_count=1,
    )
    trace.start("Test startup")
    for _ in range(3):
        msg = make_message()
        msg.to_data()
        trace.log_message("TX", msg, "sender")

    archives = list(tmp_path.glob("tm-*.html"))
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8").endswith("</body>\n</html>\n")
    assert trace.path.exists()
    assert "Trace continued after size rollover" in trace.path.read_text(encoding="utf-8")


def test_html_trace_archive_scan_compresses_all_apps_but_not_active_files(tmp_path):
    active_tm = tmp_path / "tm.html"
    active_dm = tmp_path / "dm.html"
    rotated_tm = tmp_path / "tm-2026-08-17.html"
    rotated_dm = tmp_path / "dm-2026-08-17-1.html"
    unrelated = tmp_path / "report.html"
    active_tm.write_text("active tm", encoding="utf-8")
    active_dm.write_text("active dm", encoding="utf-8")
    rotated_tm.write_text("rotated tm", encoding="utf-8")
    rotated_dm.write_text("rotated dm", encoding="utf-8")
    unrelated.write_text("not a trace", encoding="utf-8")
    trace = HTMLTrace(tmp_path, "tm", "v1", server_name="host")

    trace._compress_rotated_traces()

    assert active_tm.read_text(encoding="utf-8") == "active tm"
    assert active_dm.read_text(encoding="utf-8") == "active dm"
    assert unrelated.read_text(encoding="utf-8") == "not a trace"
    assert not rotated_tm.exists()
    assert not rotated_dm.exists()
    with gzip.open(tmp_path / "tm-2026-08-17.html.gz", "rt", encoding="utf-8") as stream:
        assert stream.read() == "rotated tm"
    with gzip.open(tmp_path / "dm-2026-08-17-1.html.gz", "rt", encoding="utf-8") as stream:
        assert stream.read() == "rotated dm"


def test_html_trace_rotation_does_not_reuse_compressed_archive_name(tmp_path):
    trace = HTMLTrace(tmp_path, "tm", "v1", server_name="host")
    trace.path.write_text("active", encoding="utf-8")
    (tmp_path / "tm-2026-08-17.html.gz").write_bytes(b"existing archive")

    trace._rotate_active_file(datetime(2026, 8, 17, tzinfo=timezone.utc).date())

    assert (tmp_path / "tm-2026-08-17.html.gz").read_bytes() == b"existing archive"
    assert (tmp_path / "tm-2026-08-17-1.html").exists()


def test_html_trace_retention_counts_compressed_archives(tmp_path):
    older = tmp_path / "tm-2026-08-16.html.gz"
    newer = tmp_path / "tm-2026-08-17.html"
    older.write_bytes(b"older")
    newer.write_text("newer", encoding="utf-8")
    older.touch()
    newer.touch()
    trace = HTMLTrace(
        tmp_path,
        "tm",
        "v1",
        server_name="host",
        backup_count=1,
    )

    trace._remove_expired_archives()

    assert not older.exists()
    assert newer.exists()


def test_html_trace_archive_manager_thread_is_optional(tmp_path):
    with mock.patch("util.html_trace.threading.Thread") as thread_class:
        HTMLTrace(tmp_path, "dm", "v1", manage_trace_archives=False)
        thread_class.assert_not_called()

        HTMLTrace(tmp_path, "tm", "v1", manage_trace_archives=True)
        thread_class.assert_called_once()
        assert thread_class.call_args.kwargs["daemon"] is True
        thread_class.return_value.start.assert_called_once_with()


def test_html_trace_compression_failure_restores_original_archive(tmp_path):
    archive = tmp_path / "dm-2026-08-17.html"
    archive.write_text("keep me", encoding="utf-8")
    trace = HTMLTrace(tmp_path, "tm", "v1", server_name="host")

    with mock.patch("util.html_trace.shutil.copyfileobj", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            trace._compress_archive(archive)

    assert archive.read_text(encoding="utf-8") == "keep me"
    assert not list(tmp_path.glob("*.compressing"))
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    ("app_name", "configured", "expected"),
    [
        ("tm", None, True),
        ("TM", None, True),
        ("dig", None, False),
        ("dig", True, True),
        ("tm", False, False),
    ],
)
def test_trace_archive_manager_default_and_override(app_name, configured, expected):
    assert _should_manage_trace_archives(app_name, configured) is expected


@pytest.mark.parametrize(
    ("command_args", "expected"),
    [
        ([], None),
        (["--manage_trace_archives"], True),
        (["--manage_trace_archives", "true"], True),
        (["--manage_trace_archives", "false"], False),
    ],
)
def test_manage_trace_archives_command_line_argument(command_args, expected):
    parser = argparse.ArgumentParser()
    app = App.__new__(App)
    app.stop = lambda: None
    app.add_args(parser)

    args = parser.parse_args(command_args)

    assert args.manage_trace_archives is expected
