import struct
import sys
import types
from unittest import mock

sys.modules.setdefault("util.log", types.ModuleType("util.log"))

with mock.patch("socket.gethostname", return_value="localhost"), mock.patch("socket.gethostbyname", return_value="127.0.0.1"):
    from ipc.tcp_client import _iter_framed_blocks as client_iter_framed_blocks
    from ipc.tcp_server import _iter_framed_blocks as server_iter_framed_blocks


def collect_frames(iter_blocks, payload_size, max_block_size):
    payload = b"x" * payload_size
    frames = list(iter_blocks(payload, max_block_size))

    decoded = [
        (*struct.unpack(">HH", header), len(block))
        for header, block in frames
    ]

    return decoded


def test_framing_marks_last_block_correctly_for_edge_sizes():
    max_block_size = 8
    payload_sizes = [8, 16, 17]

    for iter_blocks in (client_iter_framed_blocks, server_iter_framed_blocks):
        for payload_size in payload_sizes:
            decoded = collect_frames(iter_blocks, payload_size, max_block_size)

            assert decoded[-1][1] == 0
            assert sum(block_len for _, _, block_len in decoded) == payload_size


def test_framing_counts_remaining_blocks_after_current_block():
    max_block_size = 8
    decoded = collect_frames(client_iter_framed_blocks, 17, max_block_size)

    assert decoded == [
        (8, 2, 8),
        (8, 1, 8),
        (1, 0, 1),
    ]
