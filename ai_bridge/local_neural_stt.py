"""Local decoder behind the same acoustic turn/floor controller as cloud STT.

Local inference is speculative: a decoded snapshot becomes authoritative only
if no newer speech arrived. CPU/GPU work never blocks incoming VAD/barge-in.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

from pipecat.frames.frames import Frame, InterimTranscriptionFrame, StartFrame
from pipecat.services.stt_service import STTService

from .antigravity_live_stt import AntigravityLiveSTTService
from .parakeet_local_stt import STTHypothesis, load_model_async, transcribe_pcm_async
from .telemetry import emit_stage_latency


class LocalNeuralSTTService(AntigravityLiveSTTService):
    commits_transcripts_externally = True

    def __init__(self, *, model: str, decode_language: str | None = None, **kwargs: Any):
        super().__init__(**kwargs)
        self._decode_silence_sec = 0.30
        self._decode_timeout_secs = 20.0
        self._model_id = model
        self._decode_language = decode_language or self._language
        self._local_pcm = bytearray()
        self._local_speech_end = 0
        self._decode_task: asyncio.Task[None] | None = None
        self._decoded_signature: tuple[int, float] | None = None
        self._local_hypothesis = STTHypothesis(text="", trusted_for_task=False)
        self._decode_pcm = transcribe_pcm_async
        self._latency_sink: Any = None
        self._max_local_bytes = 120 * 16000 * 2
        self._overflow = False

    @property
    def turn_stop_watchdog_timeout_secs(self) -> float:
        # The aggregator's fallback must not finish a turn while its owned
        # decoder can still return a valid result. Normal commits remain fast.
        # At the last speech edge, one stale decode may still occupy the
        # single executor before the current snapshot gets its own deadline.
        return 2 * self._decode_timeout_secs + max(self._incomplete_endpoint_sec, self._fallback_endpoint_sec) + 1.0

    def set_latency_sink(self, sink: Any) -> None:
        self._latency_sink = sink

    async def start(self, frame: StartFrame) -> None:
        # StartFrame bookkeeping is shared; cloud bridge discovery is not.
        await STTService.start(self, frame)
        self._reset_turn_state()
        self._local_pcm.clear()
        self._local_speech_end = 0
        self._decoded_signature = None
        self._overflow = False
        try:
            await load_model_async(self._model_id)
        except Exception:
            await self.push_error(error_msg="Local speech recognition model could not start", fatal=True)
            raise
        self._watchdog_task = asyncio.create_task(
            self._silence_watchdog_loop(), name="local-neural-endpoint"
        )

    async def _close_session(self) -> None:
        self._is_closing = True
        task, self._decode_task = self._decode_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._local_pcm.clear()
        await super()._close_session()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        if not audio or self._is_closing:
            return
        self._local_pcm.extend(audio)
        try:
            async for frame in super().run_stt(audio):
                yield frame
        finally:
            # This adapter owns PCM locally; no bridge session/sender exists.
            self._audio_buffer.clear()
        if self._acoustic_active:
            self._local_speech_end = len(self._local_pcm)
        if not self._floor_claimed and not self._last_transcript and not self._acoustic_active:
            # Noisy, quiet onset words can precede confirmed VAD by much more
            # than250ms. Preserve them for the full-utterance local decoder.
            keep = int(self._target_sample_rate * 2 * self.recognition_preroll_secs)
            if len(self._local_pcm) > keep:
                del self._local_pcm[:-keep]
            self._local_speech_end = 0
        if len(self._local_pcm) > self._max_local_bytes:
            self._overflow = True
            self._local_pcm.clear()
            self._last_transcript = self._final_transcript = ""
            self._decoded_signature = None
            await self.push_error(
                error_msg="Local speech buffer exceeded 120 seconds; utterance was not committed"
            )

    def _signature(self) -> tuple[int, float]:
        return self._speech_epoch, self._last_speech_at

    def _can_speculate(self) -> bool:
        return self._local_hypothesis.trusted_for_task and super()._can_speculate()

    async def _watchdog_tick(self) -> None:
        if self._is_closing:
            return
        if self._overflow:
            # Let the shared empty-turn recovery resolve this explicitly; never
            # transcribe a truncated buffer as if it were the complete request.
            await super()._watchdog_tick()
            if not self._floor_claimed:
                self._overflow = False
            return
        signature = self._signature()
        if self._decode_task is not None and not self._decode_task.done():
            return
        if (
            self._floor_claimed
            and self._local_speech_end
            and self._silence_elapsed() >= max(self._decode_silence_sec, self._speculative_prefetch_silence_sec)
            and self._decoded_signature != signature
        ):
            end = min(len(self._local_pcm), self._local_speech_end + 3840)
            snapshot = bytes(self._local_pcm[:end])
            self._decode_task = asyncio.create_task(
                self._decode(snapshot, signature), name="local-neural-decode"
            )
            return
        await super()._watchdog_tick()

    async def _decode(self, snapshot: bytes, signature: tuple[int, float]) -> None:
        started = time.perf_counter()
        outcome = "discarded"
        try:
            async with asyncio.timeout(self._decode_timeout_secs):
                hypothesis = await self._decode_pcm(snapshot, self._model_id, self._decode_language)
            if (
                self._is_closing
                or self._overflow
                or signature != self._signature()
                or self._acoustic_active
            ):
                return
            self._decoded_signature = signature
            self._local_hypothesis = hypothesis
            # Each local result covers this entire buffered utterance. It is
            # not another continuous-provider segment to append to a prefix.
            self._last_transcript = self._final_transcript = ""
            self._last_provider_text = ""
            self._retired_provider_pending_final = False
            if hypothesis.text:
                candidate = self._stage_transcription(hypothesis.text, is_final=True)
                if candidate:
                    await self.push_frame(
                        InterimTranscriptionFrame(text=candidate, user_id="caller", timestamp="")
                    )
            outcome = "ready" if hypothesis.text else "empty"
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            self._decoded_signature = signature
            self._last_transcript = self._final_transcript = ""
            self._local_hypothesis = STTHypothesis(text="", trusted_for_task=False)
            outcome = "error"
            await self.push_error(error_msg=f"Local recognition failed: {type(exc).__name__}")
        finally:
            await emit_stage_latency(
                self._latency_sink,
                "local_stt",
                "decode",
                started,
                outcome=outcome,
                audio_ms=len(snapshot) / 32,
                model=self._model_id,
            )

    def _recognition_metadata(self) -> dict[str, Any]:
        h = self._local_hypothesis
        return {
            **h.diagnostics,
            "language": h.language,
            "confidence": h.confidence,
            "trusted_for_task": h.trusted_for_task,
        }

    async def _commit_pending_transcript(self, **kwargs: Any) -> None:
        if self._decoded_signature != self._signature() or self._overflow:
            return
        before = self._last_committed_speech_epoch
        consumed = len(self._local_pcm)
        await super()._commit_pending_transcript(**kwargs)
        if self._last_committed_speech_epoch != before:
            # Input may resume while downstream publication awaits. Remove only
            # the old prefix, preserving any newly captured onset bytes.
            del self._local_pcm[:consumed]
            self._local_speech_end = max(0, self._local_speech_end - consumed)
