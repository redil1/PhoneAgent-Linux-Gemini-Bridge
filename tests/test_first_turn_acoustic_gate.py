"""Tests for First-Turn Acoustic Gate in ProductionCallPipeline."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from pipecat.frames.frames import Frame, TTSSpeakFrame

from phone_agent_gateway.ai_bridge.production_pipeline import (
    ProductionCallPipeline,
    ProviderServices,
)
from phone_agent_gateway.ai_bridge.pipecat_transport import (
    PhoneAgentTransport,
    PhoneAgentTransportParams,
)
from phone_agent_gateway.ai_bridge.runtime_config import RuntimeConfig
from phone_agent_gateway.ai_bridge.session import SessionPhase


def _create_mock_pipeline():
    transport = PhoneAgentTransport(
        PhoneAgentTransportParams(
            audio_in_sample_rate=16_000,
            audio_out_sample_rate=16_000,
        )
    )
    transport.session.set_phase(SessionPhase.CONNECTING)
    transport.session.set_phase(SessionPhase.ACTIVE)

    mock_stt = MagicMock()
    mock_llm = MagicMock()
    mock_tts = MagicMock()

    services = ProviderServices(stt=mock_stt, llm=mock_llm, tts=mock_tts)
    config = RuntimeConfig.from_env()

    pipeline = ProductionCallPipeline(
        transport=transport,
        config=config,
        services=services,
        caller_id="+212660193275",
        call_direction="outbound",
    )
    return pipeline


@pytest.mark.asyncio
async def test_acoustic_gate_suppresses_greeting_when_caller_speaks_first() -> None:
    pipeline = _create_mock_pipeline()
    pipeline._runner_task = asyncio.create_task(asyncio.sleep(10))

    queued_frames: list[Frame] = []

    async def fake_queue_frame(frame, direction):
        queued_frames.append(frame)

    pipeline.response_policy.queue_frame = fake_queue_frame

    # Simulate caller speaking ("Allo?") during the acoustic gate window
    async def simulate_caller_speaking():
        await asyncio.sleep(0.05)
        pipeline.policy.observe_speech_started()

    speech_task = asyncio.create_task(simulate_caller_speaking())

    try:
        # Run greet with a 0.2s acoustic gate window
        await pipeline.greet(initial_silence_wait_secs=0.2)
        await speech_task

        # Since caller spoke first, the canned greeting MUST NOT be queued!
        assert len(queued_frames) == 0
    finally:
        pipeline._runner_task.cancel()
        await asyncio.gather(pipeline._runner_task, return_exceptions=True)
        await pipeline.policy.close()


@pytest.mark.asyncio
async def test_acoustic_gate_delivers_greeting_when_caller_is_silent() -> None:
    pipeline = _create_mock_pipeline()
    pipeline._runner_task = asyncio.create_task(asyncio.sleep(10))

    queued_frames: list[Frame] = []

    async def fake_queue_frame(frame, direction):
        queued_frames.append(frame)

    pipeline.response_policy.queue_frame = fake_queue_frame

    try:
        # Run greet with silence
        await pipeline.greet(initial_silence_wait_secs=0.05)

        # Since caller remained silent, the canned greeting MUST be queued
        assert len(queued_frames) == 1
        assert isinstance(queued_frames[0], TTSSpeakFrame)
        assert len(queued_frames[0].text) > 0
    finally:
        pipeline._runner_task.cancel()
        await asyncio.gather(pipeline._runner_task, return_exceptions=True)
        await pipeline.policy.close()
