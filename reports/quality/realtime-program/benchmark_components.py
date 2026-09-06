"""Paired real-provider component benchmarks; no phone calls or message sends.

Audio is generated locally from synthetic scripts. The reference microphone is
replaced by a paced PCM pipe, and its player by a byte sink. No real microphone
or speaker is opened. Tool catalogs are read but no tool handler is invoked.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import platform
import re
import ssl
import urllib.error
import urllib.request
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from pipecat.frames.frames import ErrorFrame, StartFrame, TTSAudioRawFrame, TranscriptionFrame
from pipecat.processors.aggregators.llm_response_universal import LLMContext

from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
from phone_agent_gateway.ai_bridge.antigravity_gemini_llm import AntigravityGeminiLLMService, _format_context_prompt
from phone_agent_gateway.ai_bridge.antigravity_live_stt import AntigravityLiveSTTService
from phone_agent_gateway.ai_bridge.cascade_tools import CascadeToolRuntime, emitted_tool_instructions
from phone_agent_gateway.ai_bridge.edge_tts_service import EdgeTTSService
from phone_agent_gateway.ai_bridge.parakeet_local_stt import load_model, transcribe_pcm

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
RATE = 16000
CASES = [
    ("en_short", "en-US", "Samantha", "Yes, please."),
    ("en_offer", "en-US", "Samantha", "I would like the six month plan for thirty nine dollars."),
    ("fr_short", "fr-FR", "Thomas", "Oui, s'il vous plaît."),
    ("fr_offer", "fr-FR", "Thomas", "Je voudrais le forfait de six mois à trente neuf dollars."),
]


def reference_module():
    path = ROOT / "Speechto Speech Google/speech_to_speech.py"
    spec = importlib.util.spec_from_file_location("phoneagent_reference_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_reference(module, llm):
    client = module.AntigravityClient.__new__(module.AntigravityClient)
    client.asr_model = module.DEFAULT_ASR_MODEL
    client.chat_model_alias = "gemini-2.5-flash"
    client.chat_model = module.CHAT_MODELS[client.chat_model_alias]
    client.base, client.csrf, client.ctx = llm._base_url, llm._csrf_token, llm._ssl_ctx
    return client


def synthesize(case, directory):
    ident, language, voice, text = case
    cache = OUT / "fixtures"
    cache.mkdir(exist_ok=True)
    cached_pcm = cache / f"{ident}.pcm"
    if cached_pcm.exists():
        return cached_pcm.read_bytes()
    source = directory / f"{ident}.aiff"
    raw = directory / f"{ident}.pcm"
    subprocess.run(["say", "-v", voice, "-r", "155", "-o", str(source), text], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-af", "highpass=f=300,lowpass=f=3400", "-ac", "1", "-ar", "16000", "-f", "s16le", str(raw)], check=True, capture_output=True)
    samples = np.frombuffer(raw.read_bytes(), dtype=np.int16)
    active = np.flatnonzero(np.abs(samples.astype(np.int32)) >= 96)
    if not len(active):
        raise RuntimeError("Synthetic voice produced no signal")
    samples = samples[max(0, active[0] - 1280):min(len(samples), active[-1] + 1281)]
    pcm = samples.tobytes()
    pcm += bytes((-len(pcm)) % 640)
    cached_pcm.write_bytes(pcm)
    return pcm


def words(text):
    return re.findall(r"[\w']+", text.casefold())


def wer(expected, actual):
    a, b = words(expected), words(actual)
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        new = [i]
        for j, y in enumerate(b, 1):
            new.append(min(new[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = new
    return row[-1] / max(1, len(a))


async def llm_bench(module, llm, repeats):
    policy = AgentPolicyRuntime(caller_id="unknown:benchmark", task_id="iptv_shopping_prod_v3_3", language="en-US", memory_enabled=False)
    runtime = CascadeToolRuntime(policy=policy, caller_id="unknown:benchmark", call_id="benchmark")
    rows = []
    try:
        await runtime.start()
        full = policy.recompile_system_prompt()
        compact = policy.persona_compiler.compile_realtime(task_contract=policy.task_contract, language="en-US", available_tools=policy.available_tools)
        protocol = emitted_tool_instructions(runtime)
        user = "I'm still missing something worth watching. What do you offer?"
        client = make_reference(module, llm)
        attempts = []
        original_rpc = client._rpc
        def traced_rpc(method, body, timeout=60):
            began = time.perf_counter()
            row = {"model": body.get("model"), "prompt_chars": len(body.get("prompt", ""))}
            try:
                status, data = original_rpc(method, body, timeout)
                row["status"] = status
                return status, data
            except urllib.error.HTTPError as exc:
                row["status"] = exc.code
                try:
                    error = json.loads(exc.read())
                    row["error_code"] = error.get("code")
                    row["error_message"] = str(error.get("message", ""))[:300]
                except Exception:
                    pass
                raise
            finally:
                row["elapsed_ms"] = (time.perf_counter()-began)*1000
                attempts.append(row)
        client._rpc = traced_rpc
        llm.set_latency_sink(attempts.append)
        for trial in range(repeats):
            modes = ["reference_short", "runtime_full", "runtime_compact", "reference_wire_full"]
            modes = modes[trial % 4:] + modes[:trial % 4]
            for mode in modes:
                prompt = _format_context_prompt(LLMContext(messages=[{"role": "system", "content": (compact if mode == "runtime_compact" else full) + '\n' + protocol}, {"role": "user", "content": user}]))
                attempts.clear()
                start = time.perf_counter()
                error = None
                answer = ""
                client.base, client.csrf = llm._base_url, llm._csrf_token
                try:
                    if mode == "reference_short":
                        answer = await asyncio.to_thread(client.generate_reply, user)
                    elif mode == "reference_wire_full":
                        status, data = await asyncio.to_thread(client._rpc, "GetModelResponse", {"prompt": prompt, "model": client.chat_model}, 20)
                        if status != 200:
                            raise RuntimeError("Reference request failed")
                        answer = data["response"]
                    else:
                        answer = await llm._generate_gemini(prompt)
                except Exception as exc:
                    error = type(exc).__name__
                rows.append({"mode": mode, "trial": trial, "elapsed_ms": (time.perf_counter()-start)*1000, "prompt_chars": None if mode == "reference_short" else len(prompt), "response_words": len(words(answer)), "response": answer, "error": error, "attempts": list(attempts)})
                (OUT / "llm-benchmark-partial.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2)+"\n")
        return rows
    finally:
        await runtime.close()
        await policy.close()


async def tts_bench(module, repeats):
    rows = []
    text = "I can send the details on WhatsApp. Let me know if you have any questions."
    old_subprocess = module.subprocess
    for trial in range(repeats):
        first = []
        byte_count = [0]
        class Pipe:
            def write(self, data):
                if not first:
                    first.append(time.perf_counter())
                byte_count[0] += len(data)
            def flush(self):
                pass
            def close(self):
                pass
        class Player:
            stdin = Pipe()
            def wait(self):
                return 0
        module.subprocess = SimpleNamespace(Popen=lambda *a, **k: Player(), PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL)
        try:
            started = time.perf_counter()
            await module.NeuralVoiceEngine("Ava")._stream_play_async(text)
            rows.append({"mode": "reference_mp3_arrival", "trial": trial, "first_output_ms": (first[0]-started)*1000, "complete_ms": (time.perf_counter()-started)*1000, "bytes": byte_count[0]})
        finally:
            module.subprocess = old_subprocess
        tts = EdgeTTSService(voice="en-US-AvaNeural", phrase_aggregation=False)
        first_pcm = None
        count = 0
        started = time.perf_counter()
        try:
            async for frame in tts.run_tts(text, "benchmark"):
                if isinstance(frame, ErrorFrame):
                    raise RuntimeError(frame.error)
                if isinstance(frame, TTSAudioRawFrame):
                    first_pcm = first_pcm or time.perf_counter()
                    count += len(frame.audio)
            rows.append({"mode": "runtime_16khz_pcm", "trial": trial, "first_output_ms": (first_pcm-started)*1000, "complete_ms": (time.perf_counter()-started)*1000, "bytes": count})
        finally:
            await tts.cleanup()
    return rows


def reference_asr(module, llm, pcm, language="en-US", common_request=False):
    client = make_reference(module, llm)
    origin = [0.0]
    received = []
    original_json, original_subprocess, original_urllib = module.json, module.subprocess, module.urllib
    def decode(text):
        value = json.loads(text)
        if "transcription" in value:
            item = value["transcription"]
            received.append({"at": time.perf_counter(), "text": item.get("text", ""), "final": item.get("isFinal", False)})
        return value
    class PCM:
        offset = 0
        def read(self, n):
            end = min(len(pcm), self.offset+n)
            time.sleep(max(0, origin[0]+end/32000-time.perf_counter()))
            chunk = pcm[self.offset:end]
            self.offset = end
            return chunk
    class Mic:
        stdout = PCM()
        def terminate(self):
            pass
        def wait(self, **_):
            return 0
        def kill(self):
            pass
    def microphone(*_, **kwargs):
        origin[0] = time.perf_counter()
        return Mic()
    if common_request:
        def request(url, *args, **kwargs):
            if str(url).endswith("/StreamAudioTranscription"):
                body = json.dumps({"mimeType": "audio/l16;rate=16000;channels=1", "continuous": True, "language": language}).encode()
                kwargs["data"] = b"\x00" + len(body).to_bytes(4, "big") + body
            return urllib.request.Request(url, *args, **kwargs)
        module.urllib = SimpleNamespace(request=SimpleNamespace(Request=request, urlopen=urllib.request.urlopen), error=urllib.error)
    module.json = SimpleNamespace(loads=decode, dumps=json.dumps)
    module.subprocess = SimpleNamespace(Popen=microphone, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            text = client.stream_transcribe_microphone(duration=len(pcm)/32000, ptt=False)
        eof = origin[0] + len(pcm)/32000
        return {"text": text, "return_after_eof_ms": (time.perf_counter()-eof)*1000, "hypotheses": [{"after_eof_ms": (e['at']-eof)*1000, "text": e['text'], "final": e['final']} for e in received]}
    finally:
        module.json, module.subprocess, module.urllib = original_json, original_subprocess, original_urllib


async def runtime_asr(llm, pcm, language):
    service = AntigravityLiveSTTService(language=language, base_url=llm._base_url, csrf_token=llm._csrf_token, chunk_duration_ms=200, speculative_pipeline_enabled=True)
    received, commits = [], []
    old_stage = service._stage_transcription
    def stage(text, *, is_final):
        received.append({"at": time.perf_counter(), "text": text, "final": is_final})
        return old_stage(text, is_final=is_final)
    async def capture(frame, *_):
        if isinstance(frame, TranscriptionFrame):
            commits.append({"at": time.perf_counter(), "text": frame.text, "timing": frame.result})
    async def interrupt():
        pass
    service._stage_transcription, service.push_frame, service.broadcast_interruption = stage, capture, interrupt
    try:
        await service._start_session()
        start = time.perf_counter()
        eof = start + len(pcm)/32000
        for offset in range(0, len(pcm)+32000*8, 640):
            chunk = pcm[offset:offset+640] if offset < len(pcm) else bytes(640)
            async for _ in service.run_stt(chunk):
                pass
            await asyncio.sleep(max(0, start+(offset+640)/32000-time.perf_counter()))
            if offset >= len(pcm) and commits:
                break
        return {"text": ' '.join(x['text'] for x in commits), "return_after_eof_ms": (commits[-1]['at']-eof)*1000 if commits else None, "commit_count": len(commits), "hypotheses": [{"after_eof_ms": (e['at']-eof)*1000, "text": e['text'], "final": e['final']} for e in received], "commits": [{**e, "after_eof_ms": (e['at']-eof)*1000} for e in commits]}
    finally:
        await service._close_session()
        service._vad._executor.shutdown(wait=False)
        if service._smart_turn is not None:
            service._smart_turn._executor.shutdown(wait=False)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['llm', 'tts', 'asr', 'whisper'], required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    module = reference_module()
    llm = AntigravityGeminiLLMService(model="gemini-2.5-flash", turn_timeout_secs=20)
    result = {"phase": args.phase, "platform": platform.platform(), "repeats": args.repeats, "methodology": "Synthetic telephone-bandlimited 16kHz PCM, real providers; no calls/messages/microphone/speaker. Reference EOF simulates manual PTT. Metrics are observable arrival points, not perceived audio.", "rows": []}
    try:
        if args.phase in ('llm', 'asr'):
            await llm.start(StartFrame())
        asr_connection = llm
        if args.phase == 'asr':
            discovery = AntigravityLiveSTTService(smart_turn_enabled=False)
            await asyncio.to_thread(discovery._discover_bridge)
            asr_connection = SimpleNamespace(_base_url=discovery._base_url, _csrf_token=discovery._csrf_token, _ssl_ctx=discovery._ssl_ctx)
            result['asr_endpoint_selected_independently'] = discovery._base_url != llm._base_url
            discovery._vad._executor.shutdown(wait=False)
        if args.phase == 'llm':
            result['rows'] = await llm_bench(module, llm, args.repeats)
        elif args.phase == 'tts':
            result['rows'] = await tts_bench(module, args.repeats)
        else:
            with tempfile.TemporaryDirectory(prefix='phoneagent-comparison-') as temp:
                cases = [(case, synthesize(case, Path(temp))) for case in CASES]
                if args.phase == 'whisper':
                    began = time.perf_counter()
                    model = await asyncio.to_thread(load_model, 'large-v3-turbo')
                    result['model_load_ms'] = (time.perf_counter()-began)*1000
                    result['compute_type'] = model.model.compute_type
                    await asyncio.to_thread(transcribe_pcm, cases[0][1], 'large-v3-turbo', 'en-US')
                for trial in range(args.repeats):
                    for case, pcm in cases:
                        ident, language, voice, expected = case
                        common = {'case': ident, 'language': language, 'trial': trial, 'expected': expected, 'audio_ms': len(pcm)/32, 'audio_sha256': hashlib.sha256(pcm).hexdigest()}
                        if args.phase == 'whisper':
                            began = time.perf_counter()
                            hypothesis = await asyncio.to_thread(transcribe_pcm, pcm, 'large-v3-turbo', language)
                            result['rows'].append({**common, 'mode': 'confidence_gated_faster_whisper', 'elapsed_ms': (time.perf_counter()-began)*1000, 'text': hypothesis.text, 'confidence': hypothesis.confidence, 'trusted': hypothesis.trusted_for_task, 'diagnostics': hypothesis.diagnostics, 'wer': wer(expected, hypothesis.text)})
                        else:
                            modes = ["reference_original_request", "runtime_automatic"]
                            if trial % 2:
                                modes.reverse()
                            for mode in modes:
                                try:
                                    measured = (await asyncio.to_thread(reference_asr, module, asr_connection, pcm, language, mode == "reference_default_backend_ptt") if mode.startswith("reference") else await runtime_asr(asr_connection, pcm, language))
                                    row = {**common, "mode": mode, **measured, "wer": wer(expected, measured["text"])}
                                except Exception as exc:
                                    row = {**common, "mode": mode, "error": type(exc).__name__, "error_category": "provider_auth" if "authentication scopes" in str(exc) else "session_failed", "text": "", "return_after_eof_ms": None}
                                result["rows"].append(row)
                                (OUT / f"{args.phase}-benchmark.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
                        (OUT / f'{args.phase}-benchmark.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        (OUT / f'{args.phase}-benchmark.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({'phase': args.phase, 'rows': len(result['rows']), 'output': str(OUT / f'{args.phase}-benchmark.json')}))
    finally:
        await llm.cleanup()


if __name__ == '__main__':
    asyncio.run(main())
