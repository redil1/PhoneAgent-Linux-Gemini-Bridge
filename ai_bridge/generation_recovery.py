"""Ordered terminal-generation failures, distinct from transport/TTS errors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from itertools import count
from typing import Protocol, cast

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .speech_floor_guard import SPEECH_TURN_EPOCH

GENERATION_ID = "phone_agent_generation_id"


@dataclass
class GenerationFailureFrame(Frame):
    turn_epoch: int = -1
    generation_id: int = -1


@dataclass
class _Generation:
    turn_epoch: int
    generation_id: int
    failed: bool = False
    closed: bool = False


class _EventRegistrar(Protocol):
    def add_event_handler(self, event_name: str, handler: Callable[..., Awaitable[None]]) -> None: ...


def bind_generation_failures(llm: FrameProcessor, turn_epoch: Callable[[], int]) -> None:
    """Bind only services whose on_error signals terminal generation failure.

    A ContextVar ties background callbacks to their originating generation,
    rather than whichever caller turn happens to be current when they finish.
    """
    scope: ContextVar[_Generation | None] = ContextVar(f"generation-{llm.id}", default=None)
    sequence = count(1)

    async def before(_processor: FrameProcessor, frame: Frame) -> None:
        if isinstance(frame, LLMContextFrame):
            scope.set(_Generation(turn_epoch(), next(sequence)))

    async def after(_processor: FrameProcessor, frame: Frame) -> None:
        if isinstance(frame, LLMContextFrame):
            generation = scope.get()
            if generation is not None:
                generation.closed = True
            scope.set(None)

    async def stamp(_processor: FrameProcessor, frame: Frame) -> None:
        generation = scope.get()
        if generation is not None and isinstance(
            frame, LLMFullResponseStartFrame | LLMTextFrame | LLMFullResponseEndFrame
        ):
            frame.metadata[GENERATION_ID] = generation.generation_id
            frame.metadata[SPEECH_TURN_EPOCH] = generation.turn_epoch

    async def failed(_processor: FrameProcessor, frame: ErrorFrame) -> None:
        generation = scope.get()
        if generation is None or generation.failed or generation.closed or frame.fatal:
            return
        if frame.processor is not None and frame.processor is not llm:
            return
        generation.failed = True
        await llm.push_frame(
            GenerationFailureFrame(
                turn_epoch=generation.turn_epoch,
                generation_id=generation.generation_id,
            ),
            FrameDirection.DOWNSTREAM,
        )
        # Some provider implementations raise before their normal EndFrame.
        # Resolve the lifecycle now; a subsequent duplicate End is harmless.
        await llm.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    # Pipecat 1.7 leaves its handler parameter untyped. Keep that vendor
    # boundary explicit while the callbacks above retain checked signatures.
    register = cast(_EventRegistrar, llm).add_event_handler
    register("on_before_process_frame", before)
    register("on_after_process_frame", after)
    register("on_before_push_frame", stamp)
    register("on_error", failed)
