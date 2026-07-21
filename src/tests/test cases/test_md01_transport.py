from types import SimpleNamespace

from dsh.drivers.md01.md01_driver import MD01Driver
from dsh.drivers.md01.md01_msg import MD01Msg


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
