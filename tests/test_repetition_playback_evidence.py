"""Repetition checks distinguish drafts from acknowledged complete speech."""

from __future__ import annotations

import pytest

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime


@pytest.fixture
def runtime():
    return AgentPolicyRuntime(
        caller_id="unknown:repeat-test",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
    )


@pytest.mark.asyncio
async def test_canceled_before_playback_does_not_block_the_answer_on_the_next_turn(runtime):
    sentence = "The six month plan costs thirty nine dollars."
    await runtime.observe_transcription("Tell me the price.")
    first = runtime.begin_streamed_response()
    text, _ = runtime.guard_sentence(sentence, is_first=True, response_id=first)
    assert text == sentence
    await runtime.playback_started()
    await runtime.mark_playback_interrupted()
    await runtime.playback_stopped(delivered_frames=0)
    await runtime.observe_transcription("What is the price of the six month plan?")
    second = runtime.begin_streamed_response()
    text, _ = runtime.guard_sentence(sentence, is_first=True, response_id=second)
    assert text == sentence
    await runtime.close()


@pytest.mark.asyncio
async def test_pending_previous_draft_does_not_block_a_new_response_before_stop_callback(runtime):
    sentence = "The six month plan costs thirty nine dollars."
    first = runtime.begin_streamed_response()
    runtime.guard_sentence(sentence, is_first=True, response_id=first)
    runtime.observe_speech_started()
    second = runtime.begin_streamed_response()
    assert runtime.guard_sentence(sentence, is_first=True, response_id=second)[0] == sentence
    await runtime.close()


@pytest.mark.asyncio
async def test_duplicate_inside_one_draft_and_verified_completed_reply_remain_blocked(runtime):
    sentence = "The six month plan costs thirty nine dollars."
    first = runtime.begin_streamed_response()
    runtime.guard_sentence(sentence, is_first=True, response_id=first)
    assert runtime.guard_sentence(sentence, is_first=False, response_id=first)[0] == ""
    await runtime.playback_started()
    await runtime.playback_stopped(delivered_frames=100)
    second = runtime.begin_streamed_response()
    assert runtime.guard_sentence(sentence, is_first=True, response_id=second)[0] == ""
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["interrupted", "failed", "unverified", "completed"])
async def test_turn_evaluation_history_uses_playback_evidence(runtime, outcome):
    text = "The support team can explain the available options."
    await runtime.finalize_response(text)
    assert list(runtime._completed_ai_turns) == []
    await runtime.playback_started()
    if outcome == "failed":
        await runtime.playback_failed("failed output")
    else:
        if outcome == "interrupted":
            await runtime.mark_playback_interrupted()
        await runtime.playback_stopped(delivered_frames=None if outcome == "unverified" else 100)
    assert list(runtime._completed_ai_turns) == ([text] if outcome == "completed" else [])
    await runtime.close()


@pytest.mark.asyncio
async def test_real_response_processor_can_reoffer_text_never_played(runtime):
    from pipecat.frames.frames import (
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection

    from phone_agent_gateway.ai_bridge.agent_policy import ResponsePolicyProcessor

    processor = ResponsePolicyProcessor(runtime)
    spoken = []

    async def capture(frame, *_):
        if isinstance(frame, LLMTextFrame):
            spoken.append(frame.text.strip())

    processor.push_frame = capture
    sentence = "The six month plan costs thirty nine dollars."

    async def reply():
        for frame in [
            LLMFullResponseStartFrame(),
            LLMTextFrame(sentence),
            LLMFullResponseEndFrame(),
        ]:
            await processor.process_frame(frame, FrameDirection.DOWNSTREAM)

    await runtime.observe_transcription("Tell me the price.")
    await reply()
    await runtime.playback_started()
    await runtime.mark_playback_interrupted()
    await runtime.playback_stopped(delivered_frames=0)
    await runtime.observe_transcription("What is the price of the six month plan?")
    await reply()
    assert spoken == [sentence, sentence]
    await runtime.close()


@pytest.mark.asyncio
async def test_partial_playback_is_not_misclassified_by_discarding_a_reservation(runtime):
    text = "The support team can explain the available options."
    _, _, response_id = await runtime.finalize_response_with_identity(text)
    await runtime.playback_started()
    runtime.discard_pending_playback(response_id)
    assert response_id not in runtime._memory_playback_outcomes
    await runtime.mark_playback_interrupted()
    await runtime.playback_stopped(delivered_frames=20)
    assert runtime._memory_playback_outcomes[response_id] == "interrupted"
    assert list(runtime._completed_ai_turns) == []
    await runtime.close()


@pytest.mark.asyncio
async def test_early_completed_callback_and_late_finalization_promote_history_once(runtime):
    text = "The support team can explain the available options."
    response_id = runtime.begin_streamed_response()
    runtime.guard_sentence(text, is_first=True, response_id=response_id)
    await runtime.playback_started()
    await runtime.playback_stopped(delivered_frames=100)
    await runtime.finalize_streamed_response(response_id, text)
    assert len(runtime._spoken_sentences) == 1
    assert list(runtime._completed_ai_turns) == [text]
    runtime._remember_memory_playback(response_id, "completed")
    assert len(runtime._spoken_sentences) == 1
    assert list(runtime._completed_ai_turns) == [text]
    await runtime.close()
