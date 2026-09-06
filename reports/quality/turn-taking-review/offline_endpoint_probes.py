"""Reproduce turn-controller behavior without network, ASR, TTS, or real calls.

Run from the checkout using:
    .venv/bin/python reports/quality/turn-taking-review/offline_endpoint_probes.py

These probes exercise the checkout implementation, not an installed runtime.
Smart Turn model loading is disabled. Synthetic transcript strings contain no
customer data. Call/session startup and close methods are never invoked.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
from pipecat.frames.frames import TranscriptionFrame


async def speech_resume_during_candidate_callback() -> dict:
    service = AntigravityLiveSTTService(
        smart_turn_enabled=False, speculative_pipeline_enabled=True
    )
    service._speech_epoch = 1
    service._stage_transcription("I want movies.", is_final=True)
    service._last_speech_at = time.monotonic() - 0.4
    service._last_transcript_update_at = time.monotonic() - 0.3
    captured = []

    async def capture(frame, *_):
        if isinstance(frame, TranscriptionFrame):
            captured.append(
                {
                    "text": frame.text,
                    "ms_since_resumed_speech": round(
                        (time.monotonic() - service._last_speech_at) * 1000, 3
                    ),
                    "speech_epoch": service._speech_epoch,
                }
            )
            service._is_closing = True

    async def candidate_handler(_):
        # Represent PCM arriving while an awaited callback yields. No ASR text
        # revision has arrived yet, so its timestamp remains unchanged.
        service._track_speech_energy(-20.0)
        await asyncio.sleep(0)

    service.push_frame = capture
    service.set_speculation_handlers(candidate_handler, None)
    await asyncio.wait_for(service._silence_watchdog_loop(), timeout=0.5)
    return {"probe": "speech_resume_during_candidate_callback", "commits": captured}


async def cumulative_extension_without_new_speech() -> dict:
    service = AntigravityLiveSTTService(smart_turn_enabled=False)
    service._speech_epoch = 1
    service._stage_transcription("Please change my address", is_final=True)

    async def capture(*_):
        pass

    service.push_frame = capture
    await service._commit_pending_transcript(source="offline_probe")
    candidate = service._stage_transcription(
        "Please change my address to London", is_final=True
    )
    return {
        "probe": "cumulative_extension_without_new_speech",
        "new_candidate": candidate,
        "speech_epoch": service._speech_epoch,
        "committed_speech_epoch": service._last_committed_speech_epoch,
    }


async def artificial_silence_precedes_buffered_audio() -> dict:
    service = AntigravityLiveSTTService(smart_turn_enabled=False)
    service._session_id = "offline-only-do-not-start-session"
    # A non-None sentinel makes run_stt enqueue bytes. No sender is started,
    # so these bytes cannot reach a provider.
    service._sender_task = object()
    service._stage_transcription("I want music", is_final=False)
    service._last_speech_at = time.monotonic() - 0.13
    # Captured but not yet sent: 20 ms tail + 120 ms natural silence, below
    # the default 150 ms network chunk size.
    speech_tail = (1000).to_bytes(2, "little", signed=True) * 320
    service._audio_buffer.extend(speech_tail + bytes(3840))
    buffered_before = len(service._audio_buffer)
    watchdog = asyncio.create_task(service._silence_watchdog_loop())
    await asyncio.sleep(0.03)
    service._is_closing = True
    await watchdog
    async for _ in service.run_stt(bytes(640)):
        pass
    chunks = []
    while not service._send_queue.empty():
        data, sequence = service._send_queue.get_nowait()
        chunks.append(
            {
                "sequence": sequence,
                "bytes": len(data),
                "all_zero": not any(data),
                "starts_with_captured_speech_tail": data.startswith(speech_tail),
            }
        )
    return {
        "probe": "artificial_silence_precedes_buffered_audio",
        "captured_bytes_buffered_before_flush": buffered_before,
        "queued_chunks": chunks,
    }


def quiet_audio_does_not_advance_speech_clock() -> dict:
    service = AntigravityLiveSTTService(smart_turn_enabled=False)
    service._last_speech_at = time.monotonic() - 0.6
    before = service._last_speech_at
    service._track_speech_energy(-48.0)
    return {
        "probe": "quiet_audio_does_not_advance_speech_clock",
        "energy_dbfs": -48,
        "threshold_dbfs": service._energy_threshold_dbfs,
        "speech_clock_advanced": before != service._last_speech_at,
    }


async def main() -> None:
    results = [
        await speech_resume_during_candidate_callback(),
        await cumulative_extension_without_new_speech(),
        await artificial_silence_precedes_buffered_audio(),
        quiet_audio_does_not_advance_speech_clock(),
    ]
    print(json.dumps({"scope": "checkout offline behavior probes", "results": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
