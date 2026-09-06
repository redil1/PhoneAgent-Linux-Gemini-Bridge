"""Regression coverage for the 2026-09-05 call: incomplete choice and messaging."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_response_universal import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.cascade_tools import (
    CascadeToolRuntime,
    ToolCallProcessor,
    parse_emitted_tool_call,
)
from phone_agent_gateway.ai_bridge.guardrails.permission_gate import PermissionGate
from phone_agent_gateway.ai_bridge.memory.memory_manager import LayeredMemoryManager
from phone_agent_gateway.ai_bridge.openwa_integration import (
    OpenWAConfig,
    OpenWAError,
    OpenWAToolRuntime,
)
from phone_agent_gateway.ai_bridge.production_pipeline import ProductionCallPipeline
from phone_agent_gateway.ai_bridge.tasks.tool_catalog import RealtimeTool
from phone_agent_gateway.ai_bridge.turn_continuity import looks_semantically_incomplete

SEND = "whatsapp_send_text_current_customer"


def test_real_whatsapp_schemas_have_no_model_selected_recipient():
    runtime = OpenWAToolRuntime(OpenWAConfig(), caller_id="unknown:test", task_id="test", call_id="test")
    for schema in runtime._definitions().values():
        properties = schema["parameters"]["properties"]
        assert not {"phone", "phone_number", "chatId", "recipient", "session_id"} & set(properties)


@pytest.mark.asyncio
async def test_missing_caller_metadata_cannot_fall_back_to_model_number():
    runtime = OpenWAToolRuntime(OpenWAConfig(), caller_id="unknown:test", task_id="test", call_id="test")
    runtime.client = object()
    with pytest.raises(OpenWAError, match="current-call recipient"):
        await runtime._current_chat()


@pytest.fixture
def policy(tmp_path):
    return AgentPolicyRuntime(
        caller_id="unknown:offline-action-test", task_id="customer_support", language="en-US",
        memory_enabled=False, memory_manager=LayeredMemoryManager(tmp_path / "memory.json"),
    )


@pytest.mark.parametrize("fragment", ["I prefer", "I prefer.", "I would prefer", "Je préfère"])
def test_missing_preference_is_incomplete(fragment):
    assert looks_semantically_incomplete(fragment)


@pytest.mark.parametrize("complete", ["I prefer movies", "Je préfère les films", "That is what I prefer"])
def test_complete_preference_is_not_blocked(complete):
    assert not looks_semantically_incomplete(complete)


@pytest.mark.asyncio
async def test_incomplete_preference_cannot_become_invented_movies(policy):
    await policy.observe_transcription("I prefer")
    spoken, stop = policy.guard_sentence("Movies and series are a huge part of our setup.", is_first=True)
    assert spoken == "What would you prefer?"
    assert stop
    await policy.close()


@pytest.mark.asyncio
async def test_unavailable_send_promise_fails_evaluation(policy):
    await policy.observe_transcription("Can you send me this offer?")
    response_id = policy.begin_streamed_response()
    spoken, stop = policy.guard_sentence("I can certainly send you the details.", is_first=True)
    assert "can't send" in spoken and stop
    evaluation = await policy.finalize_streamed_response(response_id, spoken)
    assert not evaluation.passed
    await policy.close()


@pytest.mark.asyncio
async def test_verified_send_is_speakable_but_not_delivery_or_booking(policy):
    await policy.observe_transcription("Yes, please.")
    policy.available_tools.add(SEND)
    policy.observe_tool_result(SEND, json.dumps({"accepted": True, "message_id": "mock-1"}), policy.turn_epoch)
    for text in ("I sent the offer to WhatsApp.", "I've sent the offer to WhatsApp."):
        result, violations = PermissionGate.enforce_spoken_response(
            text, language="en", verified_actions=policy._verified_actions,
        )
        assert result == text and not violations
    for text in ("I delivered the offer.", "I booked your appointment."):
        _, violations = PermissionGate.enforce_spoken_response(
            text, language="en", verified_actions=policy._verified_actions,
        )
        assert violations
    spoken, violations = PermissionGate.enforce_spoken_response(
        "Vous devriez recevoir le message sur WhatsApp sous peu.",
        language="fr", verified_actions={"sent"},
    )
    assert spoken == "Dites-moi quand vous recevez le message." and violations
    await policy.observe_transcription("Send another offer")
    assert not policy._verified_actions
    await policy.close()


@pytest.mark.asyncio
async def test_tool_protocol_executes_composed_text_without_recipient_selection(policy):
    policy.available_tools.add(SEND)
    _, _, proposal_id = await policy.finalize_response_with_identity('Would you like me to send the offer on WhatsApp?')
    await policy.playback_started(response_id=proposal_id)
    await policy.playback_stopped(delivered_frames=100)
    await policy.observe_transcription('Yes, please.')
    calls = []

    def send(arguments):
        calls.append(arguments)
        return {"accepted": True, "message_id": "mock-send-1", "delivery_confirmed": False}

    tool = RealtimeTool(name=SEND, definition={
        "type": "function", "name": SEND, "description": "Send the current caller a WhatsApp text",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                       "required": ["text"], "additionalProperties": False},
    }, handler=send)
    runtime = CascadeToolRuntime(policy=policy, caller_id="unknown:test", call_id="offline")
    runtime.catalog = {SEND: tool}
    processor = ToolCallProcessor(runtime, context=LLMContext(), llm=object())
    frames = []

    async def capture(frame, *_):
        frames.append(frame)

    processor.push_frame = capture
    block = '<tool_call>' + json.dumps({"name": SEND, "arguments": {"text": "Advanced plan: $59 for 12 months. https://example.com/advanced"}}) + '</tool_call>'
    for frame in (LLMFullResponseStartFrame(), LLMTextFrame(block), LLMFullResponseEndFrame()):
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert calls == [{"text": "Advanced plan: $59 for 12 months. https://example.com/advanced"}]
    assert policy._verified_actions == {"sent"}
    assert not any(isinstance(f, LLMTextFrame) and "tool_call" in f.text for f in frames)
    await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    assert processor._iterations == 0
    for frame in (LLMFullResponseStartFrame(), LLMTextFrame('<tool_call>' + json.dumps({"name": SEND, "text": "Voici les détails."}) + '</tool_call>'), LLMFullResponseEndFrame()):
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert calls[-1] == {"text": "Voici les détails."}
    with pytest.raises(ValueError, match="undeclared"):
        parse_emitted_tool_call({"name": SEND, "text": "x", "phone": "+15555550123"}, runtime.catalog)
    await policy.close()


@pytest.mark.asyncio
async def test_tool_reload_replaces_prompt_and_invalidates_speculation(policy):
    calls = []

    async def cancel(reason):
        calls.append(reason)

    pipeline = ProductionCallPipeline.__new__(ProductionCallPipeline)
    pipeline.policy = policy
    pipeline.context = LLMContext(messages=[{"role": "system", "content": "initial"}])
    pipeline.speculative_turn = SimpleNamespace(cancel=cancel)
    pipeline.services = SimpleNamespace(llm=object())
    pipeline._native_tools = False
    pipeline._tool_protocol_message = None
    runtime = CascadeToolRuntime(policy=policy, caller_id="unknown:test", call_id="offline")
    pipeline.tools = runtime
    await pipeline._refresh_tool_context()
    tool = RealtimeTool(name=SEND, definition={"name": SEND, "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}, handler=lambda _: {})
    runtime.catalog = {SEND: tool}
    policy.available_tools = {SEND}
    policy.task_contract["allowed_tools"] = [SEND]
    await pipeline._refresh_tool_context()
    assert SEND in pipeline.context.messages[0]["content"]
    assert SEND in pipeline._tool_protocol_message["content"]
    message_count = len(pipeline.context.messages)
    runtime.catalog = {}
    policy.available_tools = set()
    await pipeline._refresh_tool_context()
    assert SEND not in pipeline._tool_protocol_message["content"]
    assert len(pipeline.context.messages) == message_count
    assert calls == ["tool_catalog_changed"] * 3
    await policy.close()
