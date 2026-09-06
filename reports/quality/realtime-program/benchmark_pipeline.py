"""Real STT/LLM/TTS through the production pipeline and a simulated phone clock.

No phone link, microphone, speaker, WhatsApp send, or CRM write is used. Tool
handlers are replaced before audio begins; the existing services are probed
only for readiness/catalog construction. PCM acknowledgement timing is simulated.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from phone_agent_gateway.ai_bridge.media_protocol import FrameDirection, FrameKind, MediaFrame
from phone_agent_gateway.ai_bridge.pipecat_transport import (
    PhoneAgentTransport,
    PhoneAgentTransportParams,
)
from phone_agent_gateway.ai_bridge.production_pipeline import ProductionCallPipeline
from phone_agent_gateway.ai_bridge.runtime_config import ProviderConfig
from phone_agent_gateway.ai_bridge.session import SessionPhase

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("benchmark_fixture_helper", ROOT / "benchmark_components.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stt-provider", choices=["antigravity_live", "whisper_mlx", "whisper_turbo"], default="antigravity_live")
    parser.add_argument("--fail-llm", action="store_true", help="Inject a generation failure; never invoke tools")
    parser.add_argument("--drop-stt", action="store_true", help="Close the diagnostic STT stream during the first utterance")
    parser.add_argument("--fail-tts", action="store_true", help="Fail the first live TTS request after its first PCM chunk")
    parser.add_argument("--tts-failure-mode", choices=["partial", "empty", "stall"], default="partial")
    parser.add_argument("--corroborate", action="store_true")
    parser.add_argument("--input-case", choices=["en_offer", "fr_refusal_noise"], default="en_offer")
    parser.add_argument("--trace-vad", action="store_true")
    parser.add_argument("--vad-confidence", type=float)
    parser.add_argument("--vad-start-ms", type=int, default=120)
    parser.add_argument("--output")
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="Print results without writing an evidence artifact",
    )
    args = parser.parse_args()
    if args.drop_stt and args.stt_provider != "antigravity_live":
        parser.error("--drop-stt requires cloud streaming STT")
    output = ROOT / ({"whisper_mlx": "pipeline-benchmark-mlx.json", "whisper_turbo": "pipeline-benchmark-whisper-cpu.json"}.get(args.stt_provider, "pipeline-benchmark.json"))
    with tempfile.TemporaryDirectory(prefix="phoneagent-pipeline-bench-") as tmp:
        pcm = fixtures.synthesize(("en_offer", "en-US", "Samantha", "I would like the six month plan for thirty nine dollars."), Path(tmp))
        correction = fixtures.synthesize(("en_interrupt", "en-US", "Samantha", "Wait, I want the yearly plan instead."), Path(tmp))
    if args.input_case == "fr_refusal_noise":
        spec = importlib.util.spec_from_file_location("noise_fixture", ROOT / "benchmark_bilingual_noise_sequences.py")
        noise_fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(noise_fixture)
        case = next(row for row in noise_fixture.make_cases() if row["case"] == "fr_negative")
        mixed, _ = noise_fixture.with_noise(case["pcm"], 8*16000, 10.0, case["noise_seed"])
        pcm = mixed[:len(case["pcm"]) + 3840]
        with tempfile.TemporaryDirectory(prefix="phoneagent-fr-clarification-") as temporary:
            correction = fixtures.synthesize(("fr_refusal_clarification", "fr-FR", "Thomas", "Non, je ne veux pas l'abonnement annuel."), Path(temporary))

    transport = PhoneAgentTransport(PhoneAgentTransportParams())
    transport.session.set_phase(SessionPhase.ACTIVE)
    events, writes, flushes = [], [], []
    first_audio = asyncio.Event()
    origin = time.monotonic()
    def emit(event):
        events.append({"at_ms": (time.monotonic()-origin)*1000, **event})
    async def write(payload, generation, sequence):
        if not writes:
            await asyncio.sleep(0.10)  # simulated initial phone reservoir
        await asyncio.sleep(0.02)
        if generation != transport.session.generation_id:
            return
        transport.session.mark_rendered(generation, sequence)
        writes.append({"at_ms": (time.monotonic()-origin)*1000, "generation": generation, "sequence": sequence})
        first_audio.set()
    async def end(generation, sequence):
        transport.session.mark_rendered(generation, sequence)
    async def flush(advance):
        flushes.append({"at_ms": (time.monotonic()-origin)*1000, "generation": advance.next_generation})
        return {"status": "ok"}
    transport.set_tx_handler(write)
    transport.set_audio_end_handler(end)
    transport.set_flush_handler(flush)
    config = SimpleNamespace(
        providers=ProviderConfig(stt_provider=args.stt_provider, antigravity_live_local_corroboration=args.corroborate, tts_provider="edge_tts", tts_voice_id="en-US-AvaNeural", tts_aggregation="sentence", speculative_pipeline_enabled=True),
        task_id="iptv_shopping_prod_v3_3", sample_rate=16000, memory_enabled=False, system_prompt="",
    )
    pipeline = ProductionCallPipeline(transport, config, caller_id="unknown:pipeline-benchmark", event_sink=emit)
    expected_input, stt_input = bytearray(), bytearray()
    vad_trace = []
    if args.vad_confidence is not None:
        from pipecat.audio.vad.vad_analyzer import VADParams
        pipeline.services.stt._vad.set_params(VADParams(confidence=args.vad_confidence, start_secs=args.vad_start_ms/1000, stop_secs=0.2, min_volume=0))
    if args.trace_vad:
        import numpy as np
        stt = pipeline.services.stt
        run_stt = stt.run_stt
        async def capture_stt(audio):
            stt_input.extend(audio)
            async for frame in run_stt(audio):
                yield frame
        stt.run_stt = capture_stt
        confidence = stt._vad.voice_confidence
        reset_model = stt._vad._model.reset_states
        resets = [0]
        def capture_reset(*args, **kwargs):
            resets[0] += 1
            return reset_model(*args, **kwargs)
        stt._vad._model.reset_states = capture_reset
        def capture_confidence(audio):
            reset_before = resets[0]
            value = confidence(audio)
            vad_trace.append({"at_ms":(time.monotonic()-origin)*1000,
                              "input_bytes":len(stt_input), "confidence":float(np.asarray(value).reshape(-1)[0]),
                              "state_before":stt._vad._vad_state.name,
                              "model_reset":resets[0] != reset_before})
            return value
        stt._vad.voice_confidence = capture_confidence
    if args.fail_llm:
        async def fail_generation(_prompt):
            raise RuntimeError("Controlled generation failure for recovery qualification")
        pipeline.services.llm._generate_gemini = fail_generation
        output = ROOT / "pipeline-generation-failure-replay.json"

    if args.drop_stt:
        output = ROOT / "pipeline-stt-disconnect-replay.json"

    if args.fail_tts:
        from pipecat.frames.frames import TTSAudioRawFrame
        original_tts = pipeline.services.tts._run_tts_impl
        injected = False
        async def fail_first_synthesis(text, context_id):
            nonlocal injected
            fail_this = not injected
            injected = True
            source = original_tts(text, context_id)
            try:
                if fail_this and args.tts_failure_mode == "empty":
                    raise RuntimeError("Controlled TTS failure before PCM")
                if fail_this and args.tts_failure_mode == "stall":
                    await asyncio.Event().wait()
                async for frame in source:
                    yield frame
                    if fail_this and isinstance(frame, TTSAudioRawFrame):
                        raise RuntimeError("Controlled TTS stream failure after PCM")
            finally:
                await source.aclose()
        pipeline.services.tts._run_tts_impl = fail_first_synthesis
        output = ROOT / ("pipeline-tts-failure-replay.json" if args.tts_failure_mode == "partial" else f"pipeline-tts-{args.tts_failure_mode}-failure-replay.json")

    if args.corroborate:
        output = ROOT / "pipeline-corroborated.json"

    if args.input_case == "fr_refusal_noise":
        output = ROOT / ("pipeline-corroborated-french-refusal.json" if args.corroborate else "pipeline-cloud-french-refusal.json")

    if args.trace_vad:
        output = ROOT / f"pipeline-vad-{type(pipeline.services.stt._vad).__name__}-{args.vad_confidence or 0.7}-{args.vad_start_ms}-{args.input_case}.json"
    if args.output:
        requested = Path(args.output)
        output = requested if requested.is_absolute() else ROOT / requested

    original_start = pipeline.tools.start
    disabled_calls = []
    async def tools_without_effects():
        catalog = await original_start()
        def blocked(name):
            def call(arguments):
                disabled_calls.append(name)
                return {"error": "Tool side effects are disabled in this diagnostic"}
            return call
        pipeline.tools.catalog = {name: replace(tool, spec=None, handler=blocked(name)) for name, tool in catalog.items()}
        return pipeline.tools.catalog
    pipeline.tools.start = tools_without_effects
    running = True
    queued = bytearray()
    sequence = 0
    async def feed():
        nonlocal sequence
        next_at = time.monotonic()
        while running:
            chunk = bytes(queued[:640]) if queued else bytes(640)
            if queued:
                del queued[:640]
            chunk += bytes(640-len(chunk))
            if args.trace_vad:
                expected_input.extend(chunk)
            transport.feed_phone_frame(MediaFrame(kind=FrameKind.AUDIO, direction=FrameDirection.PHONE_TO_MAC, call_id=transport.session.call_id, generation_id=transport.session.generation_id, sequence=sequence, monotonic_ns=time.monotonic_ns(), payload=chunk, sample_rate=16000, channels=1, sample_width=2))
            sequence += 1
            next_at += 0.02
            await asyncio.sleep(max(0, next_at-time.monotonic()))
    feed_task = None
    result = {"input_case": args.input_case, "local_corroboration": args.corroborate, "injected_llm_failure": args.fail_llm, "injected_tts_failure": args.fail_tts, "tts_failure_mode": args.tts_failure_mode if args.fail_tts else None, "injected_stt_disconnect": args.drop_stt, "scope": "Real providers with simulated phone clock; all tool side effects disabled", "correction_observation_timeout_secs": 60 if args.stt_provider == "whisper_turbo" else 30}
    try:
        start = time.monotonic()
        await pipeline.start()
        result["pipeline_start_ms"] = (time.monotonic()-start)*1000
        queued.extend(pcm)
        first_input_at = time.monotonic()
        eof = first_input_at + len(pcm)/32000
        feed_task = asyncio.create_task(feed())
        if args.drop_stt:
            await asyncio.sleep(1.0)
            stt = pipeline.services.stt
            failed_stream = stt._stream
            if failed_stream is None:
                raise RuntimeError("No diagnostic STT stream to close")
            disconnected_at = time.monotonic()
            result["disconnect_during_caller_speech"] = stt.caller_owns_floor()
            failed_stream.close()
            while time.monotonic() - disconnected_at < 15:
                if stt._stream is not None and stt._stream is not failed_stream:
                    result["stt_reconnected_ms"] = (time.monotonic() - disconnected_at) * 1000
                    break
                await asyncio.sleep(0.02)
        await asyncio.wait_for(first_audio.wait(), len(pcm)/32000 + 20)
        result["first_simulated_playout_after_eof_ms"] = writes[0]["at_ms"] - (eof-origin)*1000
        await asyncio.sleep(0.6)
        correction_start = time.monotonic()
        if args.trace_vad:
            result["correction_input_offset"] = len(expected_input)
            result["correction_source_sha256"] = hashlib.sha256(correction).hexdigest()
            result["correction_start_ms"] = (correction_start-origin)*1000
            result["correction_queued_bytes_before"] = len(queued)
        prior_generation = transport.session.generation_id
        queued.extend(correction)
        deadline = time.monotonic() + (60 if args.stt_provider == "whisper_turbo" else 30)
        while time.monotonic() < deadline:
            callers = [e for e in events if e.get("type")=="transcript" and e.get("role")=="user"]
            answered_correction = len(callers) >= (1 if args.drop_stt else 2) and any(
                e.get("type")=="transcript" and e.get("role")=="assistant" and e["at_ms"] > callers[-1]["at_ms"]
                for e in events
            )
            if answered_correction and not queued and not pipeline.policy._pending_playback_ids and pipeline.policy._active_playback_id is None:
                break
            await asyncio.sleep(0.1)
        relevant = [f for f in flushes if f["generation"] > prior_generation]
        result["barge_in_to_flush_ms"] = relevant[0]["at_ms"]-(correction_start-origin)*1000 if relevant else None
        result["correction_transcribed"] = len(callers) >= (1 if args.drop_stt else 2)
        result["correction_answered"] = answered_correction
        result["playback_accounting_resolved"] = not pipeline.policy._pending_playback_ids and pipeline.policy._active_playback_id is None
        result["events"] = events
        result["flushes"] = flushes
        result["write_count"] = len(writes)
        result["disabled_tool_calls"] = disabled_calls
    except Exception as exc:
        result["error"] = type(exc).__name__
        result["events"] = events
    finally:
        running = False
        if feed_task is not None:
            await feed_task
        await pipeline.cancel("diagnostic completed")
        if args.trace_vad:
            result["vad_trace"] = vad_trace
            result["captured_input_bytes"] = len(stt_input)
            result["fed_input_bytes"] = len(expected_input)
            result["input_matches_feed_prefix"] = stt_input == expected_input[:len(stt_input)]
        if not args.stdout_only:
            output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    summary = {
        key: value
        for key, value in result.items()
        if key not in ("events", "flushes", "vad_trace")
    }
    if args.stdout_only:
        summary["timing_events"] = [
            event
            for event in events
            if event.get("type")
            in {
                "provider_latency",
                "speculation",
                "transcript",
                "tool_started",
                "tool_call",
                "playback_status",
            }
        ]
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
