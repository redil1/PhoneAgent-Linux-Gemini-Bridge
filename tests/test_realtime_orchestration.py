"""Production queue boundaries and metadata for responsive voice orchestration."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.aggregators.llm_response_universal import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime, ResponsePolicyProcessor
from phone_agent_gateway.ai_bridge.cascade_tools import MAX_TOOL_ITERATIONS, ToolCallProcessor
from phone_agent_gateway.ai_bridge.phone_voice_agent import PhoneVoiceAgent
from phone_agent_gateway.ai_bridge.production_pipeline import (
    ProductionCallPipeline,
    prewarm_speech_models,
)
from phone_agent_gateway.ai_bridge.runtime_config import ProviderConfig
from phone_agent_gateway.ai_bridge.session import CallSessionState, SessionPhase
from phone_agent_gateway.ai_bridge.speculative_turn import SpeculativeTurnCoordinator
from phone_agent_gateway.ai_bridge.tasks.tool_catalog import RealtimeTool


def catalog():
    return {"lookup": RealtimeTool(name="lookup", definition={"name": "lookup", "parameters": {"properties": {}}}, handler=lambda _: {})}


@pytest.mark.asyncio
async def test_tool_progress_reaches_output_before_slow_tool_finishes():
    policy = AgentPolicyRuntime(caller_id="unknown:test", task_id="customer_support", language="en-US", memory_enabled=False)
    entered, release, spoken, started = (asyncio.Event() for _ in range(4))
    order = []

    class Forward(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class Sink(Forward):
        async def process_frame(self, frame, direction):
            if isinstance(frame, TTSSpeakFrame):
                order.append("progress")
                spoken.set()
            await super().process_frame(frame, direction)

    async def execute(*_):
        entered.set()
        await release.wait()
        order.append("tool_complete")
        return '{}'

    response = ResponsePolicyProcessor(policy)
    owner = ProductionCallPipeline.__new__(ProductionCallPipeline)
    owner.policy, owner.response_policy = policy, response
    runtime = SimpleNamespace(catalog=catalog(), policy=policy, execute=execute)
    processor = ToolCallProcessor(runtime, context=LLMContext(), llm=object(), preamble=owner._speak_tool_preamble)
    worker = PipelineWorker(Pipeline([Forward(), processor, response, Sink()]), enable_rtvi=False)
    @worker.event_handler("on_pipeline_started")
    async def ready(*_):
        started.set()
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(started.wait(), 2)
        await worker.queue_frames([LLMFullResponseStartFrame(), LLMTextFrame('<tool_call>{"name":"lookup","arguments":{}}</tool_call>'), LLMFullResponseEndFrame()])
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(spoken.wait(), 1)
        assert order == ["progress"]
        # The tool-only model response was resolved; only progress owns a
        # pending playback reservation, so subsequent audio IDs cannot drift.
        assert len(policy._pending_playback_ids) == 1
        release.set()
        await asyncio.sleep(0.03)
        assert order == ["progress", "tool_complete"]
    finally:
        release.set()
        await runner.cancel("test complete")
        await asyncio.wait_for(task, 3)
        await policy.close()


@pytest.mark.asyncio
async def test_split_protocol_after_spoken_prefix_never_leaks():
    called = []
    async def execute(name, args):
        called.append(name)
        return '{}'
    runtime = SimpleNamespace(catalog=catalog(), policy=SimpleNamespace(reply_language="en"), execute=execute)
    processor = ToolCallProcessor(runtime, context=LLMContext(), llm=object())
    frames = []
    async def capture(frame, *_):
        frames.append(frame)
    processor.push_frame = capture
    await processor.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    text = 'I will check that. <tool_call>{"name":"lookup","arguments":{}}</tool_call>'
    for character in text:
        await processor.process_frame(LLMTextFrame(character), FrameDirection.DOWNSTREAM)
    await processor.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    assert ''.join(f.text for f in frames if isinstance(f, LLMTextFrame)) == 'I will check that. '
    assert called == ["lookup"]
    assert sum(isinstance(f, LLMFullResponseEndFrame) for f in frames) == 1


@pytest.mark.asyncio
async def test_exhausted_tool_budget_does_not_requeue_forever():
    runtime = SimpleNamespace(catalog=catalog(), policy=SimpleNamespace(reply_language="en"))
    processor = ToolCallProcessor(runtime, context=LLMContext(), llm=object())
    processor._iterations = MAX_TOOL_ITERATIONS
    frames = []
    async def capture(frame, *_):
        frames.append(frame)
    processor.push_frame = capture
    for frame in [LLMFullResponseStartFrame(), LLMTextFrame('<tool_call>{"name":"lookup"}</tool_call>'), LLMFullResponseEndFrame()]:
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert not any(type(f).__name__ == "LLMRunFrame" for f in frames)
    assert any(isinstance(f, LLMTextFrame) and "couldn't complete" in f.text for f in frames)


@pytest.mark.asyncio
async def test_speculation_caches_tool_decision_without_synthesizing_it():
    spoken, events = [], []
    class LLM:
        def start_prefetch(self, _):
            async def result():
                return '<tool_call>{"name":"lookup"}</tool_call>'
            return asyncio.create_task(result())
        def cancel_prefetch(self, _):
            pass
    class TTS:
        async def prefetch_text(self, text):
            spoken.append(text)
        def clear_prefetch(self):
            pass
    coordinator = SpeculativeTurnCoordinator(context=LLMContext(), llm=LLM(), tts=TTS(), policy=SimpleNamespace(preview_response=lambda text: text), event_sink=events.append)
    await coordinator.consider("Please check")
    await coordinator._task
    assert not spoken
    assert events[-1]["state"] == "tool_ready" and events[-1]["tts_ms"] == 0
    await coordinator.close()


@pytest.mark.asyncio
async def test_missing_local_decoder_cannot_be_reported_as_prewarmed(monkeypatch):
    from phone_agent_gateway.ai_bridge import parakeet_local_stt
    def fail(*_):
        raise ModuleNotFoundError("decoder unavailable")
    monkeypatch.setattr(parakeet_local_stt, "prewarm_parakeet", fail)
    with pytest.raises(RuntimeError, match="could not be prepared"):
        await prewarm_speech_models(ProviderConfig(stt_provider="whisper_turbo", tts_provider="edge_tts"))


@pytest.mark.asyncio
async def test_teardown_cancels_instead_of_draining_audio_to_ended_call():
    session = CallSessionState()
    session.set_phase(SessionPhase.ACTIVE)
    events = []
    async def disconnected():
        assert not session.is_active
        events.append("accounting_closed")
    class Speech:
        policy = SimpleNamespace(playback_disconnected=disconnected)
        async def cancel(self, reason):
            assert not session.is_active
            events.append("cancelled")
        async def stop(self):
            raise AssertionError("Ended calls must not drain queued audio")
    host = PhoneVoiceAgent(SimpleNamespace(link_authentication_key=b'x'*32, event_stream_enabled=False))
    host._runtime = SimpleNamespace(
        session=session, pipeline=Speech(), recorder=None, phone_audio_route={},
        client=SimpleNamespace(close=lambda: events.append("link_closed")),
    )
    await host._close_runtime(hangup=False)
    assert events == ["accounting_closed", "cancelled", "link_closed"]
    assert session.snapshot().phase is SessionPhase.CLOSED


def test_delayed_event_keeps_original_call_context(capsys):
    host = PhoneVoiceAgent(SimpleNamespace(link_authentication_key=b'x'*32, event_stream_enabled=True, call_channel="gsm"))
    original = SimpleNamespace(session=CallSessionState())
    sink = host._call_event_sink(original, "unknown:original")
    host._runtime = SimpleNamespace(session=CallSessionState())
    host._active_caller_id = "unknown:next"
    sink({"type": "provider_latency", "call_id": "untrusted"})
    import json
    payload = json.loads(capsys.readouterr().out.split('PHONE_AGENT_EVENT ')[1])
    assert payload["call_id"] == str(original.session.call_id)
    assert payload["caller_id"] == "unknown:original"
    assert payload["monotonic_ns"] and payload["timestamp"]


@pytest.mark.asyncio
async def test_interrupted_goodbye_releases_terminal_request_latch():
    pipeline = ProductionCallPipeline.__new__(ProductionCallPipeline)
    pipeline._terminal_request_accepted = True
    events = []
    pipeline._event_sink = events.append
    await pipeline._on_policy_event({"type": "terminal_completion_aborted", "response_id": "closing-1"})
    assert not pipeline._terminal_request_accepted
    assert events[0]["response_id"] == "closing-1"


@pytest.mark.asyncio
async def test_model_retry_chain_has_one_total_deadline():
    from phone_agent_gateway.ai_bridge.antigravity_gemini_llm import AntigravityGeminiLLMService
    service = AntigravityGeminiLLMService(turn_timeout_secs=0.03)
    events = []
    service.set_latency_sink(events.append)
    async def slow(_):
        await asyncio.sleep(0.3)
        return "too late"
    service._generate_gemini_within_budget = slow
    with pytest.raises(TimeoutError):
        await service._generate_gemini("example")
    assert events[-1]["operation"] == "turn_timeout"
    await service.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize('tail', ['<tool_', '<TOOL_CALL>', '<tool_call>{"name":'])
async def test_cut_off_protocol_is_repaired_without_reading_it_to_the_caller(tail):
    runtime = SimpleNamespace(catalog=catalog(), policy=SimpleNamespace(reply_language='en'))
    processor = ToolCallProcessor(runtime, context=LLMContext(), llm=object())
    frames = []
    async def capture(frame, *_):frames.append(frame)
    processor.push_frame = capture
    for frame in [LLMFullResponseStartFrame(), LLMTextFrame('Let me check. '+tail), LLMFullResponseEndFrame()]:
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert ''.join(f.text for f in frames if isinstance(f, LLMTextFrame)) == 'Let me check. '
    assert sum(type(f).__name__ == 'LLMRunFrame' for f in frames) == 1
    assert sum(isinstance(f, LLMFullResponseEndFrame) for f in frames) == 1


@pytest.mark.asyncio
async def test_case_variant_protocol_runs_through_the_same_validated_tool_path():
    called=[]
    async def execute(name, args):
        called.append((name, args))
        return '{}'
    runtime = SimpleNamespace(catalog=catalog(), policy=SimpleNamespace(reply_language='en'), execute=execute)
    processor = ToolCallProcessor(runtime, context=LLMContext(), llm=object())
    frames=[]
    async def capture(frame,*_):frames.append(frame)
    processor.push_frame=capture
    await processor.process_frame(LLMFullResponseStartFrame(),FrameDirection.DOWNSTREAM)
    for c in '<TOOL_CALL>{"name":"lookup","arguments":{}}</TOOL_CALL>':
        await processor.process_frame(LLMTextFrame(c),FrameDirection.DOWNSTREAM)
    await processor.process_frame(LLMFullResponseEndFrame(),FrameDirection.DOWNSTREAM)
    assert called == [('lookup','{}')]
    assert not [f for f in frames if isinstance(f,LLMTextFrame)]
