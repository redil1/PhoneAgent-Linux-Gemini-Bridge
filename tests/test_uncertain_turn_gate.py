"""An explicitly uncertain recognition must not become a fluent guessed answer."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import LLMContextFrame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.production_pipeline import ProductionCallPipeline
from phone_agent_gateway.ai_bridge.repair_processor import (
    UNCERTAIN_AUDIO_CONTEXT,
    ConversationRepairProcessor,
    UncertainTurnGate,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled', [False, True])
async def test_uncertain_words_are_logged_but_not_inserted_as_user_history_facts(enabled):
    policy = AgentPolicyRuntime(caller_id='unknown:unclear', task_id='customer_support', language='fr-FR', memory_enabled=False)
    await policy.observe_transcription("à l'abonnement annuel", trusted_for_task=False)
    processor = ConversationRepairProcessor(policy, enabled=enabled)
    processor.push_frame = AsyncMock()
    original = TranscriptionFrame(text="à l'abonnement annuel", user_id='caller', timestamp='', result={'phone_agent':{'trusted_for_task':False}})
    await processor.process_frame(original, FrameDirection.DOWNSTREAM)
    forwarded = processor.push_frame.call_args.args[0]
    assert forwarded.text==UNCERTAIN_AUDIO_CONTEXT
    assert original.text==policy.last_caller_text=="à l'abonnement annuel"
    assert forwarded.result==original.result
    await policy.close()


@pytest.mark.asyncio
async def test_uncertain_context_triggers_repair_without_entering_model():
    policy = AgentPolicyRuntime(caller_id='unknown:unclear', task_id='customer_support', language='en-US', memory_enabled=False)
    await policy.observe_transcription('annual plan', trusted_for_task=False)
    recover = AsyncMock()
    gate = UncertainTurnGate(policy, recover)
    gate.push_frame = AsyncMock()
    context = LLMContextFrame(LLMContext())
    await gate.process_frame(context, FrameDirection.DOWNSTREAM)
    recover.assert_awaited_once_with(policy.turn_epoch)
    gate.push_frame.assert_not_awaited()
    await policy.observe_transcription('I do not want the annual plan.', trusted_for_task=True)
    await gate.process_frame(context, FrameDirection.DOWNSTREAM)
    gate.push_frame.assert_awaited_once_with(context, FrameDirection.DOWNSTREAM)
    await policy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('language, expected', [('en-US','Sorry'),('fr-FR','Pardon')])
async def test_speech_guard_cannot_release_sales_pitch_on_uncertain_recognition(language, expected):
    policy = AgentPolicyRuntime(caller_id='unknown:unclear', task_id='customer_support', language=language, memory_enabled=False)
    await policy.observe_transcription('annual plan' if language=='en-US' else "à l'abonnement annuel", trusted_for_task=False)
    spoken, stop = policy.guard_sentence('Our annual plan is the best value; I can send it now.', is_first=True)
    assert spoken.startswith(expected) and stop
    assert 'annual' not in spoken
    await policy.close()


@pytest.mark.asyncio
async def test_uncertain_repair_is_once_per_turn_and_stale_turn_cannot_speak():
    owner = ProductionCallPipeline.__new__(ProductionCallPipeline)
    owner.policy = AgentPolicyRuntime(caller_id='unknown:unclear', task_id='customer_support', language='fr-FR', memory_enabled=False)
    await owner.policy.observe_transcription("à l'abonnement annuel", trusted_for_task=False)
    owner.services = SimpleNamespace(stt=SimpleNamespace(caller_owns_floor=lambda: False))
    owner.transport = SimpleNamespace(session=SimpleNamespace(is_active=True))
    owner.response_policy = SimpleNamespace(queue_frame=AsyncMock())
    owner._uncertain_recovery_epochs = set()
    owner._event_sink = None
    epoch = owner.policy.turn_epoch
    await owner._recover_uncertain_transcription(epoch)
    await owner._recover_uncertain_transcription(epoch)
    owner.response_policy.queue_frame.assert_awaited_once()
    assert owner.response_policy.queue_frame.call_args.args[0].text.replace('-', ' ')=='Pardon, pouvez vous répéter ?'
    await owner.policy.observe_transcription('Non, merci.', trusted_for_task=True)
    await owner._recover_uncertain_transcription(epoch)
    assert owner.response_policy.queue_frame.await_count==1
    await owner.policy.close()
