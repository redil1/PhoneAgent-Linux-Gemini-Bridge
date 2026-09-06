"""Transport gaps must not become plausible but truncated caller requests."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import ErrorFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from phone_agent_gateway.ai_bridge import antigravity_live_stt as module


class Vad:
    state = VADState.QUIET
    def set_sample_rate(self, _):
        pass
    async def analyze_audio(self, _):
        return self.state


class Stream:
    eof = False
    status = 200
    def read_envelope(self, _):
        time.sleep(0.005)
        return None, None
    def close(self):
        self.eof = True


@pytest_asyncio.fixture
async def stt():
    service = module.AntigravityLiveSTTService(smart_turn_enabled=False, vad_analyzer=Vad())
    service._stream = Stream()
    service._session_id = "old"
    service._reconnect_enabled = True
    service.frames = []
    service.push_frame = AsyncMock(side_effect=lambda frame, direction=FrameDirection.DOWNSTREAM: service.frames.append((frame, direction)))
    service.broadcast_interruption = AsyncMock()
    service._speculation_cancel_handler = AsyncMock()
    service._turn_recovery_handler = AsyncMock()
    yield service
    service._session_id = None  # No network teardown in this unit fixture.
    await service._close_session()


async def audio(stt, state=VADState.QUIET):
    stt._vad.state = state
    async for _ in stt.run_stt(bytes(640)):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("fragment, fresh", [("Send the", "No, thank you."), ("Envoyez le", "Non, merci.")])
async def test_gap_discards_partial_and_recovers_only_after_caller_pause(stt, fragment, fresh):
    opened, release = asyncio.Event(), asyncio.Event()
    new_stream = Stream()
    async def reconnect():
        opened.set()
        await release.wait()
        return new_stream, "new"
    stt._open_session_transport = AsyncMock(side_effect=reconnect)
    stt._speech_epoch = 4
    stt._floor_claimed = stt._speaking = stt._acoustic_active = True
    stt._last_transcript = stt._final_transcript = fragment
    stt._last_speculation_text = fragment
    old_stream = stt._stream
    await asyncio.gather(
        stt._fail_transport(old_stream, source="reader"),
        stt._fail_transport(old_stream, source="upload"),
    )
    await opened.wait()
    assert stt._last_transcript == stt._final_transcript == ""
    stt._speculation_cancel_handler.assert_awaited_once_with("stt_transport_failed")
    await audio(stt, VADState.SPEAKING)
    assert stt._audio_buffer == b""
    assert await stt._handle_provider_transcription("the wrong plan", is_final=True) == ""
    await stt._watchdog_tick()
    stt._turn_recovery_handler.assert_not_awaited()
    release.set()
    await stt._reconnect_task
    assert stt._speech_epoch >= 4 and stt._recognition_gap
    assert await stt._handle_provider_transcription("suffix from damaged turn", is_final=True) == ""
    stt._acoustic_active = False
    stt._last_speech_at = time.monotonic() - 4
    stt._last_audio_at = time.monotonic()
    stt._received_silence_sec = 4
    await stt._watchdog_tick()
    await stt._watchdog_tick()
    stt._turn_recovery_handler.assert_awaited_once()
    assert not stt._recognition_gap
    stt._speech_burst_active = False
    await audio(stt, VADState.SPEAKING)
    assert await stt._handle_provider_transcription(fresh, is_final=True) == fresh
    stt._acoustic_active = False
    stt._last_speech_at = time.monotonic() - 4
    stt._last_audio_at = time.monotonic()
    stt._received_silence_sec = 4
    await stt._commit_pending_transcript(source="test")
    assert [f.text for f, _ in stt.frames if isinstance(f, TranscriptionFrame)] == [fresh]
    errors = [(f, d) for f, d in stt.frames if isinstance(f, ErrorFrame)]
    assert len(errors) == 1 and errors[0][1] == FrameDirection.UPSTREAM


@pytest.mark.asyncio
async def test_upload_failure_retires_current_transport_but_late_failure_cannot_retire_new_one(stt, monkeypatch):
    old = stt._stream
    stt._reconnect_enabled = False
    stt._floor_claimed = True
    stt._last_transcript = "I would like"
    def failed(*_, **__):
        raise ConnectionError("controlled upload failure")
    monkeypatch.setattr(module.urllib.request, "urlopen", failed)
    stt._base_url = "https://127.0.0.1:1"
    await stt._send_chunk(bytes(640), 0)
    assert stt._stream is None and stt._session_id is None
    assert stt._last_transcript == "" and stt._recognition_gap
    current = Stream()
    stt._stream, stt._session_id = current, "new"
    await stt._fail_transport(old, source="late reader")
    assert stt._stream is current and stt._session_id == "new"
    assert len([f for f, _ in stt.frames if isinstance(f, ErrorFrame)]) == 1


@pytest.mark.asyncio
async def test_sender_never_submits_old_queue_entry_to_new_session(stt):
    stt._session_id = "new"
    stt._send_chunk = AsyncMock()
    stt._send_queue.put_nowait((b"old speech", 0, "old"))
    stt._send_queue.put_nowait((b"new speech", 0, "new"))
    stt._sender_task = asyncio.create_task(stt._audio_sender_loop())
    await asyncio.wait_for(stt._send_queue.join(), 1)
    stt._send_chunk.assert_awaited_once_with(b"new speech", 0)


@pytest.mark.asyncio
async def test_repeated_ready_then_eof_cannot_create_unlimited_sessions(stt):
    failed = asyncio.Event()
    original_capture = stt.push_frame
    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        await original_capture(frame, direction)
        if isinstance(frame, ErrorFrame) and frame.fatal:
            failed.set()
    stt.push_frame = capture
    async def open_dead():
        stream = Stream()
        stream.eof = True
        return stream, "dead"
    stt._open_session_transport = AsyncMock(side_effect=open_dead)
    await stt._fail_transport(stt._stream, source="reader")
    await asyncio.wait_for(failed.wait(), 1)
    assert stt._open_session_transport.await_count == 3
    assert len([f for f, _ in stt.frames if isinstance(f, ErrorFrame) and f.fatal]) == 1


@pytest.mark.asyncio
async def test_reconnection_has_total_deadline_and_close_cancels_pending_open(stt):
    stt._reconnect_budget_sec = 0.04
    stt._open_session_transport = AsyncMock(side_effect=lambda: None)
    async def hanging():
        await asyncio.Event().wait()
    stt._open_session_transport.side_effect = hanging
    await stt._fail_transport(stt._stream, source="reader")
    await asyncio.wait_for(stt._reconnect_task, 0.5)
    assert any(isinstance(f, ErrorFrame) and f.fatal for f, _ in stt.frames)
    stt.frames.clear()
    stt._stream, stt._session_id = Stream(), "new"
    stt._reconnect_budget_sec = 10
    stt._reconnect_attempts = 0  # A later successfully recognized turn restored availability.
    await stt._fail_transport(stt._stream, source="reader")
    task = stt._reconnect_task
    await asyncio.sleep(0)
    await stt._close_session()
    assert task.done()
    assert not any(isinstance(f, ErrorFrame) and f.fatal for f, _ in stt.frames)


@pytest.mark.asyncio
async def test_cancelled_handshake_closes_socket_that_finishes_later(monkeypatch):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Socket:
        status = 200
        def sendall(self, _):
            pass
        def read_envelope(self, _):
            entered.set()
            release.wait(2)
            return 0, json.dumps({"ready": {"sessionId": "late"}}).encode()
        def close(self):
            closed.set()
    sock = Socket()
    class TLS:
        def wrap_socket(self, socket, **_):
            return socket
    service = module.AntigravityLiveSTTService(smart_turn_enabled=False, vad_analyzer=Vad())
    service._ssl_ctx = TLS()
    monkeypatch.setattr(module.socket, "create_connection", lambda *_, **__: sock)
    monkeypatch.setattr(module, "_StreamConn", lambda socket: socket)
    task = asyncio.create_task(service._open_session_transport())
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        assert await asyncio.to_thread(closed.wait, 1)
        assert service._stream is None
    finally:
        release.set()


@pytest.mark.asyncio
async def test_ready_without_recognition_does_not_reset_total_deadline(stt):
    stt._reconnect_budget_sec = 0.06
    failed = asyncio.Event()
    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        stt.frames.append((frame, direction))
        if isinstance(frame, ErrorFrame) and frame.fatal:
            failed.set()
    stt.push_frame = capture
    async def slow_dead():
        await asyncio.sleep(0.04)
        stream = Stream()
        stream.eof = True
        return stream, "dead"
    stt._open_session_transport = AsyncMock(side_effect=slow_dead)
    started = time.monotonic()
    await stt._fail_transport(stt._stream, source="reader")
    await asyncio.wait_for(failed.wait(), 0.5)
    assert time.monotonic() - started < 0.3
    assert stt._open_session_transport.await_count == 2


@pytest.mark.asyncio
async def test_disconnect_while_publishing_partial_cannot_leak_that_partial(stt):
    stt._speech_epoch = 1
    stt._reconnect_enabled = False
    async def fail_during_floor_claim(**_):
        await stt._fail_transport(stt._stream, source="reader")
    stt._ensure_user_started = fail_during_floor_claim
    assert await stt._handle_provider_transcription("send it", is_final=False) == ""
    assert all(isinstance(f, ErrorFrame) for f, _ in stt.frames)


@pytest.mark.asyncio
async def test_repeat_prompt_waits_until_recognition_is_available(stt):
    stt._reconnect_enabled = False
    stt._speech_epoch = 1
    stt._floor_claimed = stt._speaking = True
    stt._last_speech_at = time.monotonic() - 4
    stt._last_audio_at = time.monotonic()
    stt._received_silence_sec = 4
    await stt._fail_transport(stt._stream, source="reader")
    await stt._watchdog_tick()
    stt._turn_recovery_handler.assert_not_awaited()
    assert stt.caller_owns_floor()
    stt._recognition_unavailable = False
    await stt._watchdog_tick()
    stt._turn_recovery_handler.assert_awaited_once()
