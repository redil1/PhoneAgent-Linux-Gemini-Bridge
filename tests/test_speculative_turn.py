"""Tests for one-candidate speculative turn orchestration."""

from __future__ import annotations

import asyncio

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext

from phone_agent_gateway.ai_bridge.speculative_turn import SpeculativeTurnCoordinator


class _FakeLLM:
    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.contexts: list[LLMContext] = []

    def start_prefetch(self, context: LLMContext) -> asyncio.Task[str]:
        self.contexts.append(context)

        async def complete() -> str:
            return "A concise answer."

        return asyncio.create_task(complete())

    def cancel_prefetch(self, reason: str) -> None:
        self.cancelled.append(reason)


class _FakeTTS:
    def __init__(self) -> None:
        self.prefetched: list[str] = []
        self.clear_count = 0

    async def prefetch_text(self, text: str) -> None:
        self.prefetched.append(text)

    def clear_prefetch(self) -> None:
        self.clear_count += 1


class _FakePolicy:
    def preview_response(self, text: str) -> str:
        return text


@pytest.mark.asyncio
async def test_coordinator_prefetches_one_context_bound_candidate() -> None:
    context = LLMContext(messages=[{"role": "system", "content": "Be concise."}])
    llm = _FakeLLM()
    tts = _FakeTTS()
    coordinator = SpeculativeTurnCoordinator(
        context=context,
        llm=llm,
        tts=tts,
        policy=_FakePolicy(),  # type: ignore[arg-type]
    )

    await coordinator.consider("Can you help me?")
    assert coordinator._task is not None
    await coordinator._task

    assert llm.contexts[0].get_messages()[-1] == {
        "role": "user",
        "content": "Can you help me?",
    }
    assert tts.prefetched == ["A concise answer."]


@pytest.mark.asyncio
async def test_coordinator_revision_clears_stale_text_and_audio() -> None:
    coordinator = SpeculativeTurnCoordinator(
        context=LLMContext(),
        llm=_FakeLLM(),
        tts=_FakeTTS(),
        policy=_FakePolicy(),  # type: ignore[arg-type]
    )

    await coordinator.consider("First revision")
    await coordinator.consider("Corrected revision")

    assert coordinator._candidate == "Corrected revision"
    assert coordinator._tts.clear_count >= 2
    await coordinator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('utterance', ['Yes, please.', 'Je voudrais les tarifs.', 'No, thank you.'])
async def test_speculation_projects_the_same_live_context_as_commit_without_side_effects(utterance):
    import copy

    from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
    from phone_agent_gateway.ai_bridge.antigravity_gemini_llm import (
        _format_context_prompt,
        _prefetch_prompt_key,
    )

    events = []
    policy = AgentPolicyRuntime(caller_id='unknown:projection', task_id='customer_support', language='en-US', memory_enabled=False, event_sink=events.append)
    context = LLMContext(messages=[{'role':'system','content': policy.system_prompt}])
    policy.attach_context(context)
    policy.note_opening_attempted()
    context.add_message({'role':'assistant','content':'May I help you with your account?'})
    policy._verified_actions.add('sent_message')
    before = copy.deepcopy(context.get_messages())
    before_task = copy.deepcopy(policy.task.summary())
    epoch = policy.turn_epoch
    llm = _FakeLLM()
    coordinator = SpeculativeTurnCoordinator(context=context, llm=llm, tts=_FakeTTS(), policy=policy)
    await coordinator.consider(utterance)
    await coordinator._task
    assert context.get_messages() == before
    assert policy.task.summary() == before_task
    assert policy.turn_epoch == epoch
    assert policy._verified_actions == {'sent_message'}
    assert list(policy.recent_caller_turns) == []
    assert policy.reply_language == 'en-US'
    assert events == []
    predicted = _format_context_prompt(llm.contexts[0])
    await policy.observe_transcription(utterance)
    context.add_message({'role':'user','content':utterance})
    actual = _format_context_prompt(context)
    assert _prefetch_prompt_key(predicted) == _prefetch_prompt_key(actual)
    await coordinator.close()
    await policy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['none', 'transcript', 'catalog', 'playback', 'confidence'])
async def test_projected_candidate_reused_only_for_equivalent_committed_context(change):
    from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
    from phone_agent_gateway.ai_bridge.antigravity_gemini_llm import (
        AntigravityGeminiLLMService,
        _format_context_prompt,
    )
    policy = AgentPolicyRuntime(caller_id='unknown:projection', task_id='customer_support', language='en-US', memory_enabled=False)
    context = LLMContext(messages=[{'role':'system','content':policy.system_prompt}])
    policy.attach_context(context)
    policy.note_opening_attempted()
    context.add_message({'role':'assistant','content':'May I help you with your account?'})
    llm = AntigravityGeminiLLMService()
    llm._session = object()
    async def generate(_):
        return 'How can I help with your account?'
    llm._generate_gemini = generate
    events = []
    llm.set_latency_sink(events.append)
    coordinator = SpeculativeTurnCoordinator(context=context, llm=llm, tts=_FakeTTS(), policy=policy)
    await coordinator.consider('Yes, please.')
    await coordinator._task
    if change == 'playback':
        policy._last_ai_delivery = 'interrupted'
    await policy.observe_transcription('No, thank you.' if change == 'transcript' else 'Yes, please.', trusted_for_task=change != 'confidence')
    context.add_message({'role':'user','content':policy.last_caller_text})
    if change == 'catalog':
        context.add_message({'role':'system','content':'The message tool is disconnected.'})
    result = await llm._consume_prefetch(_format_context_prompt(context))
    assert (result is not None) == (change == 'none')
    assert events[-1]['operation'] == ('prefetch_hit' if change == 'none' else 'prefetch_miss')
    assert 'prompt' not in events[-1]
    await coordinator.close()
    await policy.close()
