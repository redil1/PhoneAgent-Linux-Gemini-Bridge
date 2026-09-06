"""Real recognizer and acoustic-controller probes using protected synthetic audio.

No calls, messages, microphone, speaker, or customer recordings are used.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import wave
from pathlib import Path

import numpy as np
from pipecat.frames.frames import TranscriptionFrame, UserStartedSpeakingFrame

from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
from phone_agent_gateway.ai_bridge.production_pipeline import _default_stt_context

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT.parents[2] / "qualification/corpus/v1"


def pcm_for(name: str) -> bytes:
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    spec = next(s["audio"] for s in manifest["scenarios"] if s["id"] == name)
    path = CORPUS / spec["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == spec["sha256"]
    with wave.open(str(path)) as source:
        return source.readframes(source.getnframes())


async def probe(name, pcm, language, inject_text=False):
    service = AntigravityLiveSTTService(
        language=language, context_bias=_default_stt_context(language), chunk_duration_ms=200
    )
    frames, hypotheses, interruptions, sends = [], [], [], []
    queue_peak = 0
    original_send = service._send_chunk

    async def send(chunk, sequence):
        started = time.perf_counter()
        await original_send(chunk, sequence)
        sends.append(
            {
                "sequence": sequence,
                "bytes": len(chunk),
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            }
        )

    service._send_chunk = send
    original = service._handle_provider_transcription

    async def stage(text, *, is_final):
        hypotheses.append({"text": text, "final": is_final, "speech_epoch": service._speech_epoch})
        return await original(text, is_final=is_final)

    async def capture(frame, *_):
        if isinstance(frame, TranscriptionFrame):
            frames.append({"kind": "commit", "text": frame.text, "at": time.perf_counter()})
        elif isinstance(frame, UserStartedSpeakingFrame):
            frames.append({"kind": "start", "at": time.perf_counter()})

    async def interrupt():
        interruptions.append(time.perf_counter())

    service._handle_provider_transcription, service.push_frame, service.broadcast_interruption = (
        stage,
        capture,
        interrupt,
    )
    try:
        if not inject_text:
            await asyncio.to_thread(service._discover_bridge)
            await service._start_session()
        else:
            service._reset_turn_state()
        start = time.perf_counter()
        eof = start + len(pcm) / 32000
        injected = False
        for offset in range(0, len(pcm) + 32000 * 4, 640):
            chunk = pcm[offset : offset + 640] if offset < len(pcm) else bytes(640)
            async for _ in service.run_stt(chunk):
                pass
            queue_peak = max(queue_peak, service._send_queue.qsize())
            if inject_text and offset >= 16000 and not injected:
                await service._handle_provider_transcription("Yes, please.", is_final=True)
                injected = True
            if inject_text:
                await service._watchdog_tick()
            await asyncio.sleep(max(0, start + (offset + 640) / 32000 - time.perf_counter()))
        return {
            "case": name,
            "language": language,
            "injected_provider_fault": inject_text,
            "audio_ms": len(pcm) / 32,
            "hypotheses": hypotheses,
            "frames": [{**f, "after_eof_ms": (f["at"] - eof) * 1000} for f in frames],
            "interruptions": len(interruptions),
            "acoustic_epochs": service._speech_epoch,
            "chunk_ms": 200,
            "send_queue_peak": queue_peak,
            "send_queue_remaining": service._send_queue.qsize(),
            "sends": sends,
        }
    finally:
        await service._close_session()
        service._vad._executor.shutdown(wait=False)
        if service._smart_turn is not None:
            service._smart_turn._executor.shutdown(wait=False)


async def main():
    en, fr = pcm_for("clear_en"), pcm_for("clear_fr")
    quiet = (np.frombuffer(en, dtype=np.int16).astype(np.float32) * 0.10).astype(np.int16).tobytes()
    cases = [
        ("silence", pcm_for("silence"), "en-US", False),
        ("noise_only", pcm_for("noise_only"), "en-US", False),
        ("clear_en", en, "en-US", False),
        ("quiet_en_minus20db", quiet, "en-US", False),
        ("clear_fr", fr, "fr-FR", False),
        ("silence_with_provider_fault", pcm_for("silence"), "en-US", True),
    ]
    result = {"scope": __doc__, "rows": []}
    for args in cases:
        row = await probe(*args)
        result["rows"].append(row)
        (ROOT / "noise-level-benchmark.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        )
        print(
            json.dumps(
                {k: v for k, v in row.items() if k not in ("frames", "hypotheses", "sends")},
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
