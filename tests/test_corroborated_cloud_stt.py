"""Local corroboration must preserve capture, fail closed and never own the floor."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.frames.frames import TranscriptionFrame

from phone_agent_gateway.ai_bridge.corroborated_cloud_stt import (
    CorroboratedCloudSTTService,
    recognition_key,
)
from phone_agent_gateway.ai_bridge.local_neural_stt import LocalNeuralSTTService
from phone_agent_gateway.ai_bridge.parakeet_local_stt import STTHypothesis


class Vad:
    state = VADState.QUIET
    def set_sample_rate(self, _):
        pass
    async def analyze_audio(self, _):
        return self.state


@pytest.fixture
def stt():
    service = CorroboratedCloudSTTService(smart_turn_enabled=False, vad_analyzer=Vad())
    service._speech_epoch = 1
    service._floor_claimed = service._speaking = True
    service._last_speech_at = time.monotonic()-1
    service._last_audio_at = time.monotonic()
    service._received_silence_sec = 1
    service._last_transcript = 'I want the six month plan.'
    service._provider_final_seen = True
    service.frames = []
    service.push_frame = AsyncMock(side_effect=lambda f, *_: service.frames.append(f))
    service.broadcast_interruption = AsyncMock()
    return service


def local_result(stt, text, *, trusted=True):
    stt._verification_result_key = stt._verification_key()
    stt._verification_result = STTHypothesis(text, confidence=0.9, trusted_for_task=trusted)


@pytest.mark.asyncio
@pytest.mark.parametrize('reference, trusted, expected', [
    ('I want the six-month plan.', True, True),
    ('I do not want the six month plan.', True, False),
    ('I want the twelve month plan.', True, False),
    ('I want the six month plan.', False, False),
    ('', True, False),
])
async def test_corroboration_requires_matching_trusted_local_audio(stt, reference, trusted, expected):
    local_result(stt, reference, trusted=trusted)
    await stt._commit_pending_transcript(source='test')
    frames = [f for f in stt.frames if isinstance(f, TranscriptionFrame)]
    assert len(frames)==1
    assert frames[0].text=='I want the six month plan.'
    assert frames[0].result['phone_agent']['trusted_for_task'] is expected


@pytest.mark.asyncio
async def test_pending_verification_waits_but_deadline_produces_uncertain_turn(stt):
    await stt._commit_pending_transcript(source='test')
    assert not stt.frames
    stt._last_speech_at = time.monotonic()-4
    stt._received_silence_sec = 4
    await stt._commit_pending_transcript(source='test')
    frame = next(f for f in stt.frames if isinstance(f, TranscriptionFrame))
    assert frame.result['phone_agent']['trusted_for_task'] is False
    assert frame.result['phone_agent']['corroboration']['reason']=='unavailable'


@pytest.mark.asyncio
async def test_native_decode_cannot_queue_replacements_while_still_running(stt):
    entered, release = asyncio.Event(), asyncio.Event()
    async def decoder(*_):
        entered.set()
        await release.wait()
        return STTHypothesis('old text')
    stt._decode_for_verification = AsyncMock(side_effect=decoder)
    stt._verification_pcm.extend(bytes(16000))
    stt._verification_speech_end = 16000
    stt._last_speech_at = time.monotonic()-0.4
    stt._received_silence_sec = 0.4
    await stt._watchdog_tick()
    await entered.wait()
    stt._speech_epoch += 1
    stt._last_speech_at = time.monotonic()-0.4
    for _ in range(10):
        await stt._watchdog_tick()
    assert stt._decode_for_verification.await_count==1
    release.set()
    await stt._verification_task
    assert not stt._verification_ready()


@pytest.mark.asyncio
async def test_late_decode_after_resumed_speech_or_transport_gap_is_discarded(stt):
    key = stt._verification_key()
    stt._decode_for_verification = AsyncMock(return_value=STTHypothesis('I want the six month plan.'))
    stt._transport_revision += 1
    await stt._verify(bytes(640), key, 'en')
    assert not stt._verification_ready()
    key = stt._verification_key()
    stt._acoustic_active = True
    await stt._verify(bytes(640), key, 'en')
    assert not stt._verification_ready()


@pytest.mark.asyncio
@pytest.mark.parametrize('local_only', [False, True])
async def test_late_vad_does_not_trim_quiet_initial_refusal(local_only):
    service = (LocalNeuralSTTService(model='test', smart_turn_enabled=False, vad_analyzer=Vad()) if local_only
               else CorroboratedCloudSTTService(smart_turn_enabled=False, vad_analyzer=Vad()))
    service.push_frame = AsyncMock()
    service.broadcast_interruption = AsyncMock()
    prefix = b'\x11\x00'*320
    for index in range(42):  # observed noisy French onset took844ms to claim the floor
        async for _ in service.run_stt(prefix if index==0 else bytes(640)):
            pass
    service._vad.state = VADState.SPEAKING
    async for _ in service.run_stt(bytes(640)):
        pass
    captured = service._local_pcm if local_only else service._verification_pcm
    assert captured.startswith(prefix)
    assert len(captured)==43*640
    service._session_id = None
    await service._close_session()


@pytest.mark.parametrize('cloud, local', [
    ('$39 for six months.', 'Thirty nine dollars for 6 months.'),
    ('Not WhatsApp, send email.', 'Not WhatsApp, send e-mail.'),
    ('Actually, I want the six month plan.', 'I want the six-month plan.'),
    ("I can't accept.", 'I can not accept.'),
    ('En fait, le forfait de six mois.', 'Le forfait de 6 mois.'),
])
def test_comparison_normalizes_only_supported_formatting(cloud, local):
    assert recognition_key(cloud)==recognition_key(local)


@pytest.mark.parametrize('cloud, local', [
    ('I want to renew the plan.', 'I want to cancel the plan.'),
    ('$39', '$59'), ('$39', '39 euros'),
    ("à l'abonnement annuel", "Non, pas l'abonnement annuel"),
    ('My name is John.', 'My name is Joan.'),
])
def test_comparison_does_not_fuzzy_match_critical_words(cloud, local):
    assert recognition_key(cloud)!=recognition_key(local)


@pytest.mark.asyncio
async def test_model_loading_uses_cache_only(monkeypatch, stt):
    import sys
    from types import ModuleType

    import phone_agent_gateway.ai_bridge.corroborated_cloud_stt as module
    from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
    cache = ModuleType('huggingface_hub')
    def cached(repository, *, local_files_only):
        assert local_files_only is True
        assert repository==module.MODEL
        return '/cached/mlx-community-whisper/snapshot'
    cache.snapshot_download = cached
    monkeypatch.setitem(sys.modules,'huggingface_hub',cache)
    loader = AsyncMock()
    monkeypatch.setattr(module,'load_model_async',loader)
    start_cloud = AsyncMock()
    monkeypatch.setattr(AntigravityLiveSTTService,'_start_session',start_cloud)
    await stt._start_session()
    loader.assert_awaited_once_with('/cached/mlx-community-whisper/snapshot')
    start_cloud.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_local_model_is_fatal_before_cloud_session_starts(monkeypatch, stt):
    import sys
    from types import ModuleType

    from pipecat.frames.frames import ErrorFrame

    from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
    cache = ModuleType('huggingface_hub')
    def missing(*_, **__):
        raise FileNotFoundError('No cached model')
    cache.snapshot_download = missing
    monkeypatch.setitem(sys.modules,'huggingface_hub',cache)
    start_cloud = AsyncMock()
    monkeypatch.setattr(AntigravityLiveSTTService,'_start_session',start_cloud)
    with pytest.raises(FileNotFoundError):
        await stt._start_session()
    start_cloud.assert_not_awaited()
    assert any(isinstance(f,ErrorFrame) and f.fatal for f in stt.frames)


@pytest.mark.asyncio
async def test_local_complete_text_gives_cloud_partial_time_to_catch_up(stt):
    stt._last_transcript = 'I want the six month'
    stt._provider_final_seen = False
    local_result(stt, 'I want the six month plan.')
    await stt._commit_pending_transcript(source='test')
    assert not stt.frames
    await stt._handle_provider_transcription('I want the six month plan.', is_final=False)
    await stt._commit_pending_transcript(source='test')
    frame = next(f for f in stt.frames if isinstance(f, TranscriptionFrame))
    assert frame.text=='I want the six month plan.'
    assert frame.result['phone_agent']['trusted_for_task'] is True


@pytest.mark.asyncio
async def test_known_recognition_disagreement_cancels_speculation(stt):
    local_result(stt, 'I do not want the six month plan.')
    stt._last_speculation_text = stt._last_transcript
    stt._speculation_cancel_handler = AsyncMock()
    stt._speculation_candidate_handler = AsyncMock()
    stt._speculative_pipeline_enabled = True
    stt._last_transcript_update_at = time.monotonic()-1
    await stt._watchdog_tick()
    stt._speculation_cancel_handler.assert_awaited_once_with('recognition_uncertain')
    stt._speculation_candidate_handler.assert_not_awaited()
