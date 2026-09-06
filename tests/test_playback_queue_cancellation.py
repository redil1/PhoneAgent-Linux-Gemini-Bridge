"""Queued responses cancelled by barge-in cannot steal later audio ACKs."""

from __future__ import annotations

import pytest

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [False, True])
async def test_barge_in_retires_every_queued_response_identity(started):
    events = []
    policy = AgentPolicyRuntime(
        caller_id="unknown:queue-test",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        event_sink=events.append,
    )
    first = policy.begin_streamed_response()
    if started:
        await policy.playback_started()
    second = policy.begin_streamed_response()
    await policy.mark_playback_interrupted()
    assert not policy._pending_playback_ids
    if started:
        assert policy._active_playback_id == first
        await policy.playback_stopped(delivered_frames=8)
    else:
        assert policy._active_playback_id is None
    third = policy.begin_streamed_response()
    await policy.playback_started()
    assert policy._active_playback_id == third
    await policy.playback_stopped(delivered_frames=30)
    statuses = [e for e in events if e["type"] == "playback_status"]
    assert any(e["response_id"] == second and e["status"] == "interrupted" for e in statuses)
    assert not any(e["response_id"] == second and e["status"] == "completed" for e in statuses)
    assert any(e["response_id"] == third and e["status"] == "completed" for e in statuses)
    await policy.close()


@pytest.mark.asyncio
async def test_queued_goodbye_is_disarmed_when_the_caller_interrupts():
    events = []
    policy = AgentPolicyRuntime(
        caller_id="unknown:queue-test",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        event_sink=events.append,
    )
    policy.begin_streamed_response()
    await policy.playback_started()
    goodbye = policy.begin_streamed_response()
    policy.arm_terminal_completion(goodbye, "test", None)
    await policy.mark_playback_interrupted()
    assert policy._terminal_response_id is None
    assert any(e["type"] == "terminal_completion_aborted" for e in events)
    await policy.close()
