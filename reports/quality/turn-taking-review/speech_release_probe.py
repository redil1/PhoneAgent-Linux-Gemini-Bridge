"""Offline probes of turn-release races. No providers, credentials, devices, or calls."""
from __future__ import annotations
import asyncio
import json
import struct
import time
from pathlib import Path
from typing import Any
from pipecat.frames.frames import BotStartedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection
from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
from phone_agent_gateway.ai_bridge.parakeet_local_stt import ParakeetLocalSTTService


def capture(service: Any):
    frames, interruptions, cancellations = [], [], []
    async def push(frame: Any, *_: Any):
        frames.append(type(frame).__name__)
    async def interrupt(*_: Any):
        interruptions.append(True)
    async def cancel(reason: str):
        cancellations.append(reason)
    service.push_frame = push
    service.broadcast_interruption = interrupt
    service.set_speculation_handlers(None, cancel)
    return frames, interruptions, cancellations


def active_service():
    return AntigravityLiveSTTService(smart_turn_enabled=False, speculative_pipeline_enabled=True)


async def feed(service: Any, count: int = 25):
    pcm = struct.pack('<320h', *([8000] * 320))
    for _ in range(count):
        async for _ in service.run_stt(pcm):
            pass


async def main():
    result = {}
    stt = active_service()
    frames, interruptions, cancellations = capture(stt)
    stt._bot_speaking = True
    stt._bot_speaking_since = time.monotonic()
    await feed(stt)
    result['energy_before_provider_transcript'] = {
        'input_pcm_ms': 500,
        'provider_transcript_received': False,
        'user_started': 'UserStartedSpeakingFrame' in frames,
        'interruption_count': len(interruptions),
        'speech_candidate_at': stt._speech_candidate_at,
        'energy_registered': stt._last_speech_at > 0,
    }
    await stt._close_session()

    stt = active_service()
    frames, interruptions, cancellations = capture(stt)
    stt._stage_transcription('Actually I also need', is_final=False)
    stt._speech_candidate_at = time.monotonic() - 0.5
    await stt._ensure_user_started(force=False)
    await stt.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
    await stt._ensure_user_started(force=False)
    result['caller_started_while_answer_pending_then_bot_started'] = {
        'user_started': 'UserStartedSpeakingFrame' in frames,
        'stt_user_speaking': stt._speaking,
        'stt_bot_speaking': stt._bot_speaking,
        'interruption_count': len(interruptions),
    }
    await stt._close_session()

    stt = active_service()
    frames, interruptions, cancellations = capture(stt)
    stt._stage_transcription('I want the sports package.', is_final=True)
    stt._last_speculation_text = stt._last_transcript
    await stt._commit_pending_transcript(source='offline_probe')
    await feed(stt)
    result['resume_after_commit_before_next_transcript'] = {
        'committed_transcription': 'TranscriptionFrame' in frames,
        'input_pcm_ms': 500,
        'speculation_cancellations': cancellations,
        'interruption_count': len(interruptions),
        'last_speculation_text_after_commit': stt._last_speculation_text,
    }
    await stt._close_session()

    stt = ParakeetLocalSTTService()
    frames, interruptions, cancellations = capture(stt)
    stt._bot_speaking = True
    stt._bot_speaking_since = time.monotonic()
    await feed(stt)
    result['local_stt_twenty_ms_frames_barge_in'] = {
        'input_pcm_ms': 500,
        'user_started': 'UserStartedSpeakingFrame' in frames,
        'interruption_count': len(interruptions),
        'accumulated_speech_ms': stt._speech_bytes / 32,
    }
    await stt._shutdown()
    print(json.dumps(result, indent=2))
    Path(__file__).with_name('speech_release_probe_results.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    asyncio.run(main())
