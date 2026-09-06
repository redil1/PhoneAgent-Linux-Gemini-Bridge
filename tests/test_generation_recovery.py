"""Real queue tests for bounded, generation-bound speech after model failure."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime, ResponsePolicyProcessor
from phone_agent_gateway.ai_bridge.generation_recovery import bind_generation_failures
from phone_agent_gateway.ai_bridge.production_pipeline import ProductionCallPipeline


@pytest.mark.asyncio
@pytest.mark.parametrize("stale", [False, True])
async def test_model_failure_discards_unfinished_and_late_text_and_repairs_only_current_turn(stale):
    events = []
    policy = AgentPolicyRuntime(
        caller_id="unknown:failure-test",
        task_id="customer_support",
        language="fr-FR",
        memory_enabled=False,
        event_sink=events.append,
    )
    await policy.observe_transcription("Pouvez-vous expliquer cela ?")
    response = ResponsePolicyProcessor(policy)
    owner = ProductionCallPipeline.__new__(ProductionCallPipeline)
    owner.policy = policy
    owner.response_policy = response
    owner.services = SimpleNamespace(stt=SimpleNamespace(caller_owns_floor=lambda: False))
    owner.transport = SimpleNamespace(session=SimpleNamespace(is_active=True))
    owner._generation_recovery_epochs = set()
    owner._event_sink = events.append
    response.bind_generation_recovery(owner._recover_generation_failure)
    finished = asyncio.Event()
    received = []

    class Pass(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class FailingModel(Pass):
        async def process_frame(self, frame, direction):
            if isinstance(frame, LLMContextFrame):
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(LLMTextFrame("Une réponse inachevée"))
                if stale:
                    policy.observe_speech_started()
                await self.push_error("model unavailable")
                await self.push_error("duplicate error")
                await self.push_frame(LLMTextFrame(" et une continuation périmée."))
                await self.push_frame(LLMFullResponseEndFrame())
                finished.set()
            else:
                await super().process_frame(frame, direction)

    class Sink(Pass):
        async def process_frame(self, frame, direction):
            if isinstance(frame, LLMTextFrame | TTSSpeakFrame):
                received.append(frame)
            await super().process_frame(frame, direction)

    llm = FailingModel()
    bind_generation_failures(llm, lambda: policy.turn_epoch)
    worker = PipelineWorker(Pipeline([Pass(), llm, response, Sink()]), enable_rtvi=False)
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def ready(*_):
        started.set()

    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(started.wait(), 2)
        await worker.queue_frame(
            LLMContextFrame(
                LLMContext(messages=[{"role": "user", "content": policy.last_caller_text}])
            )
        )
        await asyncio.wait_for(finished.wait(), 2)
        await asyncio.sleep(0.05)
        assert not [f for f in received if isinstance(f, LLMTextFrame)]
        repairs = [f for f in received if isinstance(f, TTSSpeakFrame)]
        assert len(repairs) == (0 if stale else 1)
        if repairs:
            assert "Désolé" in repairs[0].text
        assert sum(e["type"] == "provider_recovery" for e in events) == (0 if stale else 1)
    finally:
        await runner.cancel("test complete")
        await asyncio.wait_for(task, 3)
        await policy.close()


@pytest.mark.asyncio
async def test_generation_failure_during_goodbye_uses_verified_closing_path():
    policy = AgentPolicyRuntime(
        caller_id="unknown:failure-test",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
    )
    await policy.observe_transcription("Goodbye.")
    owner = ProductionCallPipeline.__new__(ProductionCallPipeline)
    owner.policy = policy
    owner.services = SimpleNamespace(stt=SimpleNamespace(caller_owns_floor=lambda: False))
    owner.transport = SimpleNamespace(session=SimpleNamespace(is_active=True))
    owner._generation_recovery_epochs = set()
    closes = []

    async def close(result, **_):
        closes.append(result)

    owner._accept_ai_end_call = close
    await owner._recover_generation_failure(policy.turn_epoch)
    assert len(closes) == 1 and "Goodbye" in closes[0]["closing_message"]
    await policy.close()


@pytest.mark.asyncio
async def test_recovery_reservation_is_discarded_if_caller_resumes_while_it_is_prepared():
    policy = AgentPolicyRuntime(
        caller_id="unknown:failure-test",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
    )
    await policy.observe_transcription("Please explain the options.")
    owner = ProductionCallPipeline.__new__(ProductionCallPipeline)
    owner.policy = policy
    owner.services = SimpleNamespace(stt=SimpleNamespace(caller_owns_floor=lambda: False))
    owner.transport = SimpleNamespace(session=SimpleNamespace(is_active=True))
    owner._generation_recovery_epochs = set()
    original = policy.finalize_response_with_identity

    async def finalize(*args, **kwargs):
        result = await original(*args, **kwargs)
        policy.observe_speech_started()
        return result

    policy.finalize_response_with_identity = finalize
    await owner._recover_generation_failure(policy.turn_epoch)
    assert not policy._pending_playback_ids
    await policy.close()


@pytest.mark.asyncio
async def test_failed_partial_tool_protocol_cannot_requeue_or_execute():
    from phone_agent_gateway.ai_bridge.cascade_tools import ToolCallProcessor
    from phone_agent_gateway.ai_bridge.generation_recovery import (
        GENERATION_ID,
        GenerationFailureFrame,
    )

    policy = AgentPolicyRuntime(
        caller_id="unknown:failure-test",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
    )
    calls = []

    async def execute(*args):
        calls.append(args)
        return "{}"

    runtime = SimpleNamespace(policy=policy, catalog={}, execute=execute)
    tools = ToolCallProcessor(runtime, context=LLMContext(), llm=object())
    frames = []

    async def capture(frame, *_):
        frames.append(frame)

    tools.push_frame = capture
    start = LLMFullResponseStartFrame()
    start.metadata[GENERATION_ID] = 1
    text = LLMTextFrame('<tool_call>{"name":"unfinished"')
    text.metadata[GENERATION_ID] = 1
    end = LLMFullResponseEndFrame()
    end.metadata[GENERATION_ID] = 1
    for frame in [
        start,
        text,
        GenerationFailureFrame(turn_epoch=policy.turn_epoch, generation_id=1),
        end,
        text,
        end,
    ]:
        await tools.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert not calls
    assert not any(type(f).__name__ == "LLMRunFrame" for f in frames)
    assert sum(isinstance(f, LLMFullResponseEndFrame) for f in frames) == 1
    await policy.close()


@pytest.mark.asyncio
async def test_background_error_from_a_finished_generation_cannot_damage_the_next_one():
    from pipecat.frames.frames import ErrorFrame

    from phone_agent_gateway.ai_bridge.generation_recovery import GenerationFailureFrame

    llm = FrameProcessor()
    epoch = [1]
    bind_generation_failures(llm, lambda: epoch[0])
    frames = []

    async def capture(frame, *_):
        frames.append(frame)

    llm.push_frame = capture
    context = LLMContextFrame(LLMContext())
    await llm._call_event_handler("on_before_process_frame", context)
    release = asyncio.Event()

    async def old_background_error():
        await release.wait()
        await llm._call_event_handler("on_error", ErrorFrame(error="old failure", processor=llm))

    task = asyncio.create_task(old_background_error())
    await llm._call_event_handler("on_after_process_frame", context)
    epoch[0] = 2
    await llm._call_event_handler("on_before_process_frame", context)
    release.set()
    await task
    assert frames == []
    await llm._call_event_handler("on_error", ErrorFrame(error="current failure", processor=llm))
    failed = [f for f in frames if isinstance(f, GenerationFailureFrame)]
    assert len(failed) == 1 and failed[0].turn_epoch == 2
    await llm._call_event_handler("on_after_process_frame", context)
