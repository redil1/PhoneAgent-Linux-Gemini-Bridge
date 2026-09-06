"""Partial synthesis is not full delivery; stalls and cancellation own cleanup."""
from __future__ import annotations

import asyncio
import time

import pytest
from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame

from phone_agent_gateway.ai_bridge import edge_tts_service as module


@pytest.mark.asyncio
async def test_attached_partial_prefetch_reports_failure_without_replaying_sentence():
    service = module.EdgeTTSService()
    ready, release = asyncio.Event(), asyncio.Event()
    async def decode(_phrase, stream):
        stream.append(bytes(640))
        ready.set()
        await release.wait()
        return b""  # Decoder reports failure after streaming a prefix.
    service._decode_edge_pcm = decode
    task = asyncio.create_task(service.prefetch_text("This sentence is incomplete."))
    await ready.wait()
    output = service.run_tts("This sentence is incomplete.", "one")
    first = await anext(output)
    assert isinstance(first, TTSAudioRawFrame)
    release.set()
    rest = [f async for f in output]
    await task
    assert len(rest) == 1 and isinstance(rest[0], ErrorFrame)
    assert not service._prefetch_cache
    assert not service._prefetch_streams


@pytest.mark.asyncio
async def test_cancelled_prefetch_cannot_be_promoted_to_successful_speech():
    service = module.EdgeTTSService()
    stream = module._PrefetchedPCMStream()
    service._prefetch_streams["Hello."] = stream
    stream.append(bytes(640))
    output = service.run_tts("Hello.", "one")
    assert isinstance(await anext(output), TTSAudioRawFrame)
    service.clear_prefetch()
    rest = [f async for f in output]
    assert len(rest) == 1 and isinstance(rest[0], ErrorFrame)


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_synthesis_stall_has_bounded_wait_and_closes_generator(partial):
    service = module.EdgeTTSService(first_audio_timeout_secs=0.04, audio_idle_timeout_secs=0.04)
    closed = asyncio.Event()
    async def stalled(*_):
        try:
            if partial:
                yield TTSAudioRawFrame(bytes(640), 16000, 1)
            await asyncio.Event().wait()
        finally:
            closed.set()
    service._run_tts_impl = stalled
    started = time.monotonic()
    frames = [f async for f in service.run_tts("Hello.", "context")]
    assert time.monotonic() - started < 0.5
    assert closed.is_set()
    assert sum(isinstance(f, ErrorFrame) for f in frames) == 1
    assert sum(isinstance(f, TTSAudioRawFrame) for f in frames) == int(partial)


@pytest.mark.asyncio
async def test_total_synthesis_deadline_bounds_stream_that_keeps_producing_audio():
    service = module.EdgeTTSService(synthesis_timeout_secs=0.07)
    closed = asyncio.Event()
    async def endless(*_):
        try:
            while True:
                await asyncio.sleep(0.01)
                yield TTSAudioRawFrame(bytes(640), 16000, 1)
        finally:
            closed.set()
    service._run_tts_impl = endless
    frames = [f async for f in service.run_tts("Hello.", "context")]
    assert closed.is_set()
    assert isinstance(frames[-1], ErrorFrame)
    assert 1 <= len(frames) < 20


@pytest.mark.asyncio
async def test_consumer_close_reaps_live_decoder_and_network_writer(monkeypatch):
    written, input_closed, cancelled, writer_closed = (asyncio.Event() for _ in range(4))
    class Decoder:
        def __init__(self, *_, **__):
            pass
        async def start(self):
            pass
        async def write(self, _):
            written.set()
        async def close_input(self):
            input_closed.set()
        async def read(self):
            await written.wait()
            yield bytes(640)
            await input_closed.wait()
        async def wait(self):
            pass
        async def cancel(self):
            cancelled.set()
    class Communicator:
        async def stream(self):
            try:
                yield {"type": "audio", "data": b"dummy encoded chunk"}
                await asyncio.Event().wait()
            finally:
                writer_closed.set()
    monkeypatch.setattr(module, "FFmpegMP3StreamDecoder", Decoder)
    service = module.EdgeTTSService(communicator_factory=lambda **_: Communicator())
    output = service.run_tts("Hello.", "context")
    assert isinstance(await anext(output), TTSAudioRawFrame)
    await output.aclose()
    assert cancelled.is_set() and writer_closed.is_set() and input_closed.is_set()


@pytest.mark.asyncio
async def test_old_cancelled_prefetch_cannot_remove_replacement_task():
    service = module.EdgeTTSService()
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def decode(_phrase, stream):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    service._decode_edge_pcm = decode
    old = asyncio.create_task(service.prefetch_text("Hello."))
    await entered.wait()
    service.clear_prefetch()
    replacement = asyncio.create_task(asyncio.sleep(0.1, result=b"new"))
    new_stream = module._PrefetchedPCMStream()
    service._prefetch_streams["Hello."] = new_stream
    service._prefetch_phrase_tasks["Hello."] = replacement
    with pytest.raises(asyncio.CancelledError):
        await old
    assert cancelled.is_set()
    assert service._prefetch_phrase_tasks["Hello."] is replacement
    await replacement
    service.clear_prefetch()


@pytest.mark.asyncio
async def test_no_audio_failure_closes_context_and_releases_next_request_without_idle_wait():
    from pipecat.frames.frames import TTSSpeakFrame, TTSTextFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.workers.runner import WorkerRunner

    received = []
    audio_ready = asyncio.Event()
    class Collector(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            received.append(frame)
            if isinstance(frame, TTSAudioRawFrame):
                audio_ready.set()
            await self.push_frame(frame, direction)
    service = module.EdgeTTSService()
    async def synthesize(text, context_id):
        if "broken" in text:
            yield ErrorFrame(error="controlled zero PCM failure")
        else:
            yield TTSAudioRawFrame(bytes(640), 16000, 1, context_id=context_id)
    service._run_tts_impl = synthesize
    worker = PipelineWorker(Pipeline([service, Collector()]))
    runner = WorkerRunner(handle_sigint=False)
    task = asyncio.create_task(runner.run(worker))
    try:
        await worker.queue_frame(TTSSpeakFrame("broken first reply"), FrameDirection.DOWNSTREAM)
        await worker.queue_frame(TTSSpeakFrame("working next reply"), FrameDirection.DOWNSTREAM)
        await asyncio.wait_for(audio_ready.wait(), 1.0)
        assert not any(isinstance(f, TTSTextFrame) and "broken" in f.text for f in received)
    finally:
        await worker.cancel()
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_failure_of_queued_reply_does_not_fail_the_reply_currently_playing():
    from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
    events = []
    policy = AgentPolicyRuntime(caller_id="unknown:tts", task_id="customer_support", language="en-US", memory_enabled=False, event_sink=events.append)
    active = policy.begin_streamed_response()
    await policy.playback_started()
    queued = policy.begin_streamed_response()
    await policy.playback_failed("queued synthesis failed", response_id=queued)
    assert policy._active_playback_id == active
    assert queued not in policy._pending_playback_ids
    await policy.playback_failed("late duplicate", response_id=queued)
    assert policy._active_playback_id == active
    await policy.playback_stopped(delivered_frames=10)
    assert [(e['response_id'], e['status']) for e in events if e['type'] == 'playback_status'] == [(active,'playing'), (queued,'failed'), (active,'completed')]
    await policy.close()


@pytest.mark.asyncio
async def test_failed_context_suppresses_remaining_chunks_but_new_context_can_speak():
    service = module.EdgeTTSService()
    calls = []
    async def synthesize(text, context_id):
        calls.append((text, context_id))
        if context_id == "broken":
            yield ErrorFrame(error="controlled failure")
        else:
            yield TTSAudioRawFrame(bytes(640), 16000, 1, context_id=context_id)
    service._run_tts_impl = synthesize
    first = [f async for f in service.run_tts("First sentence.", "broken")]
    remaining = [f async for f in service.run_tts("Remaining sentence.", "broken")]
    next_reply = [f async for f in service.run_tts("Next reply.", "new")]
    assert isinstance(first[0], ErrorFrame) and not remaining
    assert isinstance(next_reply[0], TTSAudioRawFrame)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_stale_synthesis_error_cannot_fail_or_repair_the_new_turn():
    from unittest.mock import AsyncMock

    from pipecat.processors.frame_processor import FrameDirection

    from phone_agent_gateway.ai_bridge.agent_policy import (
        AgentPolicyRuntime,
        ResponsePolicyProcessor,
    )
    from phone_agent_gateway.ai_bridge.speech_floor_guard import (
        SPEECH_RESPONSE_ID,
        SPEECH_TURN_EPOCH,
        SYNTHESIS_CONTEXT_ID,
    )
    policy = AgentPolicyRuntime(caller_id="unknown:tts", task_id="customer_support", language="en-US", memory_enabled=False)
    await policy.observe_transcription("Explain the first choice.")
    old_epoch = policy.turn_epoch
    old = policy.begin_streamed_response()
    await policy.mark_playback_interrupted()
    await policy.observe_transcription("Actually, explain the other choice.")
    current = policy.begin_streamed_response()
    processor = ResponsePolicyProcessor(policy)
    processor.push_frame = AsyncMock()
    recovery = AsyncMock()
    processor.bind_output_recovery(recovery)
    for epoch in (old_epoch, policy.turn_epoch):
        error = ErrorFrame(error="late old synthesis failure")
        error.metadata.update({SPEECH_TURN_EPOCH: epoch, SPEECH_RESPONSE_ID: old, SYNTHESIS_CONTEXT_ID: "old"})
        await processor.process_frame(error, FrameDirection.UPSTREAM)
    recovery.assert_not_awaited()
    assert current in policy._pending_playback_ids
    await policy.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("language, caller, expected", [
    ("en-US", "Explain this please.", "Sorry"),
    ("fr-FR", "Pouvez-vous expliquer cela ?", "Désolé"),
])
async def test_output_recovery_is_once_per_turn_even_if_recovery_synthesis_fails(language, caller, expected):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
    from phone_agent_gateway.ai_bridge.production_pipeline import ProductionCallPipeline
    events = []
    owner = ProductionCallPipeline.__new__(ProductionCallPipeline)
    owner.policy = AgentPolicyRuntime(caller_id="unknown:tts", task_id="customer_support", language=language, memory_enabled=False, event_sink=events.append)
    await owner.policy.observe_transcription(caller)
    owner.services = SimpleNamespace(stt=SimpleNamespace(caller_owns_floor=lambda: False))
    owner.transport = SimpleNamespace(session=SimpleNamespace(is_active=True))
    owner.response_policy = SimpleNamespace(queue_frame=AsyncMock())
    owner._generation_recovery_epochs = set()
    owner._event_sink = events.append
    epoch = owner.policy.turn_epoch
    await owner._recover_generation_failure(epoch, provider="tts")
    await owner._recover_generation_failure(epoch, provider="tts")
    owner.response_policy.queue_frame.assert_awaited_once()
    assert expected in owner.response_policy.queue_frame.call_args.args[0].text
    assert [e['provider'] for e in events if e.get('type')=='provider_recovery'] == ['tts']
    await owner.policy.playback_started()
    await owner.policy.playback_stopped(delivered_frames=10)
    await owner.policy.observe_transcription("And the second option?" if language == "en-US" else "Et la deuxième option ?")
    await owner._recover_generation_failure(owner.policy.turn_epoch, provider="tts")
    assert owner.response_policy.queue_frame.await_count == 2
    await owner.policy.close()


@pytest.mark.asyncio
async def test_late_bot_events_from_failed_audio_cannot_steal_recovery_identity(monkeypatch):
    from unittest.mock import AsyncMock

    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        TTSStartedFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection
    from pipecat.transports.base_output import BaseOutputTransport

    from phone_agent_gateway.ai_bridge.agent_policy import (
        AgentPolicyRuntime,
        PlaybackEventProcessor,
    )
    from phone_agent_gateway.ai_bridge.pipecat_transport import PhoneAgentTransport
    from phone_agent_gateway.ai_bridge.speech_floor_guard import SPEECH_RESPONSE_ID

    events, emitted = [], []
    policy = AgentPolicyRuntime(caller_id="unknown:tts", task_id="customer_support", language="en-US", memory_enabled=False, event_sink=events.append)
    failed = policy.begin_streamed_response()
    await policy.playback_failed("failed before bot-start callback", response_id=failed)
    repair = policy.begin_streamed_response()
    transport = PhoneAgentTransport()
    output = transport.output()
    async def capture(_self, frame, direction=FrameDirection.DOWNSTREAM):
        emitted.append(frame)
    monkeypatch.setattr(BaseOutputTransport, "push_frame", capture)
    marker = TTSStartedFrame(context_id="failed")
    marker.metadata[SPEECH_RESPONSE_ID] = failed
    await output.write_transport_frame(marker)
    await output.push_frame(BotStartedSpeakingFrame())
    marker = TTSStartedFrame(context_id="recovery")
    marker.metadata[SPEECH_RESPONSE_ID] = repair
    await output.write_transport_frame(marker)
    await output.push_frame(BotStoppedSpeakingFrame())
    await output.push_frame(BotStartedSpeakingFrame())
    await output.push_frame(BotStoppedSpeakingFrame())
    assert [f.metadata[SPEECH_RESPONSE_ID] for f in emitted] == [failed, failed, repair, repair]
    observer = PlaybackEventProcessor(policy)
    observer.push_frame = AsyncMock()
    for frame in emitted[:2]:
        await observer.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert policy._active_playback_id is None and list(policy._pending_playback_ids) == [repair]
    for frame in emitted[2:]:
        await observer.process_frame(frame, FrameDirection.DOWNSTREAM)
    statuses = [(e['response_id'], e['status']) for e in events if e['type']=='playback_status']
    assert statuses == [(failed,'failed'), (repair,'playing'), (repair,'completed')]
    await policy.close()


@pytest.mark.asyncio
async def test_failed_progress_does_not_add_apology_behind_an_already_queued_answer():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
    from phone_agent_gateway.ai_bridge.production_pipeline import ProductionCallPipeline
    owner = ProductionCallPipeline.__new__(ProductionCallPipeline)
    owner.policy = AgentPolicyRuntime(caller_id="unknown:tts", task_id="customer_support", language="en-US", memory_enabled=False)
    await owner.policy.observe_transcription("What can I do next?")
    _, _, queued = await owner.policy.finalize_response_with_identity("Please open the account settings page.")
    owner.services = SimpleNamespace(stt=SimpleNamespace(caller_owns_floor=lambda: False))
    owner.transport = SimpleNamespace(session=SimpleNamespace(is_active=True))
    owner.response_policy = SimpleNamespace(queue_frame=AsyncMock())
    owner._generation_recovery_epochs = set()
    owner._event_sink = None
    epoch = owner.policy.turn_epoch
    await owner._recover_generation_failure(epoch, provider="tts")
    owner.response_policy.queue_frame.assert_not_awaited()
    assert epoch not in owner._generation_recovery_epochs
    await owner.policy.playback_failed("remaining answer also failed", response_id=queued)
    await owner._recover_generation_failure(epoch, provider="tts")
    owner.response_policy.queue_frame.assert_awaited_once()
    await owner.policy.close()
