"""Offline synthetic-voice smoke checks of actual Silero/Smart Turn integration.

Uses installed macOS voices, writes audio to /tmp, and never plays or sends audio.
No ASR session, LLM, phone, or cloud provider is started. This is integration
evidence, not an accuracy benchmark for actual telephone speech.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import numpy as np
from pipecat.frames.frames import TranscriptionFrame

from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService

ROOT = Path(__file__).resolve().parent
RATE = 16_000
FRAME_SAMPLES = 320
CASES = [
    {
        "id": "en_complete",
        "language": "en-US",
        "voice": "Samantha",
        "segments": ["I need help changing my address."],
    },
    {
        "id": "fr_complete",
        "language": "fr-FR",
        "voice": "Thomas",
        "segments": ["Je voudrais modifier mon adresse."],
    },
    {
        "id": "en_mid_thought",
        "language": "en-US",
        "voice": "Samantha",
        "segments": [
            "I would like to change my subscription because",
            "I am moving to a different city next month.",
        ],
    },
    {
        "id": "fr_mid_thought",
        "language": "fr-FR",
        "voice": "Thomas",
        "segments": [
            "Je voudrais changer mon abonnement parce que",
            "je déménage dans une autre ville le mois prochain.",
        ],
    },
]


def synthesize(case: dict[str, Any], directory: Path) -> list[bytes]:
    segments = []
    for index, text in enumerate(case["segments"]):
        source = directory / f"{case['id']}-{index}.aiff"
        raw = source.with_suffix(".pcm")
        subprocess.run(
            ["/usr/bin/say", "-v", case["voice"], "-r", "155", "-o", str(source), text],
            check=True, capture_output=True, timeout=30,
        )
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                "-ac", "1", "-ar", str(RATE), "-f", "s16le", str(raw),
            ],
            check=True, capture_output=True, timeout=30,
        )
        samples = np.frombuffer(raw.read_bytes(), dtype=np.int16)
        nonquiet = np.flatnonzero(np.abs(samples.astype(np.int32)) >= 96)
        if not len(nonquiet):
            raise RuntimeError(f"Local voice produced no signal for {case['id']}:{index}")
        # Remove synthesis padding, retain 80 ms on either side. Pauses below
        # are controlled fixture intervals, not estimates from arbitrary files.
        start = max(0, int(nonquiet[0]) - 1280)
        end = min(len(samples), int(nonquiet[-1]) + 1281)
        pcm = samples[start:end].tobytes()
        pcm += b"\0" * ((-len(pcm)) % (FRAME_SAMPLES * 2))
        raw.write_bytes(pcm)
        segments.append(pcm)
    return segments


async def run_case(case: dict[str, Any], segments: list[bytes]) -> dict[str, Any]:
    service = AntigravityLiveSTTService(language=case["language"], sample_rate=RATE)
    if service._smart_turn is None:
        raise RuntimeError("The existing local Smart Turn model did not load")
    events: list[dict[str, Any]] = []
    inferences: list[dict[str, Any]] = []
    position_ms = 0
    last_vad = None
    original_vad = service._vad.analyze_audio
    original_inference = service._run_smart_turn_inference

    async def capture(frame: Any, direction: Any = None) -> None:
        event = {"audio_ms": position_ms, "event": type(frame).__name__}
        if isinstance(frame, TranscriptionFrame):
            event["text"] = frame.text
        events.append(event)

    async def interrupt() -> None:
        events.append({"audio_ms": position_ms, "event": "interruption"})

    async def observe_vad(audio: bytes) -> Any:
        nonlocal last_vad
        result = await original_vad(audio)
        if result != last_vad:
            events.append({"audio_ms": position_ms, "event": "vad", "state": result.name})
            last_vad = result
        return result

    def observe_inference(audio: bytes) -> dict[str, Any]:
        started_at_audio_ms = position_ms
        started = time.perf_counter()
        result = original_inference(audio)
        inferences.append({
            "started_audio_ms": started_at_audio_ms,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "probability_complete": result.get("probability"),
            "input_audio_ms": round(len(audio) / (2 * RATE) * 1000),
        })
        return result

    service.push_frame = capture
    service.broadcast_interruption = interrupt
    service._vad.analyze_audio = observe_vad
    service._run_smart_turn_inference = observe_inference
    started = time.monotonic()

    async def feed(pcm: bytes) -> None:
        nonlocal position_ms
        for offset in range(0, len(pcm), FRAME_SAMPLES * 2):
            position_ms += 20
            async for _ in service.run_stt(pcm[offset : offset + FRAME_SAMPLES * 2]):
                pass
            await service._watchdog_tick()
            await asyncio.sleep(max(0, started + position_ms / 1000 - time.monotonic()))

    result: dict[str, Any] = {
        "id": case["id"], "language": case["language"], "voice": case["voice"],
        "pause_ms": 1200 if len(segments) > 1 else None,
        "segments": [
            {"text": text, "duration_ms": len(pcm) // 32,
             "sha256": hashlib.sha256(pcm).hexdigest()}
            for text, pcm in zip(case["segments"], segments, strict=True)
        ],
    }
    try:
        await feed(b"\0" * (RATE * 2 // 5))
        for index, segment in enumerate(segments):
            events.append({"audio_ms": position_ms, "event": "segment_start", "index": index})
            await feed(segment)
            events.append({"audio_ms": position_ms, "event": "segment_end", "index": index})
            service._stage_transcription(case["segments"][index], is_final=True)
            if index < len(segments) - 1:
                await feed(b"\0" * int(RATE * 2 * 1.2))
                result["commits_before_continuation"] = sum(
                    event["event"] == "TranscriptionFrame" for event in events
                )
        result["final_segment_end_ms"] = position_ms
        await feed(b"\0" * (RATE * 2 * 4))
        finals = [event for event in events if event["event"] == "TranscriptionFrame"]
        result.update({
            "final_commit_count": len(finals),
            "expected_text": " ".join(case["segments"]),
            "final_text_matches": bool(finals and finals[-1]["text"] == " ".join(case["segments"])),
            "commit_delay_after_fixture_segment_ms": (
                finals[-1]["audio_ms"] - result["final_segment_end_ms"] if finals else None
            ),
            "events": events, "smart_turn_inferences": inferences,
            "wall_duration_ms": round((time.monotonic() - started) * 1000),
        })
        result["passed"] = (
            result["final_commit_count"] == 1 and result["final_text_matches"]
            and result.get("commits_before_continuation", 0) == 0
        )
        return result
    finally:
        # _session_id is intentionally None throughout, so this cannot send an
        # EndAudioSession request or flush audio to a transcription provider.
        assert service._session_id is None
        await service.cleanup()


async def main() -> int:
    if not shutil.which("say") or not shutil.which("ffmpeg"):
        raise RuntimeError("Existing local say and ffmpeg installations are required")
    with tempfile.TemporaryDirectory(prefix="phoneagent-local-detectors-", dir="/tmp") as name:
        directory = Path(name)
        fixtures = [(case, synthesize(case, directory)) for case in CASES]
        results = []
        for case, segments in fixtures:
            result = await run_case(case, segments)
            results.append(result)
            print(json.dumps({key: value for key, value in result.items()
                              if key not in {"events", "segments", "smart_turn_inferences"}},
                             ensure_ascii=False), flush=True)
            (ROOT / "real-local-detector-results.json").write_text(
                json.dumps({
                    "scope": "Offline integration smoke checks using local synthetic speech; no accuracy claim",
                    "input_format": "16000 Hz mono signed 16-bit PCM, 20 ms frames, paced in real time",
                    "source_sha256": hashlib.sha256(Path(__file__).resolve().parents[3].joinpath(
                        "ai_bridge/antigravity_live_stt.py").read_bytes()).hexdigest(),
                    "results": results,
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    return 0 if all(result["passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
