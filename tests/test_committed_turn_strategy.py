"""Committed transcripts avoid a second endpoint timer in either queue order."""
import pytest
from pipecat.frames.frames import (
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)

from phone_agent_gateway.ai_bridge.committed_turn_strategy import CommittedTranscriptStopStrategy


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_first', [True, False])
async def test_commit_requires_both_signals_and_fires_without_timer(stop_first):
    strategy = CommittedTranscriptStopStrategy()
    events = []
    async def trigger():
        events.append('commit')
    strategy.trigger_user_turn_stopped = trigger
    await strategy.process_frame(UserStartedSpeakingFrame())
    final = TranscriptionFrame(text='Yes please.', user_id='caller', timestamp='', finalized=True)
    stop = UserStoppedSpeakingFrame()
    first, second = (stop, final) if stop_first else (final, stop)
    await strategy.process_frame(first)
    await strategy._maybe_trigger_user_turn_stopped()
    assert events == []
    await strategy.process_frame(second)
    assert events == ['commit']
    await strategy._maybe_trigger_user_turn_stopped()
    await strategy.process_frame(final)
    assert events == ['commit']


@pytest.mark.asyncio
async def test_new_speech_disarms_old_stop_and_unfinalized_text_cannot_commit():
    strategy = CommittedTranscriptStopStrategy()
    events = []
    async def trigger():
        events.append('commit')
    strategy.trigger_user_turn_stopped = trigger
    await strategy.process_frame(UserStoppedSpeakingFrame())
    await strategy.process_frame(UserStartedSpeakingFrame())
    await strategy.process_frame(TranscriptionFrame(text='I prefer', user_id='caller', timestamp='', finalized=True))
    await strategy._maybe_trigger_user_turn_stopped()
    assert events == []
    await strategy.handle_user_turn_started()
    await strategy.process_frame(UserStoppedSpeakingFrame())
    await strategy.process_frame(TranscriptionFrame(text='I prefer', user_id='caller', timestamp='', finalized=False))
    await strategy._maybe_trigger_user_turn_stopped()
    assert events == []
