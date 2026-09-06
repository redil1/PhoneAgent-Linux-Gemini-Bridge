"""Local recognition must obey the same floor and stale-output rules as cloud."""

from __future__ import annotations

import asyncio
import time

import pytest
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import TranscriptionFrame

from phone_agent_gateway.ai_bridge.local_neural_stt import LocalNeuralSTTService
from phone_agent_gateway.ai_bridge.parakeet_local_stt import STTHypothesis


class Vad:
    state = VADState.QUIET

    def set_sample_rate(self, _):
        pass

    async def analyze_audio(self, _):
        return self.state


@pytest.fixture
def local():
    service = LocalNeuralSTTService(
        model="test-model", smart_turn_enabled=False, vad_analyzer=Vad()
    )
    service.frames = []
    service.interruptions = []

    async def capture(frame, *_):
        service.frames.append(frame)

    async def interrupt():
        service.interruptions.append(service._speech_epoch)

    service.push_frame = capture
    service.broadcast_interruption = interrupt
    return service


@pytest.mark.asyncio
async def test_local_acoustic_onset_interrupts_before_decoder_returns(local):
    entered, release = asyncio.Event(), asyncio.Event()

    async def decode(*_):
        entered.set()
        await release.wait()
        return STTHypothesis("old answer")

    local._decode_pcm = decode
    signature = local._signature()
    task = asyncio.create_task(local._decode(bytes(16000), signature))
    await entered.wait()
    local._vad.state = VADState.SPEAKING
    async for _ in local.run_stt(bytes(640)):
        pass
    assert local.caller_owns_floor() and local.interruptions
    release.set()
    await task
    assert not local._last_transcript
    assert not any(isinstance(f, TranscriptionFrame) for f in local.frames)
    assert not local._audio_buffer and not local._session_id


@pytest.mark.asyncio
async def test_local_decode_replaces_full_snapshot_and_preserves_confidence(local):
    local._speech_epoch = 2
    local._floor_claimed = True
    local._last_speech_at = time.monotonic() - 1
    local._last_transcript = local._final_transcript = "Je préfère"

    async def decode(*_):
        return STTHypothesis(
            "Je préfère les films.", confidence=0.4, trusted_for_task=False, language="fr"
        )

    local._decode_pcm = decode
    await local._decode(bytes(16000), local._signature())
    assert local._last_transcript == "Je préfère les films."
    local._last_transcript_update_at = time.monotonic() - 0.2
    await local._commit_pending_transcript(source="test")
    final = next(f for f in local.frames if isinstance(f, TranscriptionFrame))
    assert final.result["phone_agent"]["trusted_for_task"] is False
    assert final.result["phone_agent"]["language"] == "fr"


@pytest.mark.asyncio
async def test_newer_audio_prevents_committing_older_decoded_snapshot(local):
    local._last_transcript = "I prefer movies."
    local._decoded_signature = local._signature()
    local._last_speech_at = time.monotonic()
    await local._commit_pending_transcript(source="test")
    assert not any(isinstance(f, TranscriptionFrame) for f in local.frames)


@pytest.mark.asyncio
async def test_decoder_error_cannot_commit_a_previous_partial_as_the_new_turn(local):
    local._last_transcript = local._final_transcript = "I prefer"

    async def fail(*_):
        raise ValueError("bad decoder")

    local._decode_pcm = fail
    await local._decode(bytes(16000), local._signature())
    assert local._last_transcript == ""
    await local._commit_pending_transcript(source="test")
    assert not any(isinstance(f, TranscriptionFrame) for f in local.frames)


@pytest.mark.asyncio
async def test_local_start_loads_on_decoder_executor_without_discovering_cloud(local, monkeypatch):
    from pipecat.frames.frames import StartFrame

    from phone_agent_gateway.ai_bridge import local_neural_stt
    loaded = []
    async def load(model):
        loaded.append(model)
    def forbidden():
        raise AssertionError('Local STT must not discover a cloud ASR bridge')
    monkeypatch.setattr(local_neural_stt, 'load_model_async', load)
    local._discover_bridge = forbidden
    await local.start(StartFrame())
    assert loaded == ['test-model']
    assert local._watchdog_task is not None
    assert local._session_id is None
    await local._close_session()
    assert local._watchdog_task is None


@pytest.mark.asyncio
async def test_local_decoder_does_not_start_in_tiny_interword_pause(local):
    local._floor_claimed = True
    local._local_pcm.extend(bytes(16000))
    local._local_speech_end = 12000
    local._last_speech_at = time.monotonic() - 0.16
    await local._watchdog_tick()
    assert local._decode_task is None


@pytest.mark.asyncio
async def test_decoder_has_a_deadline_and_does_not_leave_an_old_partial(local):
    from pipecat.frames.frames import ErrorFrame
    local._decode_timeout_secs = .02
    local._last_transcript = 'Old partial'
    async def never_returns(*_):
        await asyncio.Event().wait()
    local._decode_pcm = never_returns
    await asyncio.wait_for(local._decode(bytes(16000), local._signature()), timeout=1)
    assert not local._last_transcript
    assert any(isinstance(f, ErrorFrame) and 'TimeoutError' in f.error for f in local.frames)
    assert local.turn_stop_watchdog_timeout_secs > 2 * local._decode_timeout_secs + local._incomplete_endpoint_sec
