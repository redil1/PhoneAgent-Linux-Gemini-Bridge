"""Deterministic caller-continuation and speech-release regressions.

VAD/model outputs are injected; no provider, phone, operator configuration,
or invented accuracy score is involved.
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService


class Vad:
    state = VADState.QUIET

    def set_sample_rate(self, rate):
        self.rate = rate

    async def analyze_audio(self, audio):
        return self.state


@pytest.fixture
def endpoint():
    service = AntigravityLiveSTTService(smart_turn_enabled=False, vad_analyzer=Vad())
    service.frames = []
    service.interruptions = []

    async def capture(frame, *_):
        service.frames.append(frame)

    async def interrupt():
        service.interruptions.append(service._speech_epoch)

    service.push_frame = capture
    service.broadcast_interruption = interrupt
    return service


def pending(service, text="I would like the family plan.", silence=1.0):
    service._stage_transcription(text, is_final=True)
    service._last_speech_at = time.monotonic() - silence
    service._last_transcript_update_at = time.monotonic() - 0.3


async def audio(service):
    async for _ in service.run_stt(bytes(640)):
        pass


@pytest.mark.asyncio
async def test_acoustic_onset_cancels_pending_reply_before_asr(endpoint):
    endpoint._vad.state = VADState.SPEAKING
    cancelled = []
    endpoint.set_speculation_handlers(None, cancelled.append)
    await audio(endpoint)
    assert endpoint.caller_owns_floor()
    assert len(endpoint.interruptions) == 1
    assert any(isinstance(f, UserStartedSpeakingFrame) for f in endpoint.frames)
    assert cancelled == ["speech_resumed"]
    await audio(endpoint)
    assert len(endpoint.interruptions) == 1


@pytest.mark.asyncio
async def test_speech_resume_inside_awaited_prefetch_cannot_commit(endpoint):
    endpoint._speculative_pipeline_enabled = True
    pending(endpoint, silence=1.2)

    async def candidate(_):
        endpoint._track_speech_energy(-20)
        await asyncio.sleep(0)

    endpoint.set_speculation_handlers(candidate, None)
    await endpoint._watchdog_tick()
    assert not any(isinstance(f, TranscriptionFrame) for f in endpoint.frames)
    assert endpoint._last_transcript == "I would like the family plan."


@pytest.mark.asyncio
async def test_output_start_cannot_take_floor_from_resumed_caller(endpoint):
    pending(endpoint)
    await endpoint._ensure_user_started(force=True)
    initial = len(endpoint.interruptions)
    await endpoint.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    assert len(endpoint.interruptions) == initial + 1


@pytest.mark.asyncio
async def test_same_audio_revision_is_not_published_as_orphan_suffix(endpoint):
    pending(endpoint, "Please change my address")
    await endpoint._commit_pending_transcript(source="test")
    assert endpoint._stage_transcription("Please change my address to London", is_final=True) == ""
    assert endpoint._last_transcript == ""


@pytest.mark.asyncio
async def test_repeated_short_answer_after_new_speech_is_a_real_turn(endpoint):
    pending(endpoint, "Yes, please.")
    await endpoint._commit_pending_transcript(source="test")
    endpoint._speech_epoch += 1
    assert endpoint._stage_transcription("Yes, please.", is_final=True) == "Yes, please."


@pytest.mark.asyncio
async def test_media_gap_cannot_count_as_silence(endpoint):
    pending(endpoint, silence=5)
    endpoint._last_audio_at = time.monotonic() - 2
    endpoint._received_silence_sec = 5
    await endpoint._watchdog_tick()
    assert not any(isinstance(f, TranscriptionFrame) for f in endpoint.frames)


@pytest.mark.asyncio
async def test_decoder_inflight_prevents_completion(endpoint):
    pending(endpoint, silence=5)
    endpoint._audio_inflight = True
    await endpoint._commit_pending_transcript(source="stream_complete")
    assert not any(isinstance(f, TranscriptionFrame) for f in endpoint.frames)


@pytest.mark.asyncio
async def test_only_captured_audio_is_sent_in_original_order(endpoint):
    endpoint._session_id = "offline-test"
    endpoint._sender_task = object()  # queue only; no network sender exists
    tail = b"\x01\x02" * 320
    endpoint._audio_buffer.extend(tail + bytes(3840))
    pending(endpoint, silence=0.3)
    await endpoint._watchdog_tick()
    assert endpoint._send_queue.empty()
    await audio(endpoint)
    payload, sequence, session_id = endpoint._send_queue.get_nowait()
    assert session_id == "offline-test"
    assert sequence == 0 and payload.startswith(tail)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["Yes", "Oui", "I would like", "Je voudrais"])
async def test_short_and_incomplete_phrases_receive_acoustic_analysis(endpoint, text):
    endpoint._smart_turn = object()
    endpoint._recent_pcm.extend(bytes(16000))
    pending(endpoint, text, silence=0.25)
    analyzed = []

    async def analyze(pcm, epoch, snapshot):
        analyzed.append((len(pcm), epoch))
        endpoint._smart_turn_pending = False

    endpoint._evaluate_smart_turn = analyze
    await endpoint._watchdog_tick()
    await endpoint._smart_turn_task
    assert analyzed
    assert not any(isinstance(f, TranscriptionFrame) for f in endpoint.frames)


@pytest.mark.asyncio
async def test_incomplete_prosody_retains_full_thought_until_continuation(endpoint):
    pending(endpoint, "Je voudrais changer mon abonnement.", silence=1.5)
    endpoint._smart_turn_incomplete = True
    await endpoint._watchdog_tick()
    assert not any(isinstance(f, TranscriptionFrame) for f in endpoint.frames)
    endpoint._track_speech_energy(-20)
    endpoint._stage_transcription("parce que je déménage.", is_final=True)
    assert endpoint._last_transcript == "Je voudrais changer mon abonnement. parce que je déménage."


@pytest.mark.asyncio
async def test_smart_turn_failure_is_unknown_not_complete(endpoint):
    class BrokenModel:
        def _predict_endpoint(self, _):
            raise RuntimeError("offline model failure")

    endpoint._smart_turn = BrokenModel()
    assert endpoint._run_smart_turn_inference(bytes(16000))["probability"] is None
    pending(endpoint)
    endpoint._smart_turn_executor = ThreadPoolExecutor(max_workers=1)
    await endpoint._evaluate_smart_turn(bytes(16000), endpoint._speech_epoch)
    assert endpoint._smart_turn_decision is None
    assert endpoint._required_silence() == 0.9
    await endpoint._close_session()


def test_speculation_never_shortens_authoritative_turn_deadline(endpoint):
    pending(endpoint)
    normal = endpoint._required_silence()
    endpoint._speculative_pipeline_enabled = True
    endpoint._speculative_fast_endpoint_sec = 0.05
    assert endpoint._required_silence() == normal


@pytest.mark.asyncio
async def test_received_silence_releases_empty_false_start(endpoint):
    endpoint._floor_claimed = endpoint._speaking = True
    endpoint._last_speech_at = time.monotonic() - 4
    endpoint._last_audio_at = time.monotonic()
    endpoint._received_silence_sec = 4
    await endpoint._watchdog_tick()
    assert not endpoint.caller_owns_floor()


@pytest.mark.asyncio
async def test_received_pause_and_current_complete_verdict_commit_once(endpoint):
    pending(endpoint, silence=0.7)
    endpoint._smart_turn_decision = True
    endpoint._smart_turn_decision_epoch = endpoint._speech_epoch
    endpoint._smart_turn_decision_update_at = endpoint._last_transcript_update_at
    await endpoint._watchdog_tick()
    await endpoint._watchdog_tick()
    assert sum(isinstance(f, TranscriptionFrame) for f in endpoint.frames) == 1
    assert not endpoint.caller_owns_floor()


@pytest.mark.asyncio
async def test_commit_preparation_cannot_lose_prefix_when_caller_resumes(endpoint):
    pending(endpoint, "Please change my address", silence=1.1)

    async def resume_during_interrupt():
        endpoint._track_speech_energy(-20)
        await asyncio.sleep(0)

    endpoint.broadcast_interruption = resume_during_interrupt
    await endpoint._commit_pending_transcript(source="test")
    assert endpoint._last_transcript == "Please change my address"
    assert endpoint._last_committed_text == ""


@pytest.mark.asyncio
async def test_tentative_sound_does_not_take_floor_or_cancel_audio(endpoint):
    endpoint._vad.state = VADState.STARTING
    await audio(endpoint)
    assert endpoint._silence_elapsed() == 0
    assert not endpoint.caller_owns_floor()
    assert not endpoint.interruptions


@pytest.mark.asyncio
async def test_backlogged_recognition_input_prevents_commit(endpoint):
    pending(endpoint, silence=5)
    endpoint._send_queue.put_nowait((bytes(640), 0, "offline-test"))
    await endpoint._commit_pending_transcript(source="test")
    assert not any(isinstance(f, TranscriptionFrame) for f in endpoint.frames)


@pytest.mark.asyncio
async def test_short_resumption_cancels_prefetch_without_new_speech_epoch(endpoint):
    endpoint._vad.state = VADState.SPEAKING
    await audio(endpoint)
    epoch = endpoint._speech_epoch
    cancelled = []
    endpoint.set_speculation_handlers(None, cancelled.append)
    endpoint._last_speculation_text = "old candidate"
    await audio(endpoint)
    assert endpoint._speech_epoch == epoch
    assert cancelled == ["speech_resumed"]


@pytest.mark.asyncio
async def test_stale_model_audio_cannot_be_tagged_with_newer_revision(endpoint):
    class CompleteModel:
        def _predict_endpoint(self, _):
            return {"probability": 0.99}

    pending(endpoint)
    old_snapshot = (endpoint._turn_revision, endpoint._last_transcript_update_at, endpoint._last_speech_at)
    endpoint._turn_revision += 1
    endpoint._smart_turn = CompleteModel()
    endpoint._smart_turn_executor = ThreadPoolExecutor(max_workers=1)
    await endpoint._evaluate_smart_turn(bytes(16000), endpoint._speech_epoch, old_snapshot)
    assert endpoint._smart_turn_decision is None
    await endpoint._close_session()


@pytest.mark.asyncio
async def test_partial_tail_arriving_at_provider_cadence_is_not_cut_off(endpoint):
    pending(endpoint, 'Je ne sais', silence=0.75)
    endpoint._provider_final_seen = False
    endpoint._last_transcript_update_at = time.monotonic() - 0.2
    endpoint._smart_turn_decision = True
    endpoint._smart_turn_decision_epoch = endpoint._speech_epoch
    endpoint._smart_turn_decision_update_at = endpoint._last_transcript_update_at
    await endpoint._watchdog_tick()
    assert not any(isinstance(f, TranscriptionFrame) for f in endpoint.frames)
    endpoint._stage_transcription('Je ne sais pas.', is_final=False)
    assert endpoint._last_transcript == 'Je ne sais pas.'


@pytest.mark.asyncio
async def test_old_final_after_new_speech_cannot_become_the_new_turn_prefix(endpoint):
    pending(endpoint, 'Yes, please.', silence=1.2)
    endpoint._provider_final_seen = False
    await endpoint._commit_pending_transcript(source='test')
    endpoint._speech_epoch += 1
    assert endpoint._stage_transcription('Yes, please.', is_final=True) == ''
    assert endpoint._stage_transcription('Yes, I can.', is_final=False) == 'Yes, I can.'


@pytest.mark.asyncio
async def test_late_revision_updates_retired_watermark_without_publishing_an_orphan(endpoint):
    pending(endpoint, 'Where are you calling', silence=1.2)
    endpoint._provider_final_seen = False
    await endpoint._commit_pending_transcript(source='test')
    assert endpoint._stage_transcription('Where are you calling from?', is_final=False) == ''
    endpoint._speech_epoch += 1
    assert endpoint._stage_transcription('Where are you calling from?I prefer movies.', is_final=False) == 'I prefer movies.'


@pytest.mark.asyncio
async def test_identical_final_answer_is_preserved_after_a_settled_provider_segment(endpoint):
    pending(endpoint, 'Oui, merci.', silence=1.2)
    await endpoint._commit_pending_transcript(source='test')
    endpoint._speech_epoch += 1
    assert endpoint._stage_transcription('Oui, merci.', is_final=True) == 'Oui, merci.'


@pytest.mark.asyncio
async def test_recognizer_text_over_received_silence_cannot_create_a_caller_turn(endpoint):
    await audio(endpoint)
    assert await endpoint._handle_provider_transcription('Yes, please.',is_final=True)==''
    await endpoint._watchdog_tick()
    assert not endpoint.caller_owns_floor()
    assert not endpoint.interruptions
    assert not any(isinstance(f,TranscriptionFrame) for f in endpoint.frames)


@pytest.mark.asyncio
async def test_late_text_cannot_revive_an_empty_turn_after_recovery(endpoint):
    endpoint._vad.state=VADState.SPEAKING
    await audio(endpoint)
    endpoint._acoustic_active=False
    endpoint._last_speech_at=time.monotonic()-4
    endpoint._last_audio_at=time.monotonic()
    endpoint._received_silence_sec=4
    await endpoint._watchdog_tick()
    assert not endpoint.caller_owns_floor()
    assert await endpoint._handle_provider_transcription('Yes, please.',is_final=True)==''
    endpoint._speech_burst_active=False
    endpoint._vad.state=VADState.SPEAKING
    await audio(endpoint)
    assert await endpoint._handle_provider_transcription('Yes, please.',is_final=True)=='Yes, please.'
