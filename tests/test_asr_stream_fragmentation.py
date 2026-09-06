"""Socket timeout boundaries must not corrupt a partially received transcript."""

from __future__ import annotations

import json
import struct

import pytest

from phone_agent_gateway.ai_bridge.antigravity_live_stt import _StreamConn


class Socket:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, _):
        if not self.chunks:
            return b""
        value = self.chunks.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def settimeout(self, _):
        pass


def body(payload):
    envelope = b"\x00" + struct.pack(">I", len(payload)) + payload
    return f"{len(envelope):X}\r\n".encode() + envelope + b"\r\n"


@pytest.mark.parametrize("split", range(1, 55))
def test_every_header_and_payload_split_survives_a_timeout(split):
    payload = json.dumps({"transcription": {"text": "Je ne sais pas.", "isFinal": True}}).encode()
    first = body(payload)
    second = body(b'{"complete":true}')
    sock = Socket(
        [
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
            first[:split],
            TimeoutError(),
            first[split:] + second + b"0\r\n\r\n",
        ]
    )
    conn = _StreamConn(sock)
    assert conn.read_envelope() == (None, None)
    assert conn.read_envelope() == (0, payload)
    assert conn.read_envelope() == (0, b'{"complete":true}')


def test_truncated_payload_is_an_error_instead_of_a_permanent_wait():
    conn = _StreamConn(
        Socket(
            [
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
                b"8\r\n\x00\x00\x00\x00\x10abc\r\n0\r\n\r\n",
            ]
        )
    )
    with pytest.raises(ConnectionError, match="inside an envelope"):
        conn.read_envelope()
    assert conn.eof


def test_clean_transport_eof_is_distinguishable_from_a_timeout():
    conn = _StreamConn(
        Socket(
            [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n", TimeoutError(), b"0\r\n\r\n"]
        )
    )
    assert conn.read_envelope() == (None, None)
    assert not conn.eof
    assert conn.read_envelope() == (None, None)
    assert conn.eof


def test_corrupt_length_cannot_request_an_unbounded_payload():
    conn = _StreamConn(
        Socket(
            [
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
                b"5\r\n\x00" + struct.pack(">I", 2_000_000) + b"\r\n",
            ]
        )
    )
    with pytest.raises(ConnectionError, match="supported size"):
        conn.read_envelope()


@pytest.mark.asyncio
async def test_reader_reports_eof_once_instead_of_polling_forever():
    import asyncio

    from pipecat.frames.frames import ErrorFrame

    from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService

    class ClosedStream:
        eof = True
        reads = 0
        def read_envelope(self, _):
            self.reads += 1
            return None, None
        def close(self):
            pass

    service = AntigravityLiveSTTService(smart_turn_enabled=False)
    stream = ClosedStream()
    service._stream = stream
    frames = []
    async def capture(frame, *_):
        frames.append(frame)
    service.push_frame = capture
    try:
        await asyncio.wait_for(service._stream_reader_loop(), timeout=1)
        assert stream.reads == 1
        assert len([f for f in frames if isinstance(f, ErrorFrame)]) == 1
    finally:
        await service._close_session()
        service._vad._executor.shutdown(wait=False)
