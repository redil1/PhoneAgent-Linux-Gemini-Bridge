"""Annotate trusted turns and mark uncertain recognition before model context.

Meaningful trusted speech remains model-driven. Explicitly untrusted recognition
is preserved in the audit transcript, but replaced by an uncertainty marker in
model history. UncertainTurnGate routes that turn to bounded clarification.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    LLMContextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from .agent_policy import AgentPolicyRuntime, transcription_evidence
from .conversation_repair import TurnQuality

logger = logging.getLogger("PhoneAgentRepair")

UNCERTAIN_AUDIO_CONTEXT = "[Uncertain caller audio: no reliable intent was established.]"


class ConversationRepairProcessor(FrameProcessor):
    """Give the LLM turn-quality context and otherwise stay out of dialogue."""

    def __init__(self, runtime: AgentPolicyRuntime, *, enabled: bool = True) -> None:
        super().__init__()
        self.runtime = runtime
        self._enabled = enabled
        # A backchannel is by definition something said *over* the other
        # speaker. Once the agent has stopped and is waiting, anything the
        # caller says is a turn - dropping it leaves both sides silent.
        self._bot_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and direction is FrameDirection.DOWNSTREAM:
            trusted, _, _ = transcription_evidence(frame)
            if not trusted:
                self.runtime.note_turn_quality(TurnQuality.UNINTELLIGIBLE)
                # The original uncertain words have already been logged by the
                # transcription policy. Do not turn them into user history facts.
                annotated = replace(frame, text=UNCERTAIN_AUDIO_CONTEXT)
                annotated.metadata.update(frame.metadata)
                await self.push_frame(annotated, direction)
                return
        if (
            not self._enabled
            or direction is not FrameDirection.DOWNSTREAM
            or not isinstance(frame, TranscriptionFrame)
        ):
            await self.push_frame(frame, direction)
            return

        trusted, _, _ = transcription_evidence(frame)
        quality = (
            self.runtime.classify_turn(frame.text)
            if trusted
            else TurnQuality.UNINTELLIGIBLE
        )
        self.runtime.note_turn_quality(quality)

        if quality is TurnQuality.ACTIONABLE:
            self.runtime.note_turn_understood()
            await self.push_frame(frame, direction)
            return

        if quality is TurnQuality.BACKCHANNEL:
            if not self._bot_speaking:
                # The agent is waiting, so this is the caller's answer, not
                # filler over someone else's sentence. Dropping it here left
                # both sides silent with the turn invisible in the Studio.
                logger.info(
                    "Treated a short reply as an answer because the agent was not speaking: %r",
                    frame.text.strip()[:40],
                )
                self.runtime.note_turn_understood()
                await self.push_frame(frame, direction)
                return
            logger.warning(
                "Ignored caller backchannel over agent speech chars=%d",
                len(frame.text.strip()),
            )
            return

        # Repeat requests, bad timing, identity challenges, fragments, and
        # unintelligible audio all need contextual reasoning. Pass the caller's
        # words through and let the model respond using the quality guidance in
        # the mutable live-state system message.
        logger.info(
            "Delegated caller-turn recovery to model quality=%s chars=%d",
            quality.value,
            len(frame.text.strip()),
        )
        await self.push_frame(frame, direction)


class UncertainTurnGate(FrameProcessor):
    """Resolve recognized-but-untrusted turns without asking a model to guess."""

    def __init__(self, runtime: AgentPolicyRuntime, recover: Callable[[int], Awaitable[None]]):
        super().__init__()
        self.runtime = runtime
        self._recover = recover

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if (direction is FrameDirection.DOWNSTREAM and isinstance(frame, LLMContextFrame)
                and not self.runtime.last_caller_transcript_trusted):
            await self._recover(self.runtime.turn_epoch)
            return
        await self.push_frame(frame, direction)
