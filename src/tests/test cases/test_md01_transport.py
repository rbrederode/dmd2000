from types import SimpleNamespace
import socket

import pytest

from dsh.drivers.md01.md01_driver import MD01Driver
from dsh.drivers.md01.md01_msg import MD01Msg
from util.xbase import XTimeoutWaitingForResponse


MD01_RESPONSE = bytes.fromhex("57030600000a030600000a20")


class FakeSocket:
    def __init__(self, response_chunks):
        self.response_chunks = list(response_chunks)
        self.connected_to = None
        self.sent = None
        self.recv_sizes = []
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        self.connected_to = address

    def sendall(self, data):
        self.sent = data

    def recv(self, size):
        self.recv_sizes.append(size)
        if not self.response_chunks:
            return b""

        chunk = self.response_chunks.pop(0)
        result = chunk[:size]
        remainder = chunk[size:]
        if remainder:
            self.response_chunks.insert(0, remainder)
        return result

    def close(self):
        self.closed = True


def make_driver():
    driver = MD01Driver.__new__(MD01Driver)
    driver.md01_config = SimpleNamespace(host="192.0.2.1", port=23)
    driver.last_command_time = 0
    driver._last_set_command_data = None
    driver._rate_limit_wait = lambda command: None
    return driver


def test_set_command_consumes_position_response(monkeypatch):
    fake_socket = FakeSocket([MD01_RESPONSE])
    monkeypatch.setattr(
        "dsh.drivers.md01.md01_driver.socket.socket",
        lambda *args, **kwargs: fake_socket,
    )

    command = MD01Msg()
    command.set_position(80.0, 10.0)
    command.set_cmd(MD01Msg.CMD_SET)

    response = make_driver()._send_md01_command(command)

    assert response.alt == 0.0
    assert response.az == 0.0
    assert fake_socket.sent == command.to_data()
    assert fake_socket.recv_sizes == [12]
    assert fake_socket.closed


def test_md01_response_can_arrive_in_multiple_tcp_reads(monkeypatch):
    fake_socket = FakeSocket([MD01_RESPONSE[:5], MD01_RESPONSE[5:]])
    monkeypatch.setattr(
        "dsh.drivers.md01.md01_driver.socket.socket",
        lambda *args, **kwargs: fake_socket,
    )

    command = MD01Msg()
    command.set_cmd(MD01Msg.CMD_STATUS)

    response = make_driver()._send_md01_command(command)

    assert response.alt == 0.0
    assert response.az == 0.0
    assert fake_socket.recv_sizes == [12, 7]
    assert fake_socket.closed


def test_md01_response_discards_adapter_command_prefix(monkeypatch, caplog):
    fake_socket = FakeSocket([b"AT+ENTM\r" + MD01_RESPONSE])
    monkeypatch.setattr(
        "dsh.drivers.md01.md01_driver.socket.socket",
        lambda *args, **kwargs: fake_socket,
    )

    command = MD01Msg()
    command.set_cmd(MD01Msg.CMD_STATUS)

    response = make_driver()._send_md01_command(command)

    assert response.alt == 0.0
    assert response.az == 0.0
    assert fake_socket.recv_sizes == [12, 8]
    assert "discarded 8 unexpected byte(s)" in caplog.text
    assert "AT+ENTM" in caplog.text
    assert fake_socket.closed


def test_md01_response_resynchronizes_across_fragmented_adapter_data(monkeypatch):
    fake_socket = FakeSocket(
        [b"AT+", b"ENTM\rAT+ENTM\r", MD01_RESPONSE[:4], MD01_RESPONSE[4:]]
    )
    monkeypatch.setattr(
        "dsh.drivers.md01.md01_driver.socket.socket",
        lambda *args, **kwargs: fake_socket,
    )

    command = MD01Msg()
    command.set_cmd(MD01Msg.CMD_STATUS)

    response = make_driver()._send_md01_command(command)

    assert response.alt == 0.0
    assert response.az == 0.0
    assert fake_socket.closed


def test_md01_response_reports_adapter_data_without_valid_frame(monkeypatch):
    fake_sockets = [FakeSocket([b"AT+ENTM\r"]), FakeSocket([b"AT+ENTM\r"])]
    sockets = iter(fake_sockets)
    monkeypatch.setattr(
        "dsh.drivers.md01.md01_driver.socket.socket",
        lambda *args, **kwargs: next(sockets),
    )

    command = MD01Msg()
    command.set_cmd(MD01Msg.CMD_STATUS)

    with pytest.raises(
        XTimeoutWaitingForResponse, match="did not receive a valid"
    ) as exc_info:
        make_driver()._send_md01_command(command)

    assert "AT+ENTM" in str(exc_info.value)
    assert all(fake_socket.closed for fake_socket in fake_sockets)


def test_md01_socket_timeout_is_reported_as_domain_timeout(monkeypatch):
    fake_socket = FakeSocket([])

    def raise_timeout(size):
        fake_socket.recv_sizes.append(size)
        raise socket.timeout("timed out")

    fake_socket.recv = raise_timeout
    monkeypatch.setattr(
        "dsh.drivers.md01.md01_driver.socket.socket",
        lambda *args, **kwargs: fake_socket,
    )

    command = MD01Msg()
    command.set_cmd(MD01Msg.CMD_STOP)

    with pytest.raises(
        XTimeoutWaitingForResponse, match="timed-out waiting for rsp"
    ) as exc_info:
        make_driver()._send_md01_command(command)

    assert isinstance(exc_info.value.__cause__, socket.timeout)
    assert fake_socket.closed


def test_md01_command_retries_once_after_response_timeout(monkeypatch, caplog):
    timed_out_socket = FakeSocket([])

    def raise_timeout(size):
        timed_out_socket.recv_sizes.append(size)
        raise socket.timeout("timed out")

    timed_out_socket.recv = raise_timeout
    successful_socket = FakeSocket([MD01_RESPONSE])
    sockets = iter([timed_out_socket, successful_socket])
    monkeypatch.setattr(
        "dsh.drivers.md01.md01_driver.socket.socket",
        lambda *args, **kwargs: next(sockets),
    )

    command = MD01Msg()
    command.set_cmd(MD01Msg.CMD_STATUS)

    response = make_driver()._send_md01_command(command)

    assert response.alt == 0.0
    assert response.az == 0.0
    assert timed_out_socket.closed
    assert successful_socket.closed
    assert "retrying" in caplog.text


def test_md01_suppresses_duplicate_quantised_set_commands():
    driver = make_driver()
    driver.md01_config.offset_alt = 0.0
    driver.md01_config.offset_az = 0.0
    driver.md01_config.min_alt = 0.0
    driver.md01_config.max_alt = 90.0
    sent_commands = []

    def send(command):
        sent_commands.append(command.to_data())
        return MD01Msg()

    driver._send_md01_command = send

    driver._set_md01_altaz(40.927, 212.707)
    driver._set_md01_altaz(40.923, 212.718)
    driver._set_md01_altaz(40.923, 212.818)

    assert len(sent_commands) == 2
    assert sent_commands[0] != sent_commands[1]


def test_md01_stop_allows_same_set_command_to_be_sent_again():
    driver = make_driver()
    driver.md01_config.offset_alt = 0.0
    driver.md01_config.offset_az = 0.0
    driver.md01_config.min_alt = 0.0
    driver.md01_config.max_alt = 90.0
    sent_commands = []

    def send(command):
        sent_commands.append(command.to_data())
        return MD01Msg()

    driver._send_md01_command = send

    driver._set_md01_altaz(40.9, 212.7)
    driver._stop_md01()
    driver._set_md01_altaz(40.9, 212.7)

    assert [packet[-2:-1] for packet in sent_commands] == [
        MD01Msg.CMD_SET,
        MD01Msg.CMD_STOP,
        MD01Msg.CMD_SET,
    ]
