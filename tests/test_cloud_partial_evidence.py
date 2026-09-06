"""Regression evidence from the continuous bilingual/noise ASR matrix."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import TranscriptionFrame

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime, transcription_evidence
from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
from phone_agent_gateway.ai_bridge.turn_continuity import looks_semantically_incomplete


class Vad:
    def set_sample_rate(self, _):
        pass


@pytest.fixture
def stt():
    service = AntigravityLiveSTTService(smart_turn_enabled=False, vad_analyzer=Vad())
    service._speech_epoch = 1
    service._floor_claimed = service._speaking = True
    service._last_speech_at = time.monotonic() - 1.1
    service._last_audio_at = time.monotonic()
    service._received_silence_sec = 1.1
    service._speech_candidate_at = service._last_speech_at
    service.frames = []
    service.push_frame = AsyncMock(side_effect=lambda f, *_: service.frames.append(f))
    service.broadcast_interruption = AsyncMock()
    service._smart_turn_decision = True
    service._smart_turn_decision_epoch = service._speech_epoch
    return service


def finals(stt):
    return [f for f in stt.frames if isinstance(f, TranscriptionFrame)]


@pytest.mark.asyncio
@pytest.mark.parametrize('prefix, complete', [
    ('Actually, I want the six month', 'Actually, I want the six month plan.'),
    ('Yes.', 'Yes, please.'),
    ('I prefer. movies', 'I prefer movies and series.'),
])
async def test_single_partial_cannot_commit_before_next_provider_revision(stt, prefix, complete):
    await stt._handle_provider_transcription(prefix, is_final=False)
    stt._last_transcript_update_at = time.monotonic() - 0.7
    stt._smart_turn_decision_update_at = stt._last_transcript_update_at
    await stt._commit_pending_transcript(source='test')
    assert not finals(stt)
    await stt._handle_provider_transcription(complete, is_final=False)
    await stt._handle_provider_transcription(complete, is_final=False)
    stt._smart_turn_decision_update_at = stt._last_transcript_update_at
    await stt._commit_pending_transcript(source='test')
    assert [f.text for f in finals(stt)] == [complete]
    assert transcription_evidence(finals(stt)[0])[0] is True


@pytest.mark.asyncio
async def test_provider_final_does_not_need_an_extra_duplicate(stt):
    await stt._handle_provider_transcription('Bonjour.', is_final=True)
    stt._smart_turn_decision_update_at = stt._last_transcript_update_at
    await stt._commit_pending_transcript(source='test')
    assert [f.text for f in finals(stt)] == ['Bonjour.']
    assert transcription_evidence(finals(stt)[0])[0] is True


@pytest.mark.asyncio
async def test_new_speech_invalidates_old_partial_confirmations(stt):
    await stt._handle_provider_transcription('The first choice.', is_final=False)
    await stt._handle_provider_transcription('The first choice.', is_final=False)
    stt._speech_epoch += 1
    stt._last_speech_at = time.monotonic() - 0.7
    stt._received_silence_sec = 0.7
    stt._smart_turn_decision_epoch = stt._speech_epoch
    stt._smart_turn_decision_update_at = stt._last_transcript_update_at
    await stt._commit_pending_transcript(source='test')
    assert not finals(stt)


@pytest.mark.asyncio
async def test_unconfirmed_partial_has_bounded_uncertain_fallback(stt):
    await stt._handle_provider_transcription('Yes.', is_final=False)
    stt._last_speech_at = time.monotonic() - 3.2
    stt._received_silence_sec = 3.2
    await stt._commit_pending_transcript(source='test')
    assert len(finals(stt)) == 1
    assert transcription_evidence(finals(stt)[0])[0] is False
    assert finals(stt)[0].result['phone_agent']['uncertainty_reason'] == 'unconfirmed_partial_timeout'


@pytest.mark.asyncio
@pytest.mark.parametrize('partial, final, language', [
    ('Pas la bonne', "à l'abonnement annuel.", 'fr-FR'),
    ('No, not the yearly plan.', 'The yearly plan.', 'en-US'),
    ("I can't accept", 'I can accept.', 'en-US'),
])
async def test_negation_disappearing_from_asr_cannot_become_task_authority(stt, partial, final, language):
    await stt._handle_provider_transcription(partial, is_final=False)
    await stt._handle_provider_transcription(final, is_final=True)
    stt._smart_turn_decision_update_at = stt._last_transcript_update_at
    await stt._commit_pending_transcript(source='test')
    frame = finals(stt)[0]
    trusted, confidence, _ = transcription_evidence(frame)
    assert trusted is False and confidence is None
    assert frame.result['phone_agent']['uncertainty_reason'] == 'provider_revision_removed_negation'
    policy = AgentPolicyRuntime(caller_id='unknown:asr-noise-test', task_id='customer_support', language=language, memory_enabled=False)
    await policy.observe_transcription(frame.text, trusted_for_task=trusted)
    assert policy.last_caller_transcript_trusted is False
    assert policy._last_caller_intent == 'uncertain_audio'
    await policy.close()
    # Evidence from this utterance cannot contaminate a genuinely new positive answer.
    stt._speech_epoch += 1
    await stt._handle_provider_transcription('Yes, please.', is_final=True)
    assert stt._recognition_metadata().get('trusted_for_task', True) is True


@pytest.mark.parametrize('text', ['Could you?', 'Could you please?', 'Est-ce que vous pouvez?', 'Pourriez-vous?', 'Est-ce que tu peux?'])
def test_bare_request_prefix_retains_continuation_patience(text):
    assert looks_semantically_incomplete(text)


@pytest.mark.parametrize('text', ['Could you send the details?', 'Est-ce que vous pouvez envoyer les détails ?', 'Oui, vous pouvez.', 'Yes, I can.'])
def test_complete_requests_and_elliptical_answers_keep_normal_endpointing(text):
    assert not looks_semantically_incomplete(text)


@pytest.mark.asyncio
async def test_no_smart_turn_inference_for_idle_silence(stt):
    stt._floor_claimed = stt._speaking = False
    stt._smart_turn = object()
    stt._recent_pcm.extend(bytes(16000))
    stt._evaluate_smart_turn = AsyncMock()
    await stt._watchdog_tick()
    stt._evaluate_smart_turn.assert_not_awaited()
    assert stt._smart_turn_task is None
