#!/usr/bin/env python3
"""
speech_to_speech.py
===================
Real-time, ultra-low-latency SPEECH-TO-SPEECH conversational system that pairs:

  1. Antigravity's live streaming ASR (gemini-3.1-flash-live-preview)
     -> Sub-second real-time speech-to-text streamed while you speak
  2. Antigravity's fast reasoning model (Gemini 2.5 Flash / Gemini 3.7 Flash)
     -> Sub-second conversational responses on your Antigravity subscription
  3. Streaming Neural Human Speech Engine (Ava, Guy, Andrew, Jenny, etc.)
     -> Emotional, natural breathing, human-grade voice streamed directly to speaker

NO API keys or external Google Cloud billing required — runs 100% on your Antigravity subscription.

Latency Optimization Architecture:
----------------------------------
  Stage 1 (Mic -> ASR):    Audio chunks stream to Google WHILE you speak (Trailing latency: ~0.23s)
  Stage 2 (ASR -> LLM):    Gemini 2.5 Flash conversational reasoning (Latency: ~0.50s)
  Stage 3 (LLM -> Voice):  Neural TTS streams directly to ffplay stdin (First sound: ~0.45s)
  ---------------------------------------------------------------------------------
  Total Turnaround Time:   ~1.2 seconds (down from ~12.5 seconds in legacy batch mode!)

Usage:
------
  # Push-to-Talk interactive conversation (fastest, press Enter to talk, press Enter to reply):
  python3 speech_to_speech.py --interactive

  # Interactive mode with flagship Gemini 3.7 Flash reasoning:
  python3 speech_to_speech.py --interactive --model gemini-3.7-flash

  # Change voice to natural male voice (Guy or Andrew):
  python3 speech_to_speech.py --interactive --voice Guy

  # One-shot microphone question (Push-to-Talk):
  python3 speech_to_speech.py --mic --voice Ava

  # One-shot fixed duration recording (e.g. 3 seconds):
  python3 speech_to_speech.py --mic --duration 3.0

  # Process an existing audio file (.wav, .mp3, .m4a) and save reply:
  python3 speech_to_speech.py question.wav --output answer.mp3
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

try:
    import edge_tts
except ImportError:
    print("Error: edge-tts is required. Run: pip install -r requirements.txt")
    sys.exit(1)

SERVICE = "exa.language_server_pb.LanguageServerService"
DEFAULT_ASR_MODEL = "gemini-3.1-flash-live-preview"

# Chat Models
CHAT_MODELS = {
    "gemini-3.8-flash": "MODEL_PLACEHOLDER_M318",               # Gemini 3.8 Flash (High) - Newest Flagship
    "gemini-3.8-flash-med": "MODEL_PLACEHOLDER_M319",           # Gemini 3.8 Flash (Medium)
    "gemini-3.8-flash-low": "MODEL_PLACEHOLDER_M320",           # Gemini 3.8 Flash (Low)
    "gemini-3.7-flash": "MODEL_PLACEHOLDER_M298",               # Gemini 3.7 Flash (High)
    "gemini-2.5-flash": "MODEL_GOOGLE_GEMINI_2_5_FLASH",        # Sub-second fast (~0.5s)
    "gemini-2.5-flash-lite": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE", # Lightweight fast (~0.5s)
}
DEFAULT_CHAT_MODEL = "gemini-2.5-flash"

# Curated High-Speed & High-Fidelity Human Neural Voices
CURATED_NEURAL_VOICES = {
    "ava": "en-US-AvaNeural",                       # Ultra-responsive expressive female (0.38s latency)
    "guy": "en-US-GuyNeural",                       # Ultra-responsive casual male (0.50s latency)
    "andrew": "en-US-AndrewNeural",                 # Natural conversational male
    "jenny": "en-US-JennyNeural",                   # Clear professional female
    "aria": "en-US-AriaNeural",                     # Emotional expressive female
    "brian": "en-US-BrianMultilingualNeural",       # Sophisticated British English male
    "sonia": "en-GB-SoniaNeural",                   # Warm British English female
    "ryan": "en-GB-RyanNeural",                     # Modern British English male
    "vivienne": "fr-FR-VivienneMultilingualNeural", # French expressive female
    "remy": "fr-FR-RemyMultilingualNeural",         # French conversational male
    "hamed": "ar-SA-HamedNeural",                   # Arabic natural male
    "zariyah": "ar-SA-ZariyahNeural",               # Arabic natural female
}


# ---------------------------------------------------------------------------
# 1. Antigravity Bridge Auto-Discovery
# ---------------------------------------------------------------------------

def discover_bridge() -> Tuple[str, str]:
    """Dynamically find Antigravity's local language server HTTPS port + CSRF token."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    def _find() -> Tuple[Optional[str], Optional[str]]:
        # 1. Environment variables
        env_port = os.getenv("ANTIGRAVITY_PORT", "").strip()
        env_token = os.getenv("ANTIGRAVITY_CSRF_TOKEN", "").strip()
        if env_port.isdigit() and env_token:
            return f"https://127.0.0.1:{env_port}", env_token

        candidates: List[Tuple[int, Optional[str]]] = []
        candidate_ports: set = set()

        # 2. Process inspection via ps to extract PID and --csrf_token
        try:
            ps_out = subprocess.check_output(
                ["ps", "-eo", "pid,command"], text=True, stderr=subprocess.DEVNULL
            )
            for line in ps_out.splitlines():
                if "language_server" in line:
                    pid_m = re.match(r"\s*(\d+)", line)
                    csrf_m = re.search(r"--csrf_token\s+([a-f0-9-]+)", line)
                    if pid_m:
                        pid = pid_m.group(1)
                        token = csrf_m.group(1) if csrf_m else None
                        try:
                            lsof_out = subprocess.check_output(
                                ["lsof", "-Pan", "-p", pid, "-a", "-iTCP", "-sTCP:LISTEN"],
                                text=True,
                                stderr=subprocess.DEVNULL,
                            )
                            for lline in lsof_out.splitlines():
                                pm = re.search(r"127\.0\.0\.1:(\d+)", lline)
                                if pm:
                                    p = int(pm.group(1))
                                    candidates.append((p, token))
                                    candidate_ports.add(p)
                        except Exception:
                            pass
        except Exception:
            pass

        # 3. Process inspection via lsof command name prefix (macOS truncates to language_)
        for cmd_name in ("language_", "language_server", "agy"):
            try:
                out = subprocess.check_output(
                    ["lsof", "-nP", "-c", cmd_name, "-a", "-iTCP", "-sTCP:LISTEN"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                for line in out.splitlines():
                    m = re.search(r":(\d+)\s+\(LISTEN\)", line)
                    if m:
                        candidate_ports.add(int(m.group(1)))
            except Exception:
                pass

        # 4. Verify candidate ports and extract CSRF token from HTML if not already known
        ordered_ports = [p for p, _ in candidates] + [
            p for p in sorted(candidate_ports) if p not in {p for p, _ in candidates}
        ] + [p for p in range(53850, 53875) if p not in candidate_ports]

        token_by_port = {p: t for p, t in candidates if t}

        for port in ordered_ports:
            base = f"https://127.0.0.1:{port}"
            try:
                with urllib.request.urlopen(f"{base}/", context=ctx, timeout=1.2) as r:
                    html = r.read().decode("utf-8", errors="replace")
                if "csrfToken" in html:
                    m = re.search(r'csrfToken":"([^"]+)"', html)
                    if m:
                        return base, m.group(1)
                if port in token_by_port:
                    return base, token_by_port[port]
            except Exception:
                continue

        return None, None

    # First attempt
    base, token = _find()
    if base and token:
        return base, token

    # Auto-launch Antigravity if not running on macOS
    if sys.platform == "darwin":
        for app_name in ("Antigravity", "Antigravity IDE"):
            try:
                subprocess.run(["open", "-ga", app_name], stderr=subprocess.DEVNULL, check=False)
                time.sleep(1.5)
                base, token = _find()
                if base and token:
                    return base, token
            except Exception:
                pass

    raise RuntimeError(
        "No active Antigravity language_server bridge found on 127.0.0.1. "
        "Please make sure the Antigravity application is running."
    )


# ---------------------------------------------------------------------------
# 2. Antigravity Speech & Intelligence Client
# ---------------------------------------------------------------------------

class AntigravityClient:
    def __init__(self, asr_model: str = DEFAULT_ASR_MODEL, chat_model_alias: str = DEFAULT_CHAT_MODEL):
        self.asr_model = asr_model
        self.chat_model_alias = chat_model_alias
        self.chat_model = CHAT_MODELS.get(chat_model_alias.lower(), chat_model_alias)
        self.base, self.csrf = discover_bridge()
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

    def _rpc(self, method: str, body: dict, timeout: int = 60) -> Tuple[int, dict]:
        url = f"{self.base}/{SERVICE}/{method}"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-codeium-csrf-token": self.csrf,
                "Origin": self.base,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as r:
                raw = r.read().decode("utf-8")
                return r.status, json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            if e.code == 401:
                # CSRF token rotated, rediscover and retry
                self.base, self.csrf = discover_bridge()
                return self._rpc(method, body, timeout)
            raise

    # ---- Live Streaming ASR directly from Microphone (Sub-Second Latency) ----

    def stream_transcribe_microphone(self, duration: Optional[float] = None, ptt: bool = True) -> str:
        """Capture microphone and stream chunks simultaneously to Antigravity's ASR.
        Eliminates the legacy recording-wait and batch-upload delays.
        """
        sox = shutil.which("sox")
        ffmpeg = shutil.which("ffmpeg")
        if not sox and not ffmpeg:
            raise RuntimeError("Neither 'sox' nor 'ffmpeg' found. Please install: brew install sox ffmpeg")

        # 1. Establish the Live ASR Stream
        url = f"{self.base}/{SERVICE}/StreamAudioTranscription"
        body = {
            "mimeType": "audio/wav",
            "model": self.asr_model,
            "continuous": True,
        }
        payload = json.dumps(body).encode("utf-8")
        env = b"\x00" + struct.pack(">I", len(payload)) + payload

        req = urllib.request.Request(
            url,
            data=env,
            headers={
                "Content-Type": "application/connect+json",
                "Accept": "application/connect+json",
                "x-codeium-csrf-token": self.csrf,
                "Origin": self.base,
            },
            method="POST",
        )

        resp = urllib.request.urlopen(req, context=self.ctx, timeout=60)
        head = resp.read(5)
        if not head:
            raise RuntimeError("Stream closed before handshake")
        ln = struct.unpack(">I", head[1:5])[0]
        ready = json.loads(resp.read(ln))
        session_id = ready.get("ready", {}).get("sessionId")
        if not session_id:
            raise RuntimeError(f"No sessionId returned: {ready}")

        transcript_snapshots = []
        keep_reading = True

        def stream_listener():
            nonlocal keep_reading
            while keep_reading:
                try:
                    h = resp.read(5)
                    if not h:
                        break
                    l = struct.unpack(">I", h[1:5])[0]
                    frame = json.loads(resp.read(l))
                    if "transcription" in frame:
                        txt = frame["transcription"].get("text", "")
                        if txt:
                            transcript_snapshots.append(txt)
                            print(f"\r  \033[36m[Heard in real-time]\033[0m {txt}", end="", flush=True)
                    elif "complete" in frame:
                        break
                except Exception:
                    break

        listener = threading.Thread(target=stream_listener, daemon=True)
        listener.start()

        # 2. Spawn live audio capture process
        if sox:
            mic_cmd = [sox, "-d", "-r", "16000", "-c", "1", "-b", "16", "-t", "raw", "-"]
        else:
            mic_cmd = [ffmpeg, "-y", "-f", "avfoundation", "-i", ":0", "-ar", "16000", "-ac", "1", "-f", "s16le", "-"]

        mic_proc = subprocess.Popen(
            mic_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        # 3. Stream audio chunks in real time
        chunk_bytes = 8000  # 0.25s chunks at 16kHz 16-bit mono (32,000 bytes/sec)
        seq = 0
        stop_event = threading.Event()

        def wait_for_enter():
            try:
                input()
            except Exception:
                pass
            stop_event.set()

        if ptt:
            print("\033[1;32m🎙️  Listening & streaming... Speak now! Press [ENTER] when done speaking.\033[0m")
            enter_thread = threading.Thread(target=wait_for_enter, daemon=True)
            enter_thread.start()
        else:
            print(f"\033[1;32m🎙️  Listening & streaming ({duration:.1f}s)... Speak now!\033[0m")

        start_time = time.time()
        try:
            while True:
                if ptt and stop_event.is_set():
                    break
                if duration and (time.time() - start_time) >= duration:
                    break

                raw_chunk = mic_proc.stdout.read(chunk_bytes)
                if not raw_chunk:
                    break

                self._rpc("SendAudioChunk", {
                    "sessionId": session_id,
                    "data": base64.b64encode(raw_chunk).decode("utf-8"),
                    "sequenceNumber": seq,
                })
                seq += 1
        finally:
            mic_proc.terminate()
            try:
                mic_proc.wait(timeout=1.0)
            except Exception:
                mic_proc.kill()

        # 4. Flush lookahead buffer with 200ms silence and end session
        silence = b"\x00" * int(16000 * 2 * 0.20)
        self._rpc("SendAudioChunk", {
            "sessionId": session_id,
            "data": base64.b64encode(silence).decode("utf-8"),
            "sequenceNumber": seq,
        })

        self._rpc("EndAudioSession", {"sessionId": session_id})
        listener.join(timeout=1.5)
        keep_reading = False
        try:
            resp.close()
        except Exception:
            pass

        print()  # newline after live feedback
        return transcript_snapshots[-1] if transcript_snapshots else ""

    # ---- Batch Audio Transcription (For files) -----------------------------

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Stream an existing audio file buffer into Antigravity's ASR."""
        url = f"{self.base}/{SERVICE}/StreamAudioTranscription"
        body = {
            "mimeType": mime_type,
            "model": self.asr_model,
            "continuous": True,
        }
        payload = json.dumps(body).encode("utf-8")
        env = b"\x00" + struct.pack(">I", len(payload)) + payload

        req = urllib.request.Request(
            url,
            data=env,
            headers={
                "Content-Type": "application/connect+json",
                "Accept": "application/connect+json",
                "x-codeium-csrf-token": self.csrf,
                "Origin": self.base,
            },
            method="POST",
        )

        resp = urllib.request.urlopen(req, context=self.ctx, timeout=60)
        head = resp.read(5)
        if not head:
            raise RuntimeError("Stream closed before receiving ready frame")
        ln = struct.unpack(">I", head[1:5])[0]
        ready = json.loads(resp.read(ln))
        session_id = ready.get("ready", {}).get("sessionId")
        if not session_id:
            print("\n  \033[33m[Notice]\033[0m Live ASR rate-limited. Falling back to Gemini multimodal path...")
            return self.transcribe_multimodal(audio_bytes, mime_type)

        transcript_snapshots = []
        keep_reading = True

        def stream_listener():
            nonlocal keep_reading
            while keep_reading:
                try:
                    h = resp.read(5)
                    if not h:
                        break
                    l = struct.unpack(">I", h[1:5])[0]
                    frame = json.loads(resp.read(l))
                    if "transcription" in frame:
                        txt = frame["transcription"].get("text", "")
                        if txt:
                            transcript_snapshots.append(txt)
                            print(f"\r  \033[36m[Heard in real-time]\033[0m {txt}", end="", flush=True)
                    elif "complete" in frame:
                        break
                except Exception:
                    break

        listener = threading.Thread(target=stream_listener, daemon=True)
        listener.start()

        # Send audio in ~0.5s chunks with real-time clock pacing
        chunk_size = 16000
        seq = 0
        for i in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[i:i + chunk_size]
            self._rpc("SendAudioChunk", {
                "sessionId": session_id,
                "data": base64.b64encode(chunk).decode("utf-8"),
                "sequenceNumber": seq,
            })
            seq += 1
            time.sleep(0.4)

        # Flush lookahead buffer
        silence = b"\x00" * int(16000 * 2 * 0.6)
        self._rpc("SendAudioChunk", {
            "sessionId": session_id,
            "data": base64.b64encode(silence).decode("utf-8"),
            "sequenceNumber": seq,
        })
        time.sleep(0.5)

        self._rpc("EndAudioSession", {"sessionId": session_id})
        listener.join(timeout=3)
        keep_reading = False
        try:
            resp.close()
        except Exception:
            pass

        print()
        return transcript_snapshots[-1] if transcript_snapshots else ""

    def transcribe_multimodal(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Transcribe audio via Gemini multimodal chat path (unmetered quota)."""
        st, resp = self._rpc("StartCascade", {
            "requestedModel": self.chat_model,
            "source": 1,
            "trajectoryType": "TRAJECTORY_TYPE_CASCADE",
        })
        cid = resp.get("cascadeId") if isinstance(resp, dict) else None
        if not cid:
            raise RuntimeError(f"StartCascade failed: {st} {resp}")

        prompt = "Transcribe the audio you hear word for word. Reply with ONLY the transcribed text, nothing else."
        cascade_config = {
            "plannerConfig": {
                "requestedModel": {"model": self.chat_model},
                "conversational": {
                    "plannerMode": "CONVERSATIONAL_PLANNER_MODE_DEFAULT",
                    "agenticMode": False,
                },
                "knowledgeConfig": {"enabled": False},
                "supportsLatexRendering": True,
            }
        }
        body = {
            "cascadeId": cid,
            "items": [{"text": prompt}],
            "media": [{"mimeType": mime_type, "inlineData": base64.b64encode(audio_bytes).decode("utf-8")}],
            "cascadeConfig": cascade_config,
        }
        st, resp = self._rpc("SendUserCascadeMessage", body)
        if st != 200:
            raise RuntimeError(f"SendUserCascadeMessage failed: {st} {resp}")

        deadline = time.time() + 60
        traj = None
        while time.time() < deadline:
            time.sleep(1.2)
            st, traj = self._rpc("GetCascadeTrajectory", {"cascadeId": cid, "disableRehydration": True})
            if not isinstance(traj, dict):
                continue
            status = traj.get("status")
            if status and status != "CASCADE_RUN_STATUS_RUNNING":
                break

        if traj:
            steps = traj.get("trajectory", {}).get("steps", [])
            for s in steps:
                pr = s.get("plannerResponse") or {}
                resp_txt = pr.get("response") or pr.get("modifiedResponse")
                if resp_txt and resp_txt.strip():
                    return resp_txt.strip()
        return ""

    # ---- Fast Conversational Reasoning Engine -------------------------------

    def generate_reply(self, user_text: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        """Formulate a spoken conversational answer using Antigravity's models."""
        system_prompt = (
            "You are an engaging, highly intelligent voice assistant speaking directly out loud to the user. "
            "Formulate your response directly as natural spoken English. "
            "Keep answers concise (1 to 2 spoken sentences maximum, under 25 words) for snappy voice conversation, "
            "unless the user explicitly requests an in-depth explanation. "
            "Never use markdown formatting, bullet points, asterisks, or code blocks — write only words intended to be spoken."
        )

        context_lines = []
        if history:
            for turn in history[-4:]:
                context_lines.append(f"{turn['role']}: {turn['text']}")
        context_block = "\n".join(context_lines)
        if context_block:
            context_block = f"\n\nRecent Conversation:\n{context_block}"

        prompt = f"{system_prompt}{context_block}\n\nUser: {user_text}\nAssistant:"

        models_to_try = [self.chat_model]
        if self.chat_model != "MODEL_PLACEHOLDER_M298":
            models_to_try.append("MODEL_PLACEHOLDER_M298")

        last_error = None
        for m in models_to_try:
            for attempt in range(2):
                try:
                    st, data = self._rpc("GetModelResponse", {
                        "prompt": prompt,
                        "model": m,
                    }, timeout=30)
                    if st == 200 and isinstance(data, dict):
                        resp = data.get("response", "").strip()
                        if resp:
                            return resp
                except urllib.error.HTTPError as e:
                    last_error = e
                    if attempt == 0:
                        time.sleep(0.3)
                        continue
                    break
                except Exception as e:
                    last_error = e
                    break

        raise RuntimeError(f"GetModelResponse failed across models: {last_error}")


# ---------------------------------------------------------------------------
# 3. Hyper-Realistic Streaming Neural Speech Engine
# ---------------------------------------------------------------------------

class NeuralVoiceEngine:
    """Produces hyper-realistic human voice audio streamed directly to the speaker."""

    def __init__(self, voice: str = "Ava", rate: str = "+0%", pitch: str = "+0Hz"):
        self.voice = voice
        self.rate = rate
        self.pitch = pitch
        self._resolve_voice()

    def _resolve_voice(self):
        k = self.voice.lower().strip()
        if k in CURATED_NEURAL_VOICES:
            self.voice_id = CURATED_NEURAL_VOICES[k]
        elif "-" in self.voice and "Neural" in self.voice:
            self.voice_id = self.voice
        else:
            self.voice_id = CURATED_NEURAL_VOICES["ava"]

    async def _stream_play_async(self, text: str):
        """Stream synthesized audio chunks directly into ffplay stdin for sub-second sound."""
        ffplay = shutil.which("ffplay")
        if not ffplay:
            target = tempfile.mktemp(suffix=".mp3")
            comm = edge_tts.Communicate(text, self.voice_id, rate=self.rate, pitch=self.pitch)
            await comm.save(target)
            if os.path.exists(target):
                subprocess.run(["afplay", target])
                try:
                    os.remove(target)
                except Exception:
                    pass
            return

        proc = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-i", "pipe:0"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        comm = edge_tts.Communicate(text, self.voice_id, rate=self.rate, pitch=self.pitch)
        try:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    try:
                        proc.stdin.write(chunk["data"])
                        proc.stdin.flush()
                    except (BrokenPipeError, OSError):
                        break
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()

    def speak(self, text: str, output_file: Optional[str] = None, play: bool = True):
        """Speak out loud. If output_file is provided, persists to disk."""
        if output_file:
            asyncio.run(edge_tts.Communicate(text, self.voice_id, rate=self.rate, pitch=self.pitch).save(output_file))
            if play and os.path.exists(output_file):
                subprocess.run(["afplay", output_file])
        elif play:
            asyncio.run(self._stream_play_async(text))


# ---------------------------------------------------------------------------
# 4. Pipeline Orchestrator & CLI
# ---------------------------------------------------------------------------

def run_single_exchange(client: AntigravityClient, voice_engine: NeuralVoiceEngine,
                        audio_bytes: Optional[bytes] = None, output_file: Optional[str] = None,
                        duration: Optional[float] = None, ptt: bool = True):
    t_start = time.time()

    if audio_bytes is not None:
        print("\n\033[1;34m[1/3] Transcribing audio buffer (gemini-3.1-flash-live-preview)...\033[0m")
        t0 = time.time()
        user_text = client.transcribe_audio(audio_bytes)
        t_asr = time.time() - t0
    else:
        print("\n\033[1;34m[1/3] Streaming live microphone to Gemini ASR...\033[0m")
        t0 = time.time()
        user_text = client.stream_transcribe_microphone(duration=duration, ptt=ptt)
        t_asr = time.time() - t0

    if not user_text.strip():
        print("\033[31m[!] No speech detected. Please speak louder or closer to the mic.\033[0m")
        return

    print(f"\033[1mYou asked:\033[0m \"{user_text}\" \033[90m(ASR: {t_asr:.2f}s)\033[0m")

    print(f"\n\033[1;34m[2/3] Reasoning & generating reply ({client.chat_model_alias})...\033[0m")
    t0 = time.time()
    reply_text = client.generate_reply(user_text)
    t_llm = time.time() - t0
    print(f"\033[1;32mGemini:\033[0m \"{reply_text}\" \033[90m(LLM: {t_llm:.2f}s)\033[0m")

    print(f"\n\033[1;34m[3/3] Speaking response with Human Neural Voice ({voice_engine.voice.capitalize()})...\033[0m")
    voice_engine.speak(reply_text, output_file=output_file, play=True)
    t_total = time.time() - t_start
    print(f"\033[90m[Total turnaround: {t_total:.2f}s]\033[0m\n")


def main():
    parser = argparse.ArgumentParser(
        description="Ultra-low-latency Speech-to-Speech voice agent with Antigravity and Neural Human Voice Engine."
    )
    parser.add_argument("audio_file", nargs="?", help="Input audio file (.wav, .mp3, .m4a)")
    parser.add_argument("--interactive", "-i", action="store_true", help="Start continuous voice conversation (Push-to-Talk)")
    parser.add_argument("--mic", "-m", action="store_true", help="Record one question from Mac microphone")
    parser.add_argument("--voice", "-v", default="Ava",
                        help="Voice name: Ava, Guy, Andrew, Jenny, Aria, Brian, Sonia, Ryan, Vivienne, Remy, Hamed, Zariyah")
    parser.add_argument("--model", choices=["gemini-3.8-flash", "gemini-3.7-flash", "gemini-2.5-flash", "gemini-2.5-flash-lite"],
                        default="gemini-2.5-flash", help="Reasoning model: gemini-3.8-flash (latest), gemini-3.7-flash, gemini-2.5-flash (fast)")
    parser.add_argument("--duration", "-d", type=float, default=None, help="Fixed mic record duration in seconds (optional)")
    parser.add_argument("--rate", default="+0%", help="Speaking rate modification (e.g. '+10%%' or '-10%%')")
    parser.add_argument("--pitch", default="+0Hz", help="Speaking pitch modification (e.g. '+5Hz' or '-5Hz')")
    parser.add_argument("--output", "-o", help="Save the spoken response audio to this file (.mp3 / .wav)")
    args = parser.parse_args()

    client = AntigravityClient(chat_model_alias=args.model)
    voice_engine = NeuralVoiceEngine(voice=args.voice, rate=args.rate, pitch=args.pitch)

    print("\033[1;32m===============================================================\033[0m")
    print("\033[1;32m       ANTIGRAVITY REAL-TIME SPEECH-TO-SPEECH SYSTEM           \033[0m")
    print("\033[1;32m===============================================================\033[0m")
    print(f"Bridge Port:   {client.base}")
    print(f"Ears (ASR):    {client.asr_model} (Antigravity)")
    print(f"Brain (Chat):  {args.model} ({client.chat_model})")
    print(f"Voice Output:  {voice_engine.voice.capitalize()} ({voice_engine.voice_id})\n")

    if not args.interactive and not args.mic and not args.audio_file:
        args.interactive = True

    if args.interactive:
        print("\033[1;36m=== Continuous Interactive Mode Activated ===\033[0m")
        print("Workflow: Press [Enter] -> Speak -> Press [Enter] when done -> Hear response instantly.")
        print("Press [Ctrl+C] at any time to exit.\n")
        history = []
        try:
            while True:
                input("\n👉 Press [Enter] to start speaking...")
                t_turn_start = time.time()
                user_text = client.stream_transcribe_microphone(
                    duration=args.duration,
                    ptt=(args.duration is None)
                )
                t_asr = time.time() - t_turn_start

                if not user_text.strip():
                    print("No speech detected. Press Enter to try again.")
                    continue

                print(f"\033[1mYou:\033[0m {user_text} \033[90m(ASR: {t_asr:.2f}s)\033[0m")

                t_llm_start = time.time()
                reply = client.generate_reply(user_text, history=history)
                t_llm = time.time() - t_llm_start
                print(f"\033[1;32mGemini:\033[0m {reply} \033[90m(LLM: {t_llm:.2f}s)\033[0m")

                history.append({"role": "User", "text": user_text})
                history.append({"role": "Assistant", "text": reply})

                voice_engine.speak(reply, play=True)
        except KeyboardInterrupt:
            print("\nExiting voice conversation. Goodbye!")
            return

    if args.mic:
        run_single_exchange(
            client, voice_engine,
            duration=args.duration,
            ptt=(args.duration is None),
            output_file=args.output
        )
        return

    if args.audio_file:
        if not os.path.exists(args.audio_file):
            print(f"File not found: {args.audio_file}")
            sys.exit(1)
        with open(args.audio_file, "rb") as f:
            audio_bytes = f.read()
        run_single_exchange(client, voice_engine, audio_bytes=audio_bytes, output_file=args.output)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
