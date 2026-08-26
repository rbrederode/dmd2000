import errno
import queue
import socket
from datetime import timezone
from unittest import mock

from ipc.tcp_client import TCPClient
from ipc.tcp_server import TCPServer
from util.timer import Timer


class FakeSocket:
    def __init__(self, connect_result=errno.EINPROGRESS, socket_error=0):
        self.connect_result = connect_result
        self.socket_error = socket_error
        self.connect_calls = 0
        self.closed = False

    def connect_ex(self, address):
        self.connect_calls += 1
        return self.connect_result

    def getsockopt(self, level, option):
        assert (level, option) == (socket.SOL_SOCKET, socket.SO_ERROR)
        return self.socket_error

    def fileno(self):
        return -1 if self.closed else 42

    def close(self):
        self.closed = True


def make_client(fake_socket):
    client = TCPClient.__new__(TCPClient)
    client.description = "test"
    client.host = "127.0.0.1"
    client.port = 50002
    client.event_q = queue.Queue()
    client.started = True
    client.connected = False
    client.last_result = -1
    client.client_socket = fake_socket
    client._connect_lock = mock.MagicMock()
    client._process_connection = mock.MagicMock()
    client._destroy_socket = mock.MagicMock()
    client._schedule_retry = mock.MagicMock()
    return client


def test_nonblocking_connect_uses_so_error_instead_of_connecting_twice():
    fake_socket = FakeSocket()
    client = make_client(fake_socket)

    with mock.patch(
        "ipc.tcp_client.select.select",
        return_value=([], [fake_socket], []),
    ):
        result = client.connect()

    assert result == 0
    assert client.connected
    assert fake_socket.connect_calls == 1
    client._process_connection.assert_called_once_with()
    client._destroy_socket.assert_not_called()


def test_failed_connect_destroys_socket_and_schedules_one_retry():
    fake_socket = FakeSocket(socket_error=errno.ECONNREFUSED)
    client = make_client(fake_socket)

    with mock.patch(
        "ipc.tcp_client.select.select",
        return_value=([], [fake_socket], []),
    ):
        result = client.connect()

    assert result == errno.ECONNREFUSED
    assert not client.connected
    assert fake_socket.connect_calls == 1
    client._destroy_socket.assert_called_once_with()
    client._schedule_retry.assert_called_once_with()


def test_stale_disconnect_does_not_close_newer_socket():
    current_socket = FakeSocket(connect_result=0)
    stale_socket = FakeSocket(connect_result=0)
    client = make_client(current_socket)
    client.connected = True

    client._process_disconnect(expected_socket=stale_socket)

    assert client.client_socket is current_socket
    assert client.connected
    client._destroy_socket.assert_not_called()
    client._schedule_retry.assert_not_called()


def test_tcp_connection_events_use_timezone_aware_utc_timestamps():
    client_socket = FakeSocket(connect_result=0)
    client = make_client(client_socket)
    client.sel = mock.MagicMock()
    client._process_connection = TCPClient._process_connection.__get__(client)

    client._process_connection()

    client_event = client.event_q.get_nowait()
    assert client_event.timestamp.tzinfo is timezone.utc

    accepted_socket = mock.MagicMock()
    listening_socket = mock.MagicMock()
    listening_socket.accept.return_value = (accepted_socket, ("127.0.0.1", 50002))
    server = TCPServer.__new__(TCPServer)
    server.description = "test"
    server.sel = mock.MagicMock()
    server.event_q = queue.Queue()

    server._process_connection(listening_socket)

    server_event = server.event_q.get_nowait()
    assert server_event.timestamp.tzinfo is timezone.utc
