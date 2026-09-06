"""Cancellable Edge neural TTS adapter with continuous MP3 decoding.

``edge-tts`` streams audio bytes for a complete input phrase, but the service
only exposes MP3/WebM rather than telephone-ready PCM.  This adapter keeps one
MP3 decoder and one resampler alive for the whole phrase so arbitrary network
chunk boundaries never become audible gaps in the 16 kHz phone stream.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from collections import OrderedDict
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import edge_tts
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    TTSAudioRawFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import NOT_GIVEN, TTSSettings, _NotGiven

try:
    from pipecat.services.settings import assert_given  # type: ignore[attr-defined]
except ImportError:
    def assert_given(value: Any) -> Any:
        try:
            from pipecat.services.settings import NOT_GIVEN, is_given
            if not is_given(value) or value is NOT_GIVEN:
                return ""
        except Exception:
            pass
        return value if value is not None else ""
from pipecat.services.tts_service import TextAggregationMode, TTSService
from pipecat.utils.text.base_text_aggregator import (
    Aggregation,
    AggregationType,
    BaseTextAggregator,
)
from pipecat.utils.tracing.service_decorators import traced_tts

from .speech_floor_guard import SPEECH_RESPONSE_ID, SPEECH_TURN_EPOCH, SYNTHESIS_CONTEXT_ID
from .telemetry import emit_stage_latency

_SPEAKABLE_RE = re.compile(r"[\w\d]", re.UNICODE)

logger = logging.getLogger("PhoneAgentEdgeTTS")


def split_edge_phrases(text: str, *, min_chars: int, max_chars: int) -> list[str]:
    """Apply the live phrase aggregator's exact deterministic boundaries."""

    phrases: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        stripped = buffer.strip()
        if not stripped:
            continue
        terminal = char in ".!?\u3002\uff01\uff1f"
        soft_boundary = char in ",;:\uff0c\uff1b\uff1a" and len(stripped) >= min_chars
        length_boundary = len(stripped) >= max_chars and (
            char.isspace() or len(stripped) >= max_chars + 16
        )
        if terminal or soft_boundary or length_boundary:
            phrases.append(stripped)
            buffer = ""
    if buffer.strip():
        phrases.append(buffer.strip())
    return phrases


class EdgeCommunicator(Protocol):
    """Narrow interface used to make the network boundary testable."""

    def stream(self) -> AsyncGenerator[dict[str, Any], None]: ...


CommunicatorFactory = Callable[..., EdgeCommunicator]


class PhraseTextAggregator(BaseTextAggregator):
    """Release natural, bounded phrases from a streaming LLM response."""

    def __init__(self, *, min_chars: int = 12, max_chars: int = 60) -> None:
        if not 1 <= min_chars <= max_chars:
            raise ValueError("phrase min_chars must be positive and <= max_chars")
        super().__init__(aggregation_type=AggregationType.SENTENCE)
        self._min_chars = min_chars
        self._max_chars = max_chars
        self._buffer = ""

    @property
    def text(self) -> Aggregation:
        return Aggregation(text=self._buffer.strip(), type=AggregationType.SENTENCE)

    async def aggregate(self, text: str) -> AsyncGenerator[Aggregation, None]:
        for char in text:
            self._buffer += char
            stripped = self._buffer.strip()
            if not stripped:
                continue
            terminal = char in ".!?\u3002\uff01\uff1f"
            soft_boundary = char in ",;:\uff0c\uff1b\uff1a" and len(stripped) >= self._min_chars
            length_boundary = len(stripped) >= self._max_chars and (
                char.isspace() or len(stripped) >= self._max_chars + 16
            )
            if terminal or soft_boundary or length_boundary:
                result = stripped
                self._buffer = ""
                yield Aggregation(text=result, type=AggregationType.SENTENCE)

    async def flush(self) -> Aggregation | None:
        if not self._buffer.strip():
            await self.reset()
            return None
        result = self.text
        await self.reset()
        return result

    async def handle_interruption(self) -> None:
        await self.reset()

    async def reset(self) -> None:
        self._buffer = ""


@dataclass
class EdgeTTSSettings(TTSSettings):
    """Runtime settings accepted by the Edge neural speech endpoint."""

    rate: str | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    volume: str | _NotGiven = field(default_factory=lambda: NOT_GIVEN)
    pitch: str | _NotGiven = field(default_factory=lambda: NOT_GIVEN)


@dataclass
class _PrefetchedPCMStream:
    chunks: list[bytes] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    done: bool = False
    error: str | None = None

    def append(self, pcm: bytes) -> None:
        if self.done:
            return
        self.chunks.append(pcm)
        self.changed.set()

    def finish(self, error: str | None = None) -> None:
        if error is not None:
            self.error = error
        self.done = True
        self.changed.set()

    async def read(self) -> AsyncGenerator[bytes, None]:
        index = 0
        while True:
            while index < len(self.chunks):
                yield self.chunks[index]
                index += 1
            if self.done:
                return
            self.changed.clear()
            if index < len(self.chunks) or self.done:
                continue
            await self.changed.wait()


class FFmpegMP3StreamDecoder:
    """Decode one streamed MP3 phrase through a cancellable FFmpeg process."""

    def __init__(self, sample_rate: int, *, binary: str = "ffmpeg") -> None:
        self.sample_rate = sample_rate
        self.binary = binary
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        if self._process is not None:
            return
        self._process = await asyncio.create_subprocess_exec(
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(self.sample_rate),
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def write(self, data: bytes) -> None:
        process = self._require_process()
        if process.stdin is None or process.stdin.is_closing():
            raise RuntimeError("FFmpeg MP3 input is closed")
        process.stdin.write(data)
        await process.stdin.drain()

    async def close_input(self) -> None:
        process = self._require_process()
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
            await process.stdin.wait_closed()

    async def read(self, chunk_bytes: int = 4096) -> AsyncGenerator[bytes, None]:
        process = self._require_process()
        if process.stdout is None:
            raise RuntimeError("FFmpeg MP3 output is unavailable")
        pending = b""
        while chunk := await process.stdout.read(chunk_bytes):
            pending += chunk
            usable = len(pending) & ~1
            if usable:
                yield pending[:usable]
                pending = pending[usable:]
        if pending:
            raise RuntimeError("FFmpeg returned a partial PCM16 sample")

    async def wait(self) -> None:
        process = self._require_process()
        return_code = await process.wait()
        if return_code:
            error = b""
            if process.stderr is not None:
                error = await process.stderr.read(8192)
            detail = error.decode(errors="replace").strip()[:2000]
            raise RuntimeError(f"FFmpeg MP3 decoder exited {return_code}: {detail}")

    async def cancel(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise RuntimeError("FFmpeg MP3 decoder is not started")
        return self._process


class EdgeTTSService(TTSService):
    """Pipecat TTS service for Microsoft's online Edge neural voices."""

    Settings = EdgeTTSSettings
    _settings: EdgeTTSSettings

    def __init__(
        self,
        *,
        voice: str = "en-US-EmmaMultilingualNeural",
        rate: str = "+0%",
        volume: str = "+0%",
        pitch: str = "+0Hz",
        sample_rate: int = 16_000,
        connect_timeout_secs: int = 5,
        receive_timeout_secs: int = 20,
        live_attempts: int = 3,
        first_audio_timeout_secs: float = 3.0,
        audio_idle_timeout_secs: float = 2.0,
        synthesis_timeout_secs: float = 15.0,
        ffmpeg_binary: str = "ffmpeg",
        text_aggregation_mode: TextAggregationMode = TextAggregationMode.SENTENCE,
        phrase_aggregation: bool = True,
        phrase_min_chars: int = 8,
        phrase_max_chars: int = 72,
        reflex_cache_dir: Path | None = None,
        communicator_factory: CommunicatorFactory = edge_tts.Communicate,
        **kwargs: Any,
    ) -> None:
        if min(first_audio_timeout_secs, audio_idle_timeout_secs, synthesis_timeout_secs) <= 0:
            raise ValueError("TTS synthesis deadlines must be positive")
        self._first_audio_timeout_secs = first_audio_timeout_secs
        self._audio_idle_timeout_secs = audio_idle_timeout_secs
        self._synthesis_timeout_secs = synthesis_timeout_secs
        # The generator owns stall deadlines. Pipecat must not silently close
        # its context before that generator reports failure and cleans up.
        kwargs.setdefault("stop_frame_timeout_s", synthesis_timeout_secs + 3.0)
        settings = self.Settings(
            model="edge-online-neural",
            voice=voice,
            language=None,
            rate=rate,
            volume=volume,
            pitch=pitch,
        )
        super().__init__(
            sample_rate=sample_rate,
            text_aggregation_mode=text_aggregation_mode,
            push_start_frame=True,
            push_stop_frames=True,
            settings=settings,
            **kwargs,
        )
        self._connect_timeout_secs = connect_timeout_secs
        self._receive_timeout_secs = receive_timeout_secs
        if not 1 <= live_attempts <= 3:
            raise ValueError("live_attempts must be between 1 and 3")
        self._live_attempts = live_attempts
        self._ffmpeg_binary = ffmpeg_binary
        self._target_sample_rate = sample_rate
        self._communicator_factory = communicator_factory
        self._phrase_aggregation = phrase_aggregation
        self._phrase_min_chars = phrase_min_chars
        self._phrase_max_chars = phrase_max_chars
        self._prefetch_cache: dict[str, bytes] = {}
        self._greeting_cache: dict[str, bytes] = {}
        self._latency_sink: Any = None
        self._failed_synthesis_contexts: OrderedDict[str, None] = OrderedDict()
        self._synthesis_owners: OrderedDict[str, tuple[Any, Any]] = OrderedDict()
        self._speech_owner: tuple[Any, Any] = (None, None)
        self._prefetch_phrase_tasks: dict[str, asyncio.Task[bytes]] = {}
        self._prefetch_streams: dict[str, _PrefetchedPCMStream] = {}
        self._reflex_cache_dir = reflex_cache_dir or (
            Path.home() / ".cache" / "phone-agent" / "reflexes"
        )
        self._reflex_cache: dict[str, bytes] = {}
        self._reflex_tasks: dict[str, asyncio.Task[bytes]] = {}
        if phrase_aggregation:
            self._text_aggregator = PhraseTextAggregator(
                min_chars=phrase_min_chars,
                max_chars=phrase_max_chars,
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if direction is FrameDirection.DOWNSTREAM:
            if isinstance(frame, InterruptionFrame):
                self._speech_owner = (None, None)
            elif isinstance(frame, LLMFullResponseStartFrame | TTSSpeakFrame):
                self._speech_owner = (frame.metadata.get(SPEECH_TURN_EPOCH), frame.metadata.get(SPEECH_RESPONSE_ID))
        await super().process_frame(frame, direction)

    async def on_turn_context_completed(self) -> None:
        # Edge always finishes its HTTP-style generator synchronously. Pipecat
        # otherwise mistakes zero-PCM failures for asynchronous websocket TTS
        # and holds later contexts behind its idle timeout.
        self._is_yielding_frames_synchronously = True
        await super().on_turn_context_completed()

    async def create_audio_context(self, context_id: str) -> None:
        self._synthesis_owners[context_id] = self._speech_owner
        self._synthesis_owners.move_to_end(context_id)
        while len(self._synthesis_owners) > 256:
            self._synthesis_owners.popitem(last=False)
        await super().create_audio_context(context_id)

    def _tag_synthesis_error(self, frame: ErrorFrame, context_id: str) -> ErrorFrame:
        epoch, response_id = self._synthesis_owners.get(context_id, self._speech_owner)
        frame.processor = self
        frame.metadata[SYNTHESIS_CONTEXT_ID] = context_id
        frame.metadata[SPEECH_TURN_EPOCH] = epoch
        frame.metadata[SPEECH_RESPONSE_ID] = response_id
        return frame

    def _fail_synthesis_context(self, context_id: str) -> None:
        self._failed_synthesis_contexts[context_id] = None
        self._failed_synthesis_contexts.move_to_end(context_id)
        while len(self._failed_synthesis_contexts) > 256:
            self._failed_synthesis_contexts.popitem(last=False)

    async def push_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if isinstance(frame, TTSStartedFrame) and frame.context_id in self._synthesis_owners:
            epoch, response_id = self._synthesis_owners[frame.context_id]
            if isinstance(response_id, str):
                frame.metadata[SPEECH_RESPONSE_ID] = response_id
                frame.metadata[SPEECH_TURN_EPOCH] = epoch
        if (direction is FrameDirection.DOWNSTREAM
                and isinstance(frame, TTSTextFrame | TTSAudioRawFrame)
                and frame.context_id in self._failed_synthesis_contexts):
            # Pipecat emits full text after a generator finishes, including
            # one that yielded ErrorFrame. Never promote that text to heard speech.
            return
        await super().push_frame(frame, direction)

    def set_latency_sink(self, sink: Any) -> None:
        self._latency_sink = sink

    def clear_prefetch(self) -> None:
        for task in self._prefetch_phrase_tasks.values():
            if not task.done():
                task.cancel()
        for stream in self._prefetch_streams.values():
            stream.finish("Speculative synthesis cancelled")
        self._prefetch_phrase_tasks.clear()
        self._prefetch_streams.clear()
        self._prefetch_cache.clear()

    async def prefetch_greeting(self, text: str) -> None:
        """Synthesize and preserve opening greeting audio in dedicated memory cache."""
        await self.prefetch_text(text, is_greeting=True)

    async def prefetch_text(self, text: str, *, is_greeting: bool = False) -> None:
        """Synthesize one speculative response without emitting audio."""

        clean_text = text.strip()
        if not clean_text or clean_text in self._greeting_cache or clean_text in self._prefetch_cache:
            return

        phrases = (
            split_edge_phrases(
                text,
                min_chars=self._phrase_min_chars,
                max_chars=self._phrase_max_chars,
            )
            if self._phrase_aggregation
            else [clean_text]
        )
        tasks: list[asyncio.Task[bytes]] = []
        for phrase in phrases:
            if (
                not phrase
                or not _SPEAKABLE_RE.search(phrase)
                or phrase in self._greeting_cache
                or phrase in self._prefetch_cache
            ):
                continue
            task = self._prefetch_phrase_tasks.get(phrase)
            if task is None:
                stream = _PrefetchedPCMStream()
                self._prefetch_streams[phrase] = stream
                task = asyncio.create_task(
                    self._synthesize_prefetch_pcm(phrase, stream),
                    name="phoneagent-speculative-edge-tts",
                )
                self._prefetch_phrase_tasks[phrase] = task
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks)
        if self._phrase_aggregation and len(phrases) > 1:
            full_pcm = b"".join(
                self._greeting_cache.get(p) or self._prefetch_cache.get(p, b"")
                for p in phrases
            )
            if full_pcm:
                self._prefetch_cache[clean_text] = full_pcm

        if is_greeting:
            if clean_text in self._prefetch_cache:
                self._greeting_cache[clean_text] = self._prefetch_cache[clean_text]
            for phrase in phrases:
                if phrase in self._prefetch_cache:
                    self._greeting_cache[phrase] = self._prefetch_cache[phrase]

    async def _synthesize_prefetch_pcm(
        self,
        phrase: str,
        stream: _PrefetchedPCMStream,
    ) -> bytes:
        try:
            async with asyncio.timeout(self._synthesis_timeout_secs):
                pcm = await self._decode_edge_pcm(phrase, stream)
            if pcm and self._prefetch_streams.get(phrase) is stream and not stream.error:
                self._prefetch_cache[phrase] = pcm
            if not pcm:
                stream.finish("Speculative synthesis produced no complete audio")
            return pcm
        except asyncio.CancelledError:
            stream.finish("Speculative synthesis cancelled")
            raise
        except TimeoutError:
            stream.finish("Speculative synthesis deadline exceeded")
            return b""
        finally:
            stream.finish()
            if self._prefetch_streams.get(phrase) is stream:
                self._prefetch_phrase_tasks.pop(phrase, None)
                if stream.error:
                    self._prefetch_streams.pop(phrase, None)

    async def _decode_edge_pcm(
        self, phrase: str, stream: _PrefetchedPCMStream | None = None,
    ) -> bytes:
        try:
            async with asyncio.timeout(self._synthesis_timeout_secs):
                return await self._decode_edge_pcm_impl(phrase, stream)
        except TimeoutError:
            if stream is not None:
                stream.finish("Speculative synthesis deadline exceeded")
            return b""

    async def _decode_edge_pcm_impl(
        self,
        phrase: str,
        stream: _PrefetchedPCMStream | None = None,
    ) -> bytes:
        voice = assert_given(self._settings.voice)
        rate = assert_given(self._settings.rate)
        volume = assert_given(self._settings.volume)
        pitch = assert_given(self._settings.pitch)
        decoder = FFmpegMP3StreamDecoder(self._target_sample_rate, binary=self._ffmpeg_binary)
        writer_task: asyncio.Task[None] | None = None
        try:
            communicator = self._communicator_factory(
                text=phrase,
                voice=voice,
                rate=rate,
                volume=volume,
                pitch=pitch,
                boundary="SentenceBoundary",
                connect_timeout=self._connect_timeout_secs,
                receive_timeout=self._receive_timeout_secs,
            )
            await decoder.start()

            async def write_mp3_stream() -> None:
                try:
                    async for chunk in communicator.stream():
                        if chunk.get("type") == "audio" and chunk.get("data"):
                            await decoder.write(chunk["data"])
                finally:
                    await decoder.close_input()

            writer_task = asyncio.create_task(write_mp3_stream())
            chunks: list[bytes] = []
            async for chunk in decoder.read():
                chunks.append(chunk)
                if stream is not None:
                    stream.append(chunk)
            pcm = b"".join(chunks)
            await writer_task
            await decoder.wait()
            return pcm
        except asyncio.CancelledError:
            if writer_task is not None:
                writer_task.cancel()
                await asyncio.gather(writer_task, return_exceptions=True)
            await decoder.cancel()
            raise
        except Exception:
            if writer_task is not None and not writer_task.done():
                writer_task.cancel()
                await asyncio.gather(writer_task, return_exceptions=True)
            await decoder.cancel()
            logger.debug("Edge TTS background synthesis failed", exc_info=True)
            return b""

    async def synthesize_pcm(self, text: str) -> bytes:
        """Render telephone-ready PCM for another provider's safe fallback path."""

        phrase = text.strip()
        if not phrase or not _SPEAKABLE_RE.search(phrase):
            return b""
        return await self._decode_edge_pcm(phrase)

    def has_ready_speculative_audio(self) -> bool:
        """Return true only when substantive speculative PCM can start now."""

        if any(self._greeting_cache.values()) or any(self._prefetch_cache.values()):
            return True
        return any(stream.chunks and not stream.error for stream in self._prefetch_streams.values())

    def get_reflex_pcm(self, phrase: str) -> bytes | None:
        """Read a voice/settings-specific persistent reflex without network I/O."""

        cached = self._reflex_cache.get(phrase)
        if cached:
            return cached
        path = self._reflex_path(phrase)
        try:
            pcm = path.read_bytes()
        except OSError:
            return None
        maximum = self._target_sample_rate * 2 * 5
        if not pcm or len(pcm) % 2 or len(pcm) > maximum:
            return None
        self._reflex_cache[phrase] = pcm
        return pcm

    async def warm_reflexes(self, phrases: tuple[str, ...]) -> None:
        """Populate reusable Andrew PCM; failures leave the normal path untouched."""

        tasks: list[asyncio.Task[bytes]] = []
        for phrase in phrases:
            phrase = phrase.strip()
            if not phrase or self.get_reflex_pcm(phrase):
                continue
            task = self._reflex_tasks.get(phrase)
            if task is None:
                task = asyncio.create_task(
                    self._warm_reflex_phrase(phrase),
                    name="phoneagent-edge-reflex-warmup",
                )
                self._reflex_tasks[phrase] = task
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks)

    async def _warm_reflex_phrase(self, phrase: str) -> bytes:
        try:
            pcm = await self._decode_edge_pcm(phrase)
            if not pcm:
                return b""
            pcm = self._trim_reflex_pcm(pcm)
            if not pcm:
                return b""
            self._reflex_cache[phrase] = pcm
            path = self._reflex_path(phrase)
            await asyncio.to_thread(self._write_reflex_atomic, path, pcm)
            return pcm
        finally:
            self._reflex_tasks.pop(phrase, None)

    def _reflex_path(self, phrase: str) -> Path:
        settings_key = "\0".join(
            (
                "reflex-v2-trimmed",
                str(assert_given(self._settings.voice)),
                str(assert_given(self._settings.rate)),
                str(assert_given(self._settings.volume)),
                str(assert_given(self._settings.pitch)),
                str(self._target_sample_rate),
                phrase,
            )
        )
        digest = hashlib.sha256(settings_key.encode("utf-8")).hexdigest()[:24]
        return self._reflex_cache_dir / f"{digest}.pcm"

    def _trim_reflex_pcm(self, pcm: bytes) -> bytes:
        """Remove provider padding while preserving a natural speech tail."""

        if len(pcm) < 2 or len(pcm) % 2:
            return b""
        samples = memoryview(pcm).cast("h")
        threshold = 80
        first = next((index for index, value in enumerate(samples) if abs(value) > threshold), None)
        if first is None:
            return b""
        last = next(
            index
            for index in range(len(samples) - 1, first - 1, -1)
            if abs(samples[index]) > threshold
        )
        leading_padding = int(self._target_sample_rate * 0.04)
        trailing_padding = int(self._target_sample_rate * 0.10)
        start = max(0, first - leading_padding)
        end = min(len(samples), last + trailing_padding + 1)
        return pcm[start * 2 : end * 2]

    @staticmethod
    def _write_reflex_atomic(path: Path, pcm: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_bytes(pcm)
        temporary.replace(path)

    async def cleanup(self) -> None:
        self.clear_prefetch()
        self._greeting_cache.clear()
        tasks = tuple(self._reflex_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._reflex_tasks.clear()
        await super().cleanup()

    def can_generate_metrics(self) -> bool:
        return True

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        if context_id in self._failed_synthesis_contexts:
            return
        started = time.perf_counter()
        first_pcm_ms: float | None = None
        output_bytes = 0
        outcome = "complete"
        iterator = self._run_tts_impl(text, context_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._synthesis_timeout_secs
        progress_deadline = loop.time() + self._first_audio_timeout_secs
        try:
            while True:
                try:
                    # Scope cancellation to fetching the next frame. A timeout
                    # context spanning yield would cancel the downstream caller.
                    async with asyncio.timeout_at(min(deadline, progress_deadline)):
                        frame = await anext(iterator)
                except StopAsyncIteration:
                    break
                if isinstance(frame, TTSAudioRawFrame):
                    output_bytes += len(frame.audio)
                    progress_deadline = loop.time() + self._audio_idle_timeout_secs
                    if first_pcm_ms is None:
                        first_pcm_ms = (time.perf_counter() - started) * 1000
                        await emit_stage_latency(
                            self._latency_sink, "edge_tts", "first_pcm", started,
                            context_id=context_id, text_chars=len(text),
                        )
                elif isinstance(frame, ErrorFrame):
                    outcome = "error"
                    self._fail_synthesis_context(context_id)
                    frame = self._tag_synthesis_error(frame, context_id)
                yield frame
        except (asyncio.CancelledError, GeneratorExit):
            outcome = "cancelled"
            raise
        except Exception as exc:
            outcome = "error"
            self._fail_synthesis_context(context_id)
            yield self._tag_synthesis_error(
                ErrorFrame(error=f"Edge TTS synthesis failed: {type(exc).__name__}"), context_id,
            )
        finally:
            await iterator.aclose()
            if outcome != "complete":
                task = self._prefetch_phrase_tasks.get(text.strip())
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            await emit_stage_latency(
                self._latency_sink, "edge_tts", "synthesis_complete", started,
                context_id=context_id, text_chars=len(text), first_pcm_ms=first_pcm_ms,
                pcm_bytes=output_bytes, outcome=outcome,
            )

    async def _run_tts_impl(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        phrase = text.strip()
        if not phrase or not _SPEAKABLE_RE.search(phrase):
            return

        prefetched = self._greeting_cache.get(phrase) or self._prefetch_cache.get(phrase)
        if not prefetched and self._phrase_aggregation:
            chunks = split_edge_phrases(
                phrase,
                min_chars=self._phrase_min_chars,
                max_chars=self._phrase_max_chars,
            )
            if len(chunks) > 1 and all((c in self._greeting_cache or c in self._prefetch_cache) for c in chunks):
                prefetched = b"".join(
                    self._greeting_cache.get(c) or self._prefetch_cache.get(c, b"")
                    for c in chunks
                )
                self._greeting_cache[phrase] = prefetched

        prefetched_stream = self._prefetch_streams.get(phrase)
        if prefetched:
            await self.start_tts_usage_metrics(phrase)
            await self.stop_ttfb_metrics()
            logger.info("speculative Edge TTS cache hit chars=%d (greeting_cache=%s)", len(phrase), phrase in self._greeting_cache)
            for offset in range(0, len(prefetched), 4096):
                yield TTSAudioRawFrame(
                    audio=prefetched[offset : offset + 4096],
                    sample_rate=self._target_sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
            return
        if prefetched_stream is not None:
            await self.start_tts_usage_metrics(phrase)
            emitted_audio = False
            async for pcm in prefetched_stream.read():
                if not emitted_audio:
                    await self.stop_ttfb_metrics()
                    emitted_audio = True
                    logger.info("attached to speculative Edge TTS stream chars=%d", len(phrase))
                yield TTSAudioRawFrame(
                    audio=pcm,
                    sample_rate=self._target_sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
            if emitted_audio:
                if prefetched_stream.error:
                    yield ErrorFrame(error=prefetched_stream.error)
                return

        voice = assert_given(self._settings.voice)
        rate = assert_given(self._settings.rate)
        volume = assert_given(self._settings.volume)
        pitch = assert_given(self._settings.pitch)
        if not all(isinstance(value, str) and value for value in (voice, rate, volume, pitch)):
            yield ErrorFrame(error="Edge TTS voice and prosody settings must be configured")
            return

        await self.start_tts_usage_metrics(phrase)
        emitted_audio = False
        last_error = "Edge TTS completed without decodable audio"
        try:
            for attempt in range(1, self._live_attempts + 1):
                decoder = FFmpegMP3StreamDecoder(
                    self._target_sample_rate,
                    binary=self._ffmpeg_binary,
                )
                writer_task: asyncio.Task[None] | None = None
                attempt_emitted_audio = False
                try:
                    communicator = self._communicator_factory(
                        text=phrase,
                        voice=voice,
                        rate=rate,
                        volume=volume,
                        pitch=pitch,
                        boundary="SentenceBoundary",
                        connect_timeout=self._connect_timeout_secs,
                        receive_timeout=self._receive_timeout_secs,
                    )
                    await decoder.start()

                    async def write_mp3_stream(
                        active_communicator: EdgeCommunicator = communicator,
                        active_decoder: FFmpegMP3StreamDecoder = decoder,
                    ) -> None:
                        try:
                            async for chunk in active_communicator.stream():
                                if chunk.get("type") != "audio":
                                    continue
                                data = chunk.get("data")
                                if not isinstance(data, bytes):
                                    raise TypeError("Edge TTS returned a non-bytes audio chunk")
                                if data:
                                    await active_decoder.write(data)
                        finally:
                            await active_decoder.close_input()

                    writer_task = asyncio.create_task(write_mp3_stream())
                    async for pcm in decoder.read():
                        if not emitted_audio:
                            await self.stop_ttfb_metrics()
                            emitted_audio = True
                        attempt_emitted_audio = True
                        yield TTSAudioRawFrame(
                            audio=pcm,
                            sample_rate=self._target_sample_rate,
                            num_channels=1,
                            context_id=context_id,
                        )
                    await writer_task
                    await decoder.wait()
                    if attempt_emitted_audio:
                        return
                    last_error = "Edge TTS completed without decodable audio"
                except (asyncio.CancelledError, GeneratorExit):
                    if writer_task is not None:
                        writer_task.cancel()
                        await asyncio.gather(writer_task, return_exceptions=True)
                    await decoder.cancel()
                    raise
                except Exception as exc:
                    if writer_task is not None and not writer_task.done():
                        writer_task.cancel()
                        await asyncio.gather(writer_task, return_exceptions=True)
                    await decoder.cancel()
                    last_error = f"Edge TTS failed: {type(exc).__name__}: {exc}"
                    if attempt_emitted_audio:
                        logger.error(
                            "Edge TTS stream failed after audio started attempt=%d/%d error=%s",
                            attempt,
                            self._live_attempts,
                            last_error,
                        )
                        yield ErrorFrame(error=last_error)
                        return

                if attempt < self._live_attempts:
                    logger.warning(
                        "Edge TTS live attempt failed attempt=%d/%d chars=%d error=%s; retrying",
                        attempt,
                        self._live_attempts,
                        len(phrase),
                        last_error,
                    )
                    await asyncio.sleep(0.08 if attempt == 1 else 0.18)

            logger.error(
                "Edge TTS live retries exhausted attempts=%d chars=%d error=%s",
                self._live_attempts,
                len(phrase),
                last_error,
            )
            yield ErrorFrame(error=last_error)
        except (asyncio.CancelledError, GeneratorExit):
            raise
        finally:
            await self.stop_ttfb_metrics()
