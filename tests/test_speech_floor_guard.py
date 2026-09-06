"""Caller-floor regressions, including Pipecat's real TTS serialization queue."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    UserStartedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from phone_agent_gateway.ai_bridge.agent_policy import (
    AgentPolicyRuntime,
    ResponsePolicyProcessor,
    TranscriptionPolicyProcessor,
)
from phone_agent_gateway.ai_bridge.edge_tts_service import EdgeTTSService
from phone_agent_gateway.ai_bridge.memory.memory_manager import LayeredMemoryManager
from phone_agent_gateway.ai_bridge.speech_floor_guard import (
    SPEECH_TURN_EPOCH,
    SpeechFloorGuard,
    SpeechFloorPermissionFrame,
)

DOWN = FrameDirection.DOWNSTREAM


def _audio(context_id: str | None = "answer") -> TTSAudioRawFrame:
    return TTSAudioRawFrame(b"\x02\x00" * 320, 16000, 1, context_id=context_id)


class Capture:
    def __init__(self) -> None:
        self.frames: list[Frame] = []

    async def __call__(self, frame: Frame, direction: FrameDirection = DOWN) -> None:
        self.frames.append(frame)

    @property
    def audio(self) -> list[TTSAudioRawFrame]:
        return [frame for frame in self.frames if isinstance(frame, TTSAudioRawFrame)]

    @property
    def text(self) -> list[str]:
        return [frame.text.rstrip() for frame in self.frames if isinstance(frame, LLMTextFrame)]


def _guard() -> tuple[SpeechFloorGuard, dict[str, Any], Capture]:
    state: dict[str, Any] = {"caller": False, "epoch": 0}
    guard = SpeechFloorGuard(
        caller_owns_floor=lambda: state["caller"],
        turn_epoch=lambda: state["epoch"],
    )
    capture = Capture()
    guard.push_frame = capture  # type: ignore[method-assign]
    return guard, state, capture


async def _start(guard: SpeechFloorGuard, epoch: int, context_id: str | None = "answer") -> None:
    start = LLMFullResponseStartFrame()
    start.metadata[SPEECH_TURN_EPOCH] = epoch
    await guard.process_frame(start, DOWN)
    await guard.process_frame(TTSStartedFrame(context_id=context_id), DOWN)


@pytest.mark.asyncio
async def test_current_response_and_contextless_chunks_can_play() -> None:
    guard, _state, capture = _guard()
    await _start(guard, 0)
    await guard.process_frame(_audio(), DOWN)
    await guard.process_frame(TTSStoppedFrame(context_id="answer"), DOWN)
    await _start(guard, 0, context_id=None)
    await guard.process_frame(_audio(None), DOWN)
    assert len(capture.audio) == 2


@pytest.mark.asyncio
async def test_acoustic_callback_stops_audio_before_onset_frame_arrives() -> None:
    guard, state, capture = _guard()
    await _start(guard, 0)
    state["caller"] = True
    await guard.process_frame(_audio(), DOWN)
    state["caller"] = False
    # The old context remains canceled even after the room is quiet again.
    await guard.process_frame(_audio(), DOWN)
    await guard.process_frame(TTSStoppedFrame(context_id="answer"), DOWN)
    assert capture.audio == []
    assert not any(isinstance(f, TTSStoppedFrame) for f in capture.frames)


@pytest.mark.asyncio
async def test_epoch_change_drops_prepared_answer_before_first_audio() -> None:
    guard, state, capture = _guard()
    await _start(guard, 0)
    state["epoch"] = 1
    await guard.process_frame(_audio(), DOWN)
    assert capture.audio == []
    assert guard.dropped_audio_frames == 1


@pytest.mark.asyncio
async def test_new_answer_cannot_reauthorize_old_context() -> None:
    guard, state, capture = _guard()
    await _start(guard, 0, "old")
    state["epoch"] = 2
    await _start(guard, 2, "new")
    await guard.process_frame(_audio("old"), DOWN)
    await guard.process_frame(TTSStartedFrame(context_id="old"), DOWN)
    await guard.process_frame(_audio("old"), DOWN)
    await guard.process_frame(_audio("new"), DOWN)
    assert [frame.context_id for frame in capture.audio] == ["new"]


@pytest.mark.asyncio
async def test_onset_blocks_pending_start_until_new_authorization() -> None:
    guard, state, capture = _guard()
    await _start(guard, 0, "old")
    await guard.process_frame(UserStartedSpeakingFrame(), DOWN)
    await guard.process_frame(TTSStartedFrame(context_id="late"), DOWN)
    await guard.process_frame(_audio("late"), DOWN)
    state["epoch"] = 2
    # Direct speech (greeting, tool preamble, goodbye) has its own ordered marker.
    await guard.process_frame(SpeechFloorPermissionFrame(2), DOWN)
    await guard.process_frame(TTSStartedFrame(context_id="direct"), DOWN)
    await guard.process_frame(_audio("direct"), DOWN)
    assert [frame.context_id for frame in capture.audio] == ["direct"]
    assert not any(isinstance(f, SpeechFloorPermissionFrame) for f in capture.frames)


@pytest.mark.asyncio
async def test_direct_speech_marker_queued_before_caller_continuation_is_stale() -> None:
    guard, state, capture = _guard()
    state["epoch"] = 2
    await guard.process_frame(SpeechFloorPermissionFrame(0), DOWN)
    await guard.process_frame(TTSStartedFrame(context_id="old-goodbye"), DOWN)
    await guard.process_frame(_audio("old-goodbye"), DOWN)
    assert capture.audio == []


def _runtime(tmp_path: Path) -> AgentPolicyRuntime:
    return AgentPolicyRuntime(
        caller_id="anonymous",
        task_id="customer_support",
        language="en-US",
        memory_enabled=False,
        memory_manager=LayeredMemoryManager(storage_path=tmp_path / "memory.json"),
    )


@pytest.mark.asyncio
async def test_acoustic_onset_invalidates_policy_before_any_transcript(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    transcription = TranscriptionPolicyProcessor(runtime)
    response = ResponsePolicyProcessor(runtime)
    capture = Capture()
    response.push_frame = capture  # type: ignore[method-assign]
    transcription.push_frame = response.process_frame  # type: ignore[method-assign]
    await response.process_frame(LLMFullResponseStartFrame(), DOWN)
    await response.process_frame(LLMTextFrame("A pending unfinished response"), DOWN)
    original_epoch = runtime.turn_epoch
    await transcription.process_frame(UserStartedSpeakingFrame(), DOWN)
    await response.process_frame(LLMTextFrame(" and its late continuation."), DOWN)
    await response.process_frame(LLMFullResponseEndFrame(), DOWN)
    assert runtime.is_stale(original_epoch)
    assert runtime.last_caller_text == ""
    assert capture.text == []
    assert not runtime._pending_playback_ids
    await runtime.observe_transcription("I need help choosing an option.")
    await response.process_frame(LLMFullResponseStartFrame(), DOWN)
    await response.process_frame(LLMTextFrame("I can explain the options."), DOWN)
    await response.process_frame(LLMFullResponseEndFrame(), DOWN)
    assert capture.text == ["I can explain the options."]


@pytest.mark.asyncio
async def test_queued_direct_speech_keeps_original_epoch(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    response = ResponsePolicyProcessor(runtime)
    response.enable_speech_floor_guard()
    capture = Capture()
    response.push_frame = capture  # type: ignore[method-assign]
    queued = TTSSpeakFrame("Thanks for your time, goodbye.")
    queued.metadata[SPEECH_TURN_EPOCH] = runtime.turn_epoch
    runtime.observe_speech_started()
    await response.process_frame(queued, DOWN)
    assert capture.frames == []


@pytest.mark.asyncio
async def test_caller_resuming_during_recovery_never_releases_old_repeat(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    response = ResponsePolicyProcessor(runtime)
    capture = Capture()
    response.push_frame = capture  # type: ignore[method-assign]

    async def retry(_text: str, _epoch: int) -> bool:
        runtime.observe_speech_started()
        return False

    async def resolved(_epoch: int) -> None:
        pass

    response.bind_repetition_recovery(retry, resolved)
    await response.process_frame(LLMFullResponseStartFrame(), DOWN)
    await response.process_frame(LLMTextFrame("I hear you, clear value is important."), DOWN)
    await response.process_frame(LLMFullResponseEndFrame(), DOWN)
    assert capture.text == []
    assert not runtime._pending_playback_ids


@pytest.mark.asyncio
async def test_recovery_does_not_speak_repair_and_then_rejected_sentence(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    response = ResponsePolicyProcessor(runtime)
    capture = Capture()
    response.push_frame = capture  # type: ignore[method-assign]
    await response.process_frame(LLMFullResponseStartFrame(), DOWN)
    rejected = "I hear you, clear value is important."
    await response.process_frame(LLMTextFrame(rejected), DOWN)
    await response.process_frame(LLMFullResponseEndFrame(), DOWN)
    assert len(capture.text) == 1
    assert capture.text[0] != rejected


class LocalEdgeTTS(EdgeTTSService):
    """Exercise Edge's installed Pipecat base without a voice service or decoder."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(reflex_cache_dir=tmp_path / "reflexes", phrase_aggregation=False)
        self.synthesizing = asyncio.Event()
        self.release = asyncio.Event()
        self.requests: list[str] = []
        self.contexts: dict[str, str] = {}

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        self.requests.append(text)
        self.contexts[text] = context_id
        if "pending" in text:
            self.synthesizing.set()
            await self.release.wait()
        yield _audio(context_id)


class AudioSink(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self.audio: asyncio.Queue[TTSAudioRawFrame] = asyncio.Queue()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame):
            self.audio.put_nowait(frame)
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_real_edge_tts_serialization_preserves_authorization(
    tmp_path: Path, interrupted: bool
) -> None:
    runtime = _runtime(tmp_path)
    caller = {"active": False}
    response = ResponsePolicyProcessor(runtime)
    response.enable_speech_floor_guard()
    tts = LocalEdgeTTS(tmp_path)
    guard = SpeechFloorGuard(
        caller_owns_floor=lambda: caller["active"], turn_epoch=lambda: runtime.turn_epoch
    )
    sink = AudioSink()
    worker = PipelineWorker(
        Pipeline([TranscriptionPolicyProcessor(runtime), response, tts, guard, sink]),
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000),
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_started(_worker: Any, _frame: Frame) -> None:
        started.set()

    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(started.wait(), 3)
        await worker.queue_frame(TTSSpeakFrame("Hello, welcome."))
        greeting = await asyncio.wait_for(sink.audio.get(), 3)
        assert greeting.context_id
        await worker.queue_frame(LLMFullResponseStartFrame())
        await worker.queue_frame(LLMTextFrame("Here is the pending answer."))
        await worker.queue_frame(LLMFullResponseEndFrame())
        await asyncio.wait_for(tts.synthesizing.wait(), 3)
        if interrupted:
            caller["active"] = True
            await worker.queue_frame(UserStartedSpeakingFrame())
            await worker.queue_frame(InterruptionFrame())
            # Wait for the system frames to reach the guard, rather than use a
            # timing sleep that could hide a serialization/cancellation race.
            for _ in range(100):
                if guard._authorized_epoch is None:
                    break
                await asyncio.sleep(0)
            assert guard._authorized_epoch is None
            caller["active"] = False
            await runtime.observe_transcription("Actually, I have another question.")
            await worker.queue_frame(TTSSpeakFrame("Yes, go ahead."))
        tts.release.set()
        answer = await asyncio.wait_for(sink.audio.get(), 3)
        assert answer.context_id != greeting.context_id
        expected = "Yes, go ahead." if interrupted else "Here is the pending answer."
        assert answer.context_id == tts.contexts[expected]
        await worker.stop_when_done()
        await asyncio.wait_for(task, 3)
        assert sink.audio.empty(), "a canceled answer must never precede or follow the fresh answer"
    finally:
        if not task.done():
            await worker.cancel()
            await asyncio.wait_for(task, 3)
