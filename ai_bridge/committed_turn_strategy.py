"""External turn handshake for adapters publishing one already-committed transcript.

System stop frames can overtake transcription data frames in Pipecat's two
queues. The default external strategy waits another 500 ms in that ordering.
Our adapters already own endpoint/revision patience: trigger once both their
explicit stop and finalized transcript have arrived, in either order.
"""
from __future__ import annotations

from pipecat.frames.frames import (
    Frame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.external_user_turn_stop_strategy import ExternalUserTurnStopStrategy


class CommittedTranscriptStopStrategy(ExternalUserTurnStopStrategy):
    def __init__(self) -> None:
        super().__init__()
        self._explicit_stop_seen = False
        self._final_transcript_seen = False

    async def handle_user_turn_started(self):
        self._explicit_stop_seen = False
        self._final_transcript_seen = False
        await super().handle_user_turn_started()

    async def handle_user_turn_stopped(self):
        self._explicit_stop_seen = False
        self._final_transcript_seen = False
        await super().handle_user_turn_stopped()

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        if isinstance(frame, UserStartedSpeakingFrame):
            self._explicit_stop_seen = False
            self._final_transcript_seen = False
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._explicit_stop_seen = True
        if isinstance(frame, TranscriptionFrame) and frame.finalized:
            self._final_transcript_seen = True
        result = await super().process_frame(frame)
        if isinstance(frame, TranscriptionFrame) and frame.finalized and self._explicit_stop_seen:
            await self._maybe_trigger_user_turn_stopped()
        return result

    async def _maybe_trigger_user_turn_stopped(self):
        if (self._user_speaking or not self._explicit_stop_seen
                or not self._final_transcript_seen or not self._text
                or self._seen_interim_results):
            return
        # Disarm before invoking callbacks; the background timer must not
        # duplicate inference while a callback is awaiting downstream work.
        self._explicit_stop_seen = False
        self._final_transcript_seen = False
        await self.trigger_user_turn_stopped()
