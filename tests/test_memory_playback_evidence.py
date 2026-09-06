"""Generated replies cannot become remembered delivered speech before playback."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.memory.memory_manager import LayeredMemoryManager
from phone_agent_gateway.ai_bridge.memory.memory_writer import ValidatedMemoryWriter


def policy(tmp_path: Path):
    return AgentPolicyRuntime(
        caller_id="+212600000000",
        task_id="customer_support",
        language="en-US",
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome", ["completed", "interrupted", "not_delivered", "failed", "unverified"]
)
async def test_memory_waits_for_playback_and_keeps_the_original_caller(tmp_path, outcome):
    runtime = policy(tmp_path)
    writes = []

    async def write(*args, **kwargs):
        writes.append((args, kwargs))

    runtime.memory_writer.process_turn_async = write
    await runtime.observe_transcription("My name is Omar.")
    await runtime.finalize_response("How can I help today?")
    await asyncio.sleep(0)
    assert writes == []
    await runtime.observe_transcription("Actually, I need something else.")
    await runtime.playback_started()
    if outcome == "failed":
        await runtime.playback_failed("link failure")
    else:
        if outcome == "interrupted":
            await runtime.mark_playback_interrupted()
        await runtime.playback_stopped(
            delivered_frames=0
            if outcome == "not_delivered"
            else None
            if outcome == "unverified"
            else 10
        )
    await asyncio.sleep(0)
    assert len(writes) == 1
    assert writes[0][0][1] == "My name is Omar."
    assert writes[0][1]["delivery_status"] == outcome
    await runtime.close()
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_teardown_preserves_caller_facts_without_inventing_agent_delivery(tmp_path):
    runtime = policy(tmp_path)
    await runtime.observe_transcription("My name is Omar and I prefer English.")
    await runtime.finalize_response("How can I help you?")
    await runtime.playback_disconnected()
    await runtime.close()
    saved = runtime.memory_manager.get_caller_memory("+212600000000")
    assert saved["name"] == "Omar"
    row = saved["episodic_turns"][0]
    assert row["ai"] == ""
    assert row["generated_ai"] == "How can I help you?"
    assert row["delivery_status"] == "interrupted"


@pytest.mark.asyncio
async def test_playback_finishing_before_stream_finalization_is_not_lost(tmp_path):
    runtime = policy(tmp_path)
    writes = []

    async def write(*args, **kwargs):
        writes.append((args, kwargs))

    runtime.memory_writer.process_turn_async = write
    await runtime.observe_transcription("Please explain that.")
    response_id = runtime.begin_streamed_response()
    await runtime.playback_started()
    await runtime.playback_stopped(delivered_frames=20)
    await runtime.finalize_streamed_response(response_id, "Here is an explanation.")
    await runtime.close()
    assert len(writes) == 1
    assert writes[0][1]["delivery_status"] == "completed"


@pytest.mark.parametrize("status", ["completed", "interrupted", "failed", "unverified"])
def test_only_verified_agent_speech_enters_semantic_memory(tmp_path, status):
    manager = LayeredMemoryManager(storage_path=tmp_path / "memory.json")
    submitted = []
    identity = SimpleNamespace(submit_turn=lambda **kwargs: submitted.append(kwargs))
    writer = ValidatedMemoryWriter(manager, identity_memory=identity)
    writer._process_turn(
        "+212600000000", "My name is Omar.", "Here is the answer.", 100, 100, "support", [], status
    )
    assert [e["role"] for e in submitted] == (
        ["caller", "agent"] if status == "completed" else ["caller"]
    )
    row = manager.get_caller_memory("+212600000000")["episodic_turns"][0]
    assert row["ai"] == ("Here is the answer." if status == "completed" else "")


@pytest.mark.asyncio
async def test_out_of_order_playback_and_storage_preserve_caller_correction_order(tmp_path):
    runtime = policy(tmp_path)
    writes = []
    release = asyncio.Event()

    async def write(*args, **kwargs):
        if "French" in args[1]:
            await release.wait()
        writes.append(args[1])

    runtime.memory_writer.process_turn_async = write
    await runtime.observe_transcription("I prefer French.")
    _, _, first = await runtime.finalize_response_with_identity("I will explain the options.")
    await runtime.observe_transcription("Actually, I prefer English.")
    _, _, second = await runtime.finalize_response_with_identity("We can continue in English.")
    runtime._remember_memory_playback(second, "completed")
    await asyncio.sleep(0)
    assert writes == []
    runtime._remember_memory_playback(first, "interrupted")
    await asyncio.sleep(0)
    assert writes == []
    release.set()
    await runtime.close()
    assert writes == ["I prefer French.", "Actually, I prefer English."]
