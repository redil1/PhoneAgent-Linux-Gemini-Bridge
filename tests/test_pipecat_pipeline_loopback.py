"""Real PipelineWorker loopback for the framed PhoneAgent transport."""

from __future__ import annotations

import asyncio
import time

import pytest
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InterruptionWorkerFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

from phone_agent_gateway.ai_bridge.media_protocol import FrameDirection as WireDirection
from phone_agent_gateway.ai_bridge.media_protocol import FrameKind, MediaFrame
from phone_agent_gateway.ai_bridge.pipecat_transport import (
    PhoneAgentTransport,
    PhoneAgentTransportParams,
)
from phone_agent_gateway.ai_bridge.session import SessionPhase


class ProbeSink(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self.input_frames: asyncio.Queue[InputAudioRawFrame] = asyncio.Queue()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            await self.input_frames.put(frame)
            return
        await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_pipeline_worker_routes_framed_duplex_and_interruption() -> None:
    transport = PhoneAgentTransport(
        PhoneAgentTransportParams(
            audio_in_sample_rate=16_000,
            audio_out_sample_rate=16_000,
        )
    )
    transport.session.set_phase(SessionPhase.CONNECTING)
    transport.session.set_phase(SessionPhase.ACTIVE)
    sent: asyncio.Queue[tuple[bytes, int, int]] = asyncio.Queue()
    ended: asyncio.Queue[tuple[int, int]] = asyncio.Queue()
    flushed: asyncio.Queue[int] = asyncio.Queue()
    transport.set_tx_handler(
        lambda payload, generation, sequence: sent.put_nowait(
            (payload, generation, sequence)
        )
    )
    transport.set_audio_end_handler(
        lambda generation, sequence: ended.put_nowait((generation, sequence))
    )
    transport.set_flush_handler(
        lambda advance: flushed.put_nowait(advance.next_generation)
    )

    sink = ProbeSink()
    worker = PipelineWorker(
        Pipeline([transport.input(), sink, transport.output()]),
        params=PipelineParams(audio_in_sample_rate=16_000, audio_out_sample_rate=16_000),
        enable_rtvi=False,
    )
    started = asyncio.Event()

    @worker.event_handler("on_pipeline_started")
    async def on_pipeline_started(_worker, _frame) -> None:
        started.set()

    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)
    runner_task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        caller_payload = b"\x01\x00" * 320
        transport.feed_phone_frame(
            MediaFrame(
                kind=FrameKind.AUDIO,
                direction=WireDirection.PHONE_TO_MAC,
                call_id=transport.session.call_id,
                generation_id=1,
                sequence=0,
                monotonic_ns=time.monotonic_ns(),
                payload=caller_payload,
                sample_rate=16_000,
                channels=1,
                sample_width=2,
            )
        )
        caller_frame = await asyncio.wait_for(sink.input_frames.get(), timeout=2)
        assert caller_frame.audio == caller_payload

        agent_payload = b"\x02\x00" * 320
        await worker.queue_frame(
            OutputAudioRawFrame(audio=agent_payload, sample_rate=16_000, num_channels=1)
        )
        sent_payload, generation, sequence = await asyncio.wait_for(sent.get(), timeout=2)
        assert (sent_payload, generation, sequence) == (agent_payload, 1, 0)

        await worker.queue_frame(
            TTSStartedFrame(context_id="test-tts")
        )
        await worker.queue_frame(
            TTSAudioRawFrame(
                audio=agent_payload,
                sample_rate=16_000,
                num_channels=1,
                context_id="test-tts",
            )
        )
        await worker.queue_frame(TTSStoppedFrame(context_id="test-tts"))
        _tts_payload, tts_generation, tts_sequence = await asyncio.wait_for(
            sent.get(), timeout=2
        )
        assert (tts_generation, tts_sequence) == (1, 1)
        assert await asyncio.wait_for(ended.get(), timeout=2) == (1, 2)

        await worker.queue_frame(TTSStartedFrame(context_id="empty-tts"))
        await worker.queue_frame(TTSStoppedFrame(context_id="empty-tts"))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ended.get(), timeout=0.1)

        await worker.queue_frame(InterruptionWorkerFrame())
        assert await asyncio.wait_for(flushed.get(), timeout=2) == 2
        assert transport.session.generation_id == 2

        transport.feed_phone_frame(
            MediaFrame(
                kind=FrameKind.AUDIO,
                direction=WireDirection.PHONE_TO_MAC,
                call_id=transport.session.call_id,
                generation_id=1,
                sequence=1,
                monotonic_ns=time.monotonic_ns(),
                payload=caller_payload,
                sample_rate=16_000,
                channels=1,
                sample_width=2,
            )
        )
        assert transport.session.metrics.stale_input_frames == 1
    finally:
        await worker.stop_when_done()
        await asyncio.wait_for(runner_task, timeout=3)


@pytest.mark.asyncio
async def test_output_transport_accumulates_without_midstream_silence_padding() -> None:
    transport = PhoneAgentTransport(
        PhoneAgentTransportParams(
            audio_in_sample_rate=16_000,
            audio_out_sample_rate=16_000,
        )
    )
    transport.session.set_phase(SessionPhase.CONNECTING)
    transport.session.set_phase(SessionPhase.ACTIVE)
    sent_chunks: list[bytes] = []
    transport.set_tx_handler(
        lambda payload, gen, seq: sent_chunks.append(payload)
    )

    output = transport.output()

    # Send 1000 bytes (e.g. from an arbitrary decoder buffer)
    # Byte values 1 to 200 repeated
    raw_1000 = bytes(i % 250 + 1 for i in range(1000))
    await output.write_audio_frame(OutputAudioRawFrame(audio=raw_1000, sample_rate=16_000, num_channels=1))

    # Exactly 1 frame of 640 bytes should have been emitted; 360 bytes remain buffered
    assert len(sent_chunks) == 1
    assert len(sent_chunks[0]) == 640
    assert sent_chunks[0] == raw_1000[:640]

    # Now send another 280 bytes (360 + 280 = 640)
    raw_280 = bytes((i + 1000) % 250 + 1 for i in range(280))
    await output.write_audio_frame(OutputAudioRawFrame(audio=raw_280, sample_rate=16_000, num_channels=1))

    # Second frame emitted: 360 bytes from first + 280 from second = 640 bytes with ZERO zeros!
    assert len(sent_chunks) == 2
    assert len(sent_chunks[1]) == 640
    assert sent_chunks[1] == raw_1000[640:] + raw_280
    assert b"\x00" not in sent_chunks[1]

    # Now finish segment: if accumulator empty, no extra padded chunk
    await output.finish_audio_segment()
    assert len(sent_chunks) == 2

    # Now send partial chunk 500 bytes and finish segment
    raw_500 = b"\x05" * 500
    await output.write_audio_frame(OutputAudioRawFrame(audio=raw_500, sample_rate=16_000, num_channels=1))
    assert len(sent_chunks) == 2  # Not yet emitted because < 640

    await output.finish_audio_segment()
    assert len(sent_chunks) == 3
    assert len(sent_chunks[2]) == 640
    assert sent_chunks[2][:500] == raw_500
    assert sent_chunks[2][500:] == b"\x00" * 140

