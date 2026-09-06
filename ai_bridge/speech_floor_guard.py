"""Fence synthesized speech at the final handoff to the audio transport.

Turn detection can change while generation or synthesis is in flight.  A quiet
microphone alone must never reauthorize an old answer: authorization travels
through TTS with the response and is checked again for every audio chunk.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from pipecat.frames.frames import (
    ControlFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

SPEECH_TURN_EPOCH = "phone_agent_speech_turn_epoch"
SPEECH_RESPONSE_ID = "phone_agent_speech_response_id"
SYNTHESIS_CONTEXT_ID = "phone_agent_synthesis_context_id"


@dataclass
class SpeechFloorPermissionFrame(ControlFrame):
    """Ordered authorization before a direct TTSSpeakFrame enters TTS.

Pipecat serializes ordinary control frames alongside synthesis.  Unlike a
system frame, this marker cannot jump ahead of previously queued TTS contexts.
"""

    turn_epoch: int


class SpeechFloorGuard(FrameProcessor):
    """Drop synthesis belonging to an interrupted or uncommitted caller turn."""

    def __init__(
        self,
        *,
        caller_owns_floor: Callable[[], bool],
        turn_epoch: Callable[[], int],
    ) -> None:
        super().__init__()  # pyright: ignore[reportUnknownMemberType] -- Pipecat **kwargs.
        self._caller_owns_floor = caller_owns_floor
        self._turn_epoch = turn_epoch
        self._authorized_epoch: int | None = None
        self._contexts: OrderedDict[str, int | None] = OrderedDict()
        self._anonymous_epoch: int | None = None
        self._response_allowed = False
        self.dropped_audio_frames = 0

    def _allowed(self, epoch: int | None) -> bool:
        return (
            epoch is not None
            and epoch == self._turn_epoch()
            and not self._caller_owns_floor()
        )

    def _remember_context(self, context_id: str, epoch: int | None) -> None:
        self._contexts[context_id] = epoch
        self._contexts.move_to_end(context_id)
        # Keep canceled-context tombstones so delayed frames cannot inherit a
        # later response's permission. Bound memory for long-running calls.
        while len(self._contexts) > 256:
            self._contexts.popitem(last=False)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, InterruptionFrame | UserStartedSpeakingFrame):
            self._authorized_epoch = None
            self._anonymous_epoch = None
            self._response_allowed = False
            for context_id in self._contexts:
                self._contexts[context_id] = None
            await self.push_frame(frame, direction)
            return
        if direction is not FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, SpeechFloorPermissionFrame):
            self._authorized_epoch = frame.turn_epoch if self._allowed(frame.turn_epoch) else None
            return
        if isinstance(frame, LLMFullResponseStartFrame):
            epoch = frame.metadata.get(SPEECH_TURN_EPOCH)
            self._authorized_epoch = epoch if self._allowed(epoch) else None
            self._response_allowed = self._authorized_epoch is not None
            if self._response_allowed:
                await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSStartedFrame):
            # Cached reflex PCM passes through the policy before TTS and carries
            # its own epoch. Normal TTS contexts inherit the serialized marker.
            epoch = frame.metadata.get(SPEECH_TURN_EPOCH, self._authorized_epoch)
            epoch = epoch if self._allowed(epoch) else None
            if frame.context_id:
                if frame.context_id in self._contexts:
                    # A duplicated start must not resurrect canceled synthesis.
                    epoch = self._contexts[frame.context_id]
                self._remember_context(frame.context_id, epoch)
            else:
                self._anonymous_epoch = epoch
            if self._allowed(epoch):
                await self.push_frame(frame, direction)
            return
        if isinstance(frame, TTSAudioRawFrame | TTSTextFrame | TTSStoppedFrame):
            context_id = frame.context_id
            epoch = self._contexts.get(context_id) if context_id else self._anonymous_epoch
            if not self._allowed(epoch):
                if context_id:
                    self._remember_context(context_id, None)
                else:
                    self._anonymous_epoch = None
                if isinstance(frame, TTSAudioRawFrame):
                    self.dropped_audio_frames += 1
                return
            await self.push_frame(frame, direction)
            return
        if isinstance(frame, LLMFullResponseEndFrame):
            if self._response_allowed and self._allowed(self._authorized_epoch):
                await self.push_frame(frame, direction)
            self._response_allowed = False
            return
        await self.push_frame(frame, direction)
