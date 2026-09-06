"""Antigravity Live STT Service for Pipecat.

Streams 16kHz PCM audio from the cellular downlink into Google's speech
recognition engine via the local Antigravity Language Server bridge.
Includes adaptive silence-watchdog endpointing for sub-second turn latency.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import logging
import math
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import AsyncGenerator, Awaitable, Callable

import aiohttp
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import STTService

from .streaming_silero_vad import StreamingSileroVADAnalyzer
from .turn_continuity import looks_semantically_incomplete

logger = logging.getLogger("AntigravityLiveSTT")

SERVICE = "exa.language_server_pb.LanguageServerService"
CONNECT_JSON = "application/connect+json"
APP_JSON = "application/json"

SpeculationCandidateHandler = Callable[[str], Awaitable[None] | None]
SpeculationCancelHandler = Callable[[str], Awaitable[None] | None]

_ENGLISH_LANGUAGE_MARKERS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "can",
        "do",
        "for",
        "hello",
        "how",
        "i",
        "is",
        "like",
        "matches",
        "no",
        "please",
        "thanks",
        "the",
        "this",
        "watch",
        "we",
        "what",
        "why",
        "with",
        "would",
        "yes",
        "you",
        "your",
    }
)
_FRENCH_LANGUAGE_MARKERS = frozenset(
    {
        "avec",
        "bonjour",
        "comment",
        "dans",
        "de",
        "des",
        "du",
        "est",
        "et",
        "je",
        "la",
        "le",
        "les",
        "matchs",
        "merci",
        "non",
        "nous",
        "oui",
        "pour",
        "pourquoi",
        "que",
        "qui",
        "sur",
        "une",
        "vous",
        "votre",
    }
)


def _calc_dbfs(audio: bytes) -> float:
    """Calculate RMS dBFS of 16-bit mono PCM."""
    if len(audio) < 2:
        return -120.0
    samples = memoryview(audio).cast("h")
    if not samples:
        return -120.0
    sum_sq = sum(s * s for s in samples)
    mean_sq = sum_sq / len(samples)
    if mean_sq <= 0:
        return -120.0
    return 20.0 * math.log10(math.sqrt(mean_sq) / 32768.0)


class _StreamConn:
    """De-chunks Connect-gRPC server streams over raw TLS."""

    def __init__(self, sock: ssl.SSLSocket):
        self.sock = sock
        self.status = 0
        self.headers: dict[str, str] = {}
        self._rbuf = bytearray()
        self._body = bytearray()
        self._chunk_left = 0
        self._eof = False
        self._read_head()

    def _read_head(self) -> None:
        while b"\r\n\r\n" not in self._rbuf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Stream closed before HTTP head")
            self._rbuf.extend(chunk)
        head, rest = self._rbuf.split(b"\r\n\r\n", 1)
        self._rbuf = bytearray(rest)
        lines = head.split(b"\r\n")
        parts = lines[0].split(b" ", 2)
        self.status = int(parts[1]) if len(parts) > 1 else 0
        for ln in lines[1:]:
            if b":" in ln:
                k, v = ln.split(b":", 1)
                self.headers[k.decode("latin1").strip().lower()] = v.decode("latin1").strip()

    def _fill(self, want: int) -> bool:
        while len(self._body) < want and not self._eof:
            if self._chunk_left <= 0:
                while b"\r\n" not in self._rbuf:
                    chunk = self.sock.recv(4096)
                    if not chunk:
                        self._eof = True
                        return len(self._body) >= want
                    self._rbuf.extend(chunk)
                line, rest = self._rbuf.split(b"\r\n", 1)
                self._rbuf = bytearray(rest)
                if not line.strip():
                    continue
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    continue
                if size == 0:
                    self._eof = True
                    break
                self._chunk_left = size
            take = min(want - len(self._body), self._chunk_left)
            while len(self._rbuf) < take and not self._eof:
                chunk = self.sock.recv(4096)
                if not chunk:
                    self._eof = True
                    break
                self._rbuf.extend(chunk)
            actual = min(len(self._rbuf), take)
            self._body.extend(self._rbuf[:actual])
            del self._rbuf[:actual]
            self._chunk_left -= actual
        return len(self._body) >= want

    def read_envelope(self, timeout: float = 1.0) -> tuple[int | None, bytes | None]:
        self.sock.settimeout(timeout)
        try:
            if not self._fill(5):
                return None, None
            # Keep the header until its entire payload is buffered. A socket
            # timeout is resumable; consuming the header first made the next
            # read interpret partial JSON as a new envelope header.
            head = bytes(self._body[:5])
            flag = head[0]
            length = struct.unpack(">I", head[1:5])[0]
            if length > 1_048_576:
                raise ConnectionError("ASR envelope exceeds the supported size")
            if not self._fill(5 + length):
                if self._eof:
                    raise ConnectionError("ASR stream ended inside an envelope")
                return None, None
            payload = bytes(self._body[5:5 + length])
            del self._body[:5 + length]
            return flag, payload
        except TimeoutError:
            return None, None

    @property
    def eof(self) -> bool:
        return self._eof

    def close(self) -> None:
        self._eof = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class AntigravityLiveSTTService(STTService):
    """Production Pipecat STT Adapter for Antigravity's Live Speech Stream."""

    recognition_preroll_secs = 2.0

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        language: str = "en-US",
        chunk_duration_ms: int = 150,
        silence_endpoint_ms: int = 600,
        incomplete_endpoint_ms: int = 3000,
        transcript_stability_ms: int = 100,
        partial_transcript_stability_ms: int = 650,
        fallback_endpoint_ms: int = 900,
        barge_in_min_ms: int = 220,
        energy_threshold_dbfs: float = -42.0,
        context_bias: str = "",
        speculative_pipeline_enabled: bool = False,
        speculative_prefetch_silence_ms: int = 120,
        speculative_prefetch_stability_ms: int = 80,
        speculative_fast_endpoint_ms: int = 600,
        speculative_ambiguous_endpoint_ms: int = 900,
        speculative_incomplete_endpoint_ms: int = 3000,
        smart_turn_enabled: bool = True,
        smart_turn_model_path: str | None = None,
        smart_turn_completion_threshold: float = 0.5,
        vad_analyzer: Any | None = None,
        base_url: str = "",
        csrf_token: str = "",
        **kwargs: Any,
    ) -> None:
        # Crucial: audio_passthrough=False ensures caller voice NEVER echoes into output transport
        super().__init__(
            audio_passthrough=False,
            sample_rate=sample_rate,
            settings=STTSettings(model=None, language=language),
            **kwargs,
        )
        self._target_sample_rate = sample_rate
        self._language = language
        self._chunk_bytes = max(640, int(sample_rate * 2 * (chunk_duration_ms / 1000.0)))
        self._silence_endpoint_sec = silence_endpoint_ms / 1000.0
        self._incomplete_endpoint_sec = incomplete_endpoint_ms / 1000.0
        self._transcript_stability_sec = transcript_stability_ms / 1000.0
        self._partial_transcript_stability_sec = max(transcript_stability_ms, partial_transcript_stability_ms) / 1000.0
        self._fallback_endpoint_sec = fallback_endpoint_ms / 1000.0
        self._barge_in_min_sec = barge_in_min_ms / 1000.0
        self._energy_threshold_dbfs = energy_threshold_dbfs
        self._context_bias = context_bias
        self._speculative_pipeline_enabled = speculative_pipeline_enabled
        self._speculative_prefetch_silence_sec = speculative_prefetch_silence_ms / 1000.0
        self._speculative_prefetch_stability_sec = speculative_prefetch_stability_ms / 1000.0
        self._speculative_fast_endpoint_sec = speculative_fast_endpoint_ms / 1000.0
        self._speculative_ambiguous_endpoint_sec = speculative_ambiguous_endpoint_ms / 1000.0
        self._speculative_incomplete_endpoint_sec = speculative_incomplete_endpoint_ms / 1000.0
        self._speculation_candidate_handler: SpeculationCandidateHandler | None = None
        self._speculation_cancel_handler: SpeculationCancelHandler | None = None
        self._turn_recovery_handler: Callable[[], Awaitable[None]] | None = None
        self._last_speculation_text = ""
        self._audio_buffer = bytearray()
        self._seq = 0
        self._session_id: str | None = None
        self._stream: _StreamConn | None = None
        self._reader_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_enabled = False
        self._recognition_unavailable = False
        self._recognition_gap = False
        self._reconnect_budget_sec = 12.0
        self._reconnect_attempts = 0
        self._reconnect_deadline = 0.0
        self._transport_revision = 0
        self._watchdog_task: asyncio.Task | None = None
        self._sender_task: asyncio.Task | None = None
        self._send_queue: asyncio.Queue[tuple[bytes, int, str]] = asyncio.Queue(maxsize=64)
        self._transcript_lock = asyncio.Lock()
        self._base_url = base_url
        self._csrf_token = csrf_token
        self._ssl_ctx = self._create_ssl_context()
        self._http_session: aiohttp.ClientSession | None = None
        self._speaking = False
        self._bot_speaking = False
        self._bot_speaking_since = 0.0
        self._last_speech_at = 0.0
        self._last_transcript = ""
        self._final_transcript = ""
        self._provider_final_seen = False
        self._partial_needs_confirmation = False
        self._partial_confirmations = 0
        self._partial_confirmation_signature: tuple[int, float] | None = None
        self._heard_negation = False
        self._negation_revision_uncertain = False
        self._last_transcript_update_at = 0.0
        self._last_committed_text = ""
        self._last_committed_at = 0.0
        self._last_committed_speech_epoch = -1
        self._empty_resolved_speech_epoch = 0
        self._last_provider_text = ""
        self._retired_provider_text = ""
        self._retired_provider_pending_final = False
        self._speech_epoch = 0
        self._candidate_speech_epoch = 0
        self._speech_burst_active = False
        self._lookahead_flushed_epoch = -1
        self._late_revision_window_sec = 2.5
        self._language_switch_endpoint_sec = 1.6
        self._speech_candidate_at = 0.0
        self._is_closing = False
        # This adapter owns external turn frames. Acoustic onset must therefore
        # invalidate replies locally, without waiting for the ASR service.
        self._vad = vad_analyzer or StreamingSileroVADAnalyzer(
            sample_rate=sample_rate,
            params=VADParams(start_secs=0.12, stop_secs=0.20, confidence=0.7, min_volume=0.0),
        )
        self._vad.set_sample_rate(sample_rate)
        self._audio_inflight = False
        self._send_inflight = False
        self._last_audio_at = 0.0
        self._received_silence_sec = 0.0
        self._acoustic_active = False
        self._vad_confirmed_active = False
        self._floor_claimed = False
        self._turn_revision = 0
        self._interrupted_speech_epoch = -1
        if not 0 < smart_turn_completion_threshold <= 1:
            raise ValueError("smart_turn_completion_threshold must be in (0, 1]")
        self._smart_turn_completion_threshold = smart_turn_completion_threshold
        self._smart_turn_enabled = smart_turn_enabled
        self._smart_turn_model_path = smart_turn_model_path
        self._smart_turn: Any | None = None
        self._smart_turn_executor: ThreadPoolExecutor | None = None
        self._recent_pcm = bytearray()
        self._smart_turn_incomplete = False
        self._smart_turn_pending = False
        self._smart_turn_decision_epoch = -1
        self._smart_turn_decision_update_at = -1.0
        self._smart_turn_decision: bool | None = None
        self._smart_turn_task: asyncio.Task[None] | None = None

        if self._smart_turn_enabled:
            try:
                from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
                    LocalSmartTurnAnalyzerV3,
                )

                self._smart_turn = LocalSmartTurnAnalyzerV3(
                    smart_turn_model_path=smart_turn_model_path or None,
                    sample_rate=self._target_sample_rate,
                )
                self._smart_turn.set_sample_rate(self._target_sample_rate)
                self._smart_turn_executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="smart_turn"
                )
                logger.info("Local Smart Turn v3.2 ONNX analyzer initialized for acoustic prosody")
            except Exception as exc:
                logger.warning("Smart Turn v3.2 ONNX analyzer could not be loaded: %s", exc)
                self._smart_turn = None

    def set_speculation_handlers(
        self,
        candidate_handler: SpeculationCandidateHandler | None,
        cancel_handler: SpeculationCancelHandler | None,
    ) -> None:
        """Attach optional non-blocking speculative turn orchestration hooks."""

        self._speculation_candidate_handler = candidate_handler
        self._speculation_cancel_handler = cancel_handler

    def set_turn_recovery_handler(self, handler: Callable[[], Awaitable[None]]) -> None:
        self._turn_recovery_handler = handler

    async def _invoke_speculation_handler(
        self,
        handler: SpeculationCandidateHandler | SpeculationCancelHandler | None,
        value: str,
    ) -> None:
        if handler is None:
            return
        result = handler(value)
        if inspect.isawaitable(result):
            await result

    def _create_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _discover_bridge(self) -> None:
        if self._base_url and self._csrf_token:
            return

        def _scan() -> tuple[str | None, str | None]:
            # 1. Environment variables
            env_port = os.getenv("ANTIGRAVITY_PORT", "").strip()
            env_token = os.getenv("ANTIGRAVITY_CSRF_TOKEN", "").strip()
            if env_port.isdigit() and env_token:
                return f"https://127.0.0.1:{env_port}", env_token

            candidates: list[tuple[int, str | None]] = []
            candidate_ports: set[int] = set()

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

            # 3. Process inspection via lsof command name prefix
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

            ordered_ports = [p for p, _ in candidates] + [
                p for p in sorted(candidate_ports) if p not in {p for p, _ in candidates}
            ] + [p for p in range(53850, 53875) if p not in candidate_ports]

            token_by_port = {p: t for p, t in candidates if t}

            for port in ordered_ports:
                url = f"https://127.0.0.1:{port}/"
                try:
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=1.2) as r:
                        html = r.read().decode(errors="replace")
                    if "csrfToken" in html:
                        m = re.search(r'csrfToken":"([^"]+)"', html)
                        if m:
                            return f"https://127.0.0.1:{port}", m.group(1)
                    if port in token_by_port:
                        return f"https://127.0.0.1:{port}", token_by_port[port]
                except Exception:
                    continue

            return None, None

        base, token = _scan()
        if not base or not token:
            if sys.platform == "darwin":
                for app_name in ("Antigravity", "Antigravity IDE"):
                    try:
                        launched = subprocess.run(["open", "-ga", app_name], stderr=subprocess.DEVNULL, check=False)
                        if launched.returncode:
                            continue
                        deadline = time.monotonic() + 6.0
                        while time.monotonic() < deadline:
                            time.sleep(0.25)
                            base, token = _scan()
                            if base and token:
                                break
                        if base and token:
                            break
                    except Exception:
                        pass

        if base and token:
            self._base_url = base
            self._csrf_token = token
            logger.info("Found Antigravity bridge on %s", self._base_url)
            return

        raise RuntimeError("Antigravity language_server bridge not found on 127.0.0.1")

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        try:
            if not self._base_url or not self._csrf_token:
                await asyncio.to_thread(self._discover_bridge)
            await self._start_session()
        except Exception:
            await self.push_error(error_msg="Speech recognition could not start; check the configured bridge or model", fatal=True)
            raise

    def _reset_turn_state(self) -> None:
        """Reset the acoustic/turn controller independently of the ASR transport."""
        self._seq = 0
        self._audio_buffer.clear()
        self._speaking = False
        self._bot_speaking = False
        self._bot_speaking_since = 0.0
        self._last_speech_at = time.monotonic()
        self._last_transcript = ""
        self._final_transcript = ""
        self._provider_final_seen = False
        self._reset_recognition_evidence()
        self._last_transcript_update_at = 0.0
        self._last_committed_text = ""
        self._last_committed_at = 0.0
        self._last_committed_speech_epoch = -1
        self._empty_resolved_speech_epoch = 0
        self._last_provider_text = ""
        self._retired_provider_text = ""
        self._retired_provider_pending_final = False
        self._speech_epoch = 0
        self._candidate_speech_epoch = 0
        self._speech_burst_active = False
        self._lookahead_flushed_epoch = -1
        self._speech_candidate_at = 0.0
        self._last_speculation_text = ""
        self._acoustic_active = False
        self._vad_confirmed_active = False
        self._floor_claimed = False
        self._audio_inflight = False
        self._last_audio_at = 0.0
        self._received_silence_sec = 0.0
        self._send_inflight = False
        self._turn_revision += 1
        self._interrupted_speech_epoch = -1
        self._smart_turn_decision = None
        self._smart_turn_decision_epoch = -1
        self._smart_turn_decision_update_at = -1.0
        self._smart_turn_incomplete = False
        self._recent_pcm.clear()
        self._vad.set_sample_rate(self._target_sample_rate)
        if isinstance(self._vad, StreamingSileroVADAnalyzer):
            self._vad.reset_stream()
        elif isinstance(self._vad, SileroVADAnalyzer):
            self._vad._vad_buffer = b""
            self._vad._model.reset_states()
        if self._smart_turn is not None and self._smart_turn_executor is None:
            self._smart_turn_executor = ThreadPoolExecutor(max_workers=1)
        self._send_queue = asyncio.Queue(maxsize=64)
        self._is_closing = False

    async def _start_session(self) -> None:
        self._reset_turn_state()
        if self._http_session is None or self._http_session.closed:
            connector = aiohttp.TCPConnector(ssl=self._ssl_ctx, limit=10, keepalive_timeout=60)
            self._http_session = aiohttp.ClientSession(connector=connector)
        stream, session_id = await self._open_session_transport()
        self._stream, self._session_id = stream, session_id
        self._recognition_unavailable = self._recognition_gap = False
        self._reconnect_enabled = True
        self._reconnect_attempts = 0
        self._reader_task = asyncio.create_task(self._stream_reader_loop(), name="ag_stt_reader")
        self._watchdog_task = asyncio.create_task(
            self._silence_watchdog_loop(), name="ag_stt_watchdog"
        )
        self._sender_task = asyncio.create_task(self._audio_sender_loop(), name="ag_stt_sender")

    async def _open_session_transport(self) -> tuple[_StreamConn, str]:
        """Open transport without resetting acoustic epochs or pending floor ownership."""
        body: dict[str, Any] = {
            "mimeType": "audio/l16;rate=16000;channels=1",
            "continuous": True,
            "language": self._language or "en-US",
        }
        if self._context_bias:
            body["preCursorText"] = self._context_bias

        payload = json.dumps(body).encode()
        envelope = b"\x00" + struct.pack(">I", len(payload)) + payload

        url = urllib.parse.urlsplit(self._base_url)
        host, port = url.hostname or "127.0.0.1", url.port or 53857

        def open_socket() -> tuple[_StreamConn, str]:
            sock = socket.create_connection((host, port), timeout=5)
            stream = None
            try:
                sock = self._ssl_ctx.wrap_socket(sock, server_hostname=host)
                headers = {
                    "Content-Type": CONNECT_JSON,
                    "Accept": CONNECT_JSON,
                    "x-codeium-csrf-token": self._csrf_token,
                    "Origin": self._base_url,
                    "Content-Length": str(len(envelope)),
                }
                head = (
                    f"POST /{SERVICE}/StreamAudioTranscription HTTP/1.1\r\n"
                    f"Host: {host}:{port}\r\n"
                    + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
                ).encode()
                sock.sendall(head + envelope)
                stream = _StreamConn(sock)
                if stream.status != 200:
                    raise ConnectionError(f"STT handshake HTTP {stream.status}")
                flag, data = stream.read_envelope(10.0)
                if flag != 0 or not data:
                    raise ConnectionError("No STT ready envelope")
                session_id = json.loads(data).get("ready", {}).get("sessionId")
                if not isinstance(session_id, str) or not session_id:
                    raise ConnectionError("Missing STT session ID")
                return stream, session_id
            except BaseException:
                if stream is not None:
                    stream.close()
                else:
                    sock.close()
                raise

        # A cancelled to_thread await does not stop socket creation. Retain
        # ownership until it finishes and close any late successful socket.
        opening = asyncio.create_task(asyncio.to_thread(open_socket))
        try:
            return await asyncio.shield(opening)
        except asyncio.CancelledError:
            def close_late(task: asyncio.Task[tuple[_StreamConn, str]]) -> None:
                if not task.cancelled() and task.exception() is None:
                    task.result()[0].close()
            opening.add_done_callback(close_late)
            raise

    async def _fail_transport(self, stream: _StreamConn | None, *, source: str) -> None:
        """Retire the damaged session before any awaited notification or reconnect."""
        if self._is_closing or stream is None or stream is not self._stream:
            return
        self._stream = None
        self._session_id = None
        if self._reconnect_attempts == 0:
            self._reconnect_deadline = time.monotonic() + self._reconnect_budget_sec
        self._recognition_unavailable = True
        self._transport_revision += 1
        self._recognition_gap = self._recognition_gap or self.caller_owns_floor() or self._acoustic_active
        self._audio_buffer.clear()
        while not self._send_queue.empty():
            self._send_queue.get_nowait()
            self._send_queue.task_done()
        stream.close()
        async with self._transcript_lock:
            self._turn_revision += 1
            self._reset_recognition_evidence()
            self._last_transcript = self._final_transcript = ""
            self._provider_final_seen = False
            self._last_transcript_update_at = 0.0
            self._last_provider_text = self._retired_provider_text = ""
            self._retired_provider_pending_final = False
            self._last_speculation_text = ""
        await self._invoke_speculation_handler(self._speculation_cancel_handler, "stt_transport_failed")
        await self.push_error(error_msg=f"STT {source} failed; recognition session is being recovered")
        if self._reconnect_enabled and not self._is_closing:
            # Only the task that retired the current stream reaches here.
            self._reconnect_task = asyncio.create_task(self._reconnect_transport(), name="ag_stt_reconnect")

    async def _reconnect_transport(self) -> None:
        started = time.monotonic()
        try:
            async with asyncio.timeout_at(self._reconnect_deadline):
                while self._reconnect_attempts < 3:
                    if self._is_closing:
                        return
                    self._reconnect_attempts += 1
                    attempt = self._reconnect_attempts
                    try:
                        stream, session_id = await self._open_session_transport()
                    except Exception:
                        logger.warning("STT reconnect attempt %d failed", attempt)
                        if attempt == 3:
                            break
                        await asyncio.sleep(0.25 * attempt)
                        continue
                    if self._is_closing:
                        stream.close()
                        return
                    self._stream, self._session_id = stream, session_id
                    self._seq = 0
                    self._audio_buffer.clear()
                    self._recognition_unavailable = False
                    if not self.caller_owns_floor() and not self._acoustic_active and not self._audio_inflight:
                        self._recognition_gap = False
                    self._reader_task = asyncio.create_task(self._stream_reader_loop(), name="ag_stt_reader")
                    logger.info("STT transport recovered in %.1f ms", (time.monotonic() - started) * 1000)
                    return
        except asyncio.CancelledError:
            return
        except TimeoutError:
            logger.warning("STT reconnect budget exhausted")
        if not self._is_closing:
            await self.push_error(error_msg="Speech recognition connection could not be restored", fatal=True)

    async def _stream_reader_loop(self) -> None:
        """Continuously reads incoming transcription envelopes and pushes Pipecat frames."""
        try:
            while self._stream is not None and not self._is_closing:
                stream = self._stream
                flag, payload = await asyncio.to_thread(stream.read_envelope, 0.1)
                if stream is not self._stream or self._is_closing:
                    return
                if payload is None:
                    if stream.eof:
                        raise ConnectionError("ASR stream ended without completion")
                    continue
                obj = json.loads(payload)
                if not isinstance(obj, dict):
                    raise ConnectionError("ASR stream returned an invalid object")
                if flag == 2 or "error" in obj:
                    raise ConnectionError("ASR stream returned an end/error envelope")
                if "transcription" in obj:
                    t = obj["transcription"]
                    text = t.get("text", "").strip()
                    is_final = bool(t.get("isFinal"))
                    if text:
                        await self._handle_provider_transcription(text, is_final=is_final)
                elif "complete" in obj:
                    await self._commit_pending_transcript(
                        source="stream_complete",
                        require_provider_final=True,
                    )
                    if self._reconnect_enabled:
                        raise ConnectionError("Continuous STT session completed unexpectedly")
                    break
        except asyncio.CancelledError:
            pass
        except Exception:
            if not self._is_closing:
                logger.warning("Antigravity STT reader stopped unexpectedly")
                await self._fail_transport(stream, source="reader")

    def _reset_recognition_evidence(self) -> None:
        self._partial_needs_confirmation = False
        self._partial_confirmations = 0
        self._partial_confirmation_signature = None
        self._heard_negation = False
        self._negation_revision_uncertain = False

    @staticmethod
    def _contains_negation(text: str) -> bool:
        text = text.casefold().replace("\u2019", "'")
        return bool(re.search(
            r"\b(?:no|not|never|neither|nor|without|cannot|non|ne|pas|jamais|rien|aucun|aucune|sans)\b|\b\w+n't\b",
            text,
        ))

    def _partial_is_confirmed(self) -> bool:
        return not self._partial_needs_confirmation or (
            self._partial_confirmations >= 2
            and self._partial_confirmation_signature == (self._speech_epoch, self._last_speech_at)
        )

    async def _handle_provider_transcription(self, text: str, *, is_final: bool) -> str:
        """Accept provider text only for an open, acoustically detected turn."""
        async with self._transcript_lock:
            if (self._is_closing or self._recognition_unavailable or self._recognition_gap
                    or self._speech_epoch <= self._empty_resolved_speech_epoch):
                return ""
            previous = self._last_transcript
            candidate = self._stage_transcription(text, is_final=is_final)
            if candidate:
                signature = (self._speech_epoch, self._last_speech_at)
                if not is_final:
                    self._partial_needs_confirmation = True
                    same = candidate == previous and signature == self._partial_confirmation_signature
                    self._partial_confirmations = self._partial_confirmations + 1 if same else 1
                    self._partial_confirmation_signature = signature
                negation = self._contains_negation(candidate)
                self._heard_negation = self._heard_negation or negation
                # A true negation retraction occurs on short decisions/refusals (e.g. "I can't accept" -> "I can accept",
                # "No, not the plan" -> "The plan"). An articulate multi-word inquiry (e.g. "I need more information on what
                # this can add value to me and to my project") where a transient phoneme jitter occurred during interim streaming
                # is not a negation reversal.
                is_substantive_inquiry = len(candidate.split()) >= 8 or candidate.casefold().startswith(
                    ("can you", "could you", "i need more", "how does", "what is", "tell me", "pourriez-vous", "pouvez-vous", "j'ai besoin")
                )
                if is_substantive_inquiry:
                    self._negation_revision_uncertain = False
                else:
                    self._negation_revision_uncertain = self._heard_negation and not negation
            transport_revision = self._transport_revision
        if candidate:
            self._reconnect_attempts = 0  # A working recognition cycle replenishes the retry budget.
            await self._ensure_user_started(force=False)
            if self._is_closing or transport_revision != self._transport_revision:
                return ""
            await self.push_frame(InterimTranscriptionFrame(text=candidate, user_id="caller", timestamp=""))
        return candidate

    async def _ensure_user_started(self, *, force: bool) -> None:
        if self._speech_candidate_at == 0.0:
            return
        candidate_age = time.monotonic() - self._speech_candidate_at
        if force or candidate_age >= self._barge_in_min_sec:
            already_started = self._speaking
            self._speaking = True
            self._floor_claimed = True
            # Cancel a response that is generating or queued as well as one
            # already audible. ExternalUserTurnStrategies will not do this.
            if self._interrupted_speech_epoch != self._speech_epoch:
                self._interrupted_speech_epoch = self._speech_epoch
                await self.broadcast_interruption()
                if not already_started:
                    await self.push_frame(UserStartedSpeakingFrame())
                await self._invoke_speculation_handler(
                    self._speculation_cancel_handler, "speech_resumed"
                )
            elif not already_started:
                await self.push_frame(UserStartedSpeakingFrame())

    def caller_owns_floor(self) -> bool:
        """Synchronous last check used immediately before assistant audio release."""
        # Tentative VAD STARTING blocks a new commitment, but must not destroy
        # an existing reply on a brief non-speech sound. Confirmed SPEAKING
        # claims the floor and sends an actual interruption.
        return self._floor_claimed or bool(self._last_transcript)

    @staticmethod
    def _merge_transcript(left: str, right: str) -> str:
        """Merge cumulative and segmented provider revisions without duplicating words."""

        left = " ".join(left.split())
        right = " ".join(right.split())
        if not left:
            return right
        if not right or right == left:
            return left
        if right.startswith(left):
            return right
        if left.startswith(right):
            return left
        left_words = left.split()
        right_words = right.split()
        left_keys = [re.sub(r"^\W+|\W+$", "", word).casefold() for word in left_words]
        right_keys = [re.sub(r"^\W+|\W+$", "", word).casefold() for word in right_words]
        for overlap in range(min(len(left_words), len(right_words)), 0, -1):
            if left_keys[-overlap:] == right_keys[:overlap]:
                return " ".join([*left_words, *right_words[overlap:]])
        return f"{left} {right}"

    @staticmethod
    def _language_signal(text: str) -> str:
        tokens = re.findall(r"[a-zà-ÿ']+", text.casefold())
        english = sum(token in _ENGLISH_LANGUAGE_MARKERS for token in tokens)
        french = sum(token in _FRENCH_LANGUAGE_MARKERS for token in tokens)
        if re.search(r"[àâçéèêëîïôùûüÿœ]", text.casefold()):
            french += 2
        if english >= 2 and french >= 2:
            return "mixed"
        if english >= 2 and english > french:
            return "en"
        if french >= 2 and french > english:
            return "fr"
        return "unknown"

    @staticmethod
    def _content_overlap(left: str, right: str) -> int:
        def content_words(value: str) -> set[str]:
            words = set(re.findall(r"[a-zà-ÿ']+", value.casefold()))
            return {
                word
                for word in words
                if len(word) >= 4
                and word not in _ENGLISH_LANGUAGE_MARKERS
                and word not in _FRENCH_LANGUAGE_MARKERS
            }

        left_words = content_words(left)
        right_words = content_words(right)
        return len(left_words & right_words)

    def _has_language_conflict(self, text: str) -> bool:
        signal = self._language_signal(text)
        expected = "fr" if self._language.lower().startswith("fr") else "en"
        return signal in {"en", "fr", "mixed"} and signal != expected

    def _track_speech_energy(self, dbfs: float) -> None:
        now = time.monotonic()
        if (
            self._bot_speaking
            and self._bot_speaking_since > 0.0
            and now - self._bot_speaking_since > 15.0
        ):
            logger.warning("Bot-speaking state timed out in AntigravityLiveSTT; clearing flag")
            self._bot_speaking = False
            self._bot_speaking_since = 0.0

        if dbfs >= self._energy_threshold_dbfs:
            if not self._speech_burst_active:
                self._speech_epoch += 1
                self._speech_burst_active = True
                self._smart_turn_decision = None
                self._smart_turn_incomplete = False
                self._smart_turn_decision_epoch = -1
            self._last_speech_at = now
        elif self._speech_burst_active and now - self._last_speech_at >= 0.15:
            self._speech_burst_active = False

    def _stage_transcription(self, text: str, *, is_final: bool) -> str:
        """Merge an accepted provider/local-decoder hypothesis; no I/O here."""
        now = time.monotonic()
        text = " ".join(text.split())
        if not text:
            return ""

        # A provider segment can outlive our acoustic turn. Remember its raw
        # watermark even when a revision is suppressed, so the old prefix does
        # not reappear in the next caller turn. isFinal settles that segment;
        # only acoustic activity starts a new caller turn.
        raw_text = text
        retired = self._retired_provider_text or self._last_committed_text
        if retired:
            old_tokens = list(re.finditer(r"\w+", retired))
            new_tokens = list(re.finditer(r"\w+", text))
            prefix_matches = bool(old_tokens) and len(new_tokens) >= len(old_tokens) and (
                [m.group().casefold() for m in new_tokens[:len(old_tokens)]] == [m.group().casefold() for m in old_tokens]
            )
            same_text = prefix_matches and len(new_tokens) == len(old_tokens)
            if self._speech_epoch <= self._last_committed_speech_epoch:
                if prefix_matches:
                    self._retired_provider_text = raw_text
                if is_final:
                    self._retired_provider_pending_final = False
                return ""
            if self._retired_provider_pending_final and prefix_matches:
                if same_text:
                    if is_final:
                        self._retired_provider_pending_final = False
                    return ""
                text = text[new_tokens[len(old_tokens)-1].end():].lstrip(" ,.;:?!")
                if not text:
                    return ""
                if is_final:
                    self._retired_provider_pending_final = False
            elif not prefix_matches:
                # The provider has begun a fresh segment. It is now legitimate
                # for a repeated answer to equal the previous caller's words.
                self._retired_provider_pending_final = False
        self._last_provider_text = raw_text

        if self._speech_candidate_at == 0.0:
            self._speech_candidate_at = now
            self._candidate_speech_epoch = self._speech_epoch

        previous = self._last_transcript
        if is_final:
            previous_signal = self._language_signal(self._final_transcript)
            new_signal = self._language_signal(text)
            is_cross_language_revision = (
                bool(self._final_transcript)
                and previous_signal in {"en", "fr"}
                and new_signal in {"en", "fr"}
                and previous_signal != new_signal
                and self._content_overlap(self._final_transcript, text) >= 2
            )
            if is_cross_language_revision:
                logger.info(
                    "Replacing cross-language STT hypothesis %s->%s in speech epoch %d",
                    previous_signal,
                    new_signal,
                    self._candidate_speech_epoch,
                )
                self._final_transcript = text
            else:
                self._final_transcript = self._merge_transcript(self._final_transcript, text)
            candidate = self._final_transcript
            self._provider_final_seen = True
        else:
            candidate = self._merge_transcript(self._final_transcript, text)

        self._provider_final_seen = is_final
        self._last_transcript = candidate
        self._floor_claimed = True
        if candidate != previous or is_final:
            self._last_transcript_update_at = now
            if candidate != previous:
                self._smart_turn_incomplete = False
        return candidate

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        return looks_semantically_incomplete(text)

    @staticmethod
    def _is_short_affirmative_or_negative(text: str) -> bool:
        normalized = " ".join(text.strip().casefold().strip(".!?,").split())
        return normalized in {
            "oui", "non", "ouais", "nan", "d'accord", "d accord", "c'est bon", "c est bon",
            "parfait", "merci", "non merci", "oui merci", "bien sur", "bien sûr",
            "yes", "no", "yeah", "yep", "nope", "sure", "okay", "ok", "alright",
            "yes please", "no thanks", "thank you", "thanks", "go ahead",
        }

    def _run_smart_turn_inference(self, pcm_bytes: bytes) -> dict[str, Any]:
        if not self._smart_turn or not pcm_bytes:
            return {"probability": None}
        try:
            audio_float32 = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            return self._smart_turn._predict_endpoint(audio_float32)
        except Exception as exc:
            logger.warning("Smart Turn prediction unavailable: %s", exc)
            return {"probability": None}

    async def _evaluate_smart_turn(
        self, pcm_bytes: bytes, epoch: int,
        snapshot: tuple[int, float, float] | None = None,
    ) -> None:
        revision, transcript_version, speech_at = snapshot or (
            self._turn_revision, self._last_transcript_update_at, self._last_speech_at
        )
        try:
            if self._smart_turn is None or self._smart_turn_executor is None:
                return
            result = await asyncio.get_running_loop().run_in_executor(
                self._smart_turn_executor, self._run_smart_turn_inference, pcm_bytes
            )
            value = result.get("probability")
            probability = float(value) if value is not None else None
            if probability is not None and (not math.isfinite(probability) or not 0 <= probability <= 1):
                probability = None
            # A prediction describes this audio snapshot only. A new phrase,
            # transcript revision, or completed turn invalidates it.
            if (epoch, revision, transcript_version, speech_at) != (
                self._speech_epoch, self._turn_revision,
                self._last_transcript_update_at, self._last_speech_at,
            ):
                return
            self._smart_turn_decision_epoch = epoch
            self._smart_turn_decision_update_at = transcript_version
            self._smart_turn_decision = (
                probability > self._smart_turn_completion_threshold
                if probability is not None else None
            )
            self._smart_turn_incomplete = self._smart_turn_decision is False
            logger.info(
                "Turn evidence decision=%s score=%s epoch=%d revision=%d",
                self._smart_turn_decision, probability, epoch, revision,
            )
        except Exception as exc:
            logger.warning("Smart Turn evaluation unavailable: %s", exc)
        finally:
            self._smart_turn_pending = False

    def _required_silence(self) -> float:
        """Speculation prepares work; it never grants a shorter speech deadline."""
        text = self._last_transcript.strip()
        if self._looks_incomplete(text) or self._smart_turn_incomplete:
            return max(self._silence_endpoint_sec, self._incomplete_endpoint_sec)
        current_verdict = (
            self._smart_turn_decision_epoch == self._speech_epoch
            and self._smart_turn_decision_update_at == self._last_transcript_update_at
        )
        if current_verdict and self._smart_turn_decision is True:
            if (
                self._is_short_affirmative_or_negative(text)
                and 0 < self._speculative_fast_endpoint_sec < self._silence_endpoint_sec
            ):
                return self._speculative_fast_endpoint_sec
            return self._silence_endpoint_sec
        # With no acoustic verdict, final ASR text is still just a segment.
        # Unknown inference and unsupported audio use bounded conservative patience.
        return max(self._silence_endpoint_sec, self._fallback_endpoint_sec)

    def _silence_elapsed(self) -> float:
        if self._audio_inflight or self._acoustic_active:
            return 0.0
        now = time.monotonic()
        elapsed = max(0.0, now - self._last_speech_at)
        if self._last_audio_at:
            # Missing media is not received silence. Never manufacture an end
            # while the phone link or the input processing task is stalled.
            if now - self._last_audio_at > 0.30:
                return 0.0
            elapsed = min(elapsed, self._received_silence_sec)
        return elapsed

    def _recognition_metadata(self) -> dict[str, Any]:
        """Acoustic evidence supplied by a decoder sharing this turn controller."""
        if self._negation_revision_uncertain:
            return {"trusted_for_task": False, "uncertainty_reason": "provider_revision_removed_negation"}
        if not self._provider_final_seen and not self._partial_is_confirmed():
            return {"trusted_for_task": False, "uncertainty_reason": "unconfirmed_partial_timeout"}
        return {}

    async def _commit_pending_transcript(
        self,
        *,
        source: str,
        require_provider_final: bool = False,
        expected_update_at: float | None = None,
        expected_speech_epoch: int | None = None,
        expected_speech_at: float | None = None,
    ) -> None:
        if require_provider_final and not self._provider_final_seen:
            return
        preparation_epoch = self._speech_epoch
        preparation_speech_at = self._last_speech_at
        preparation_update = self._last_transcript_update_at
        if not self._speaking and self._last_transcript:
            await self._ensure_user_started(force=True)
        async with self._transcript_lock:
            if (preparation_epoch, preparation_speech_at, preparation_update) != (
                self._speech_epoch, self._last_speech_at, self._last_transcript_update_at,
            ):
                return
            if self._is_closing or self._audio_inflight or self._acoustic_active:
                return
            if self._send_inflight or not self._send_queue.empty():
                return
            if expected_speech_epoch is not None and expected_speech_epoch != self._speech_epoch:
                return
            if expected_speech_at is not None and expected_speech_at != self._last_speech_at:
                return
            if self._last_audio_at and self._silence_elapsed() < self._required_silence():
                return
            if require_provider_final and not self._provider_final_seen:
                return
            if (not self._provider_final_seen and not self._partial_is_confirmed()
                    and self._silence_elapsed() < self._incomplete_endpoint_sec):
                return
            if (
                expected_update_at is not None
                and expected_update_at != self._last_transcript_update_at
            ):
                return
            text = self._last_transcript.strip()
            if not text:
                return
            provider_final_seen = self._provider_final_seen
            if not provider_final_seen and not self._partial_is_confirmed():
                # If this was isolated non-language noise during silence, drop it
                tokens = re.findall(r"[a-zà-ÿ']+", text.casefold())
                english = sum(token in _ENGLISH_LANGUAGE_MARKERS for token in tokens)
                french = sum(token in _FRENCH_LANGUAGE_MARKERS for token in tokens)
                if len(tokens) <= 3 and english == 0 and french == 0:
                    logger.info("Discarded unconfirmed acoustic noise fragment: %r", text)
                    self._reset_recognition_evidence()
                    self._last_transcript = ""
                    self._floor_claimed = False
                    self._speaking = False
                    return
            commit_timing = {
                **self._recognition_metadata(),
                "speech_epoch": self._speech_epoch,
                "provider_final": provider_final_seen,
                "partial_confirmations": self._partial_confirmations,
                "partial_confirmed": self._partial_is_confirmed(),
                "speech_end_monotonic_ns": int(self._last_speech_at * 1e9),
                "committed_monotonic_ns": time.monotonic_ns(),
                "endpoint_wait_ms": round(self._silence_elapsed() * 1000, 3),
                "completion_reason": (
                    "bounded_incomplete" if self._smart_turn_incomplete or self._looks_incomplete(text)
                    else "smart_turn" if self._smart_turn_decision is True else "uncertain_timeout"
                ),
            }
            self._retired_provider_text = self._last_provider_text or text
            self._retired_provider_pending_final = not provider_final_seen
            self._last_committed_text = text
            self._last_committed_at = time.monotonic()
            committed_epoch = self._speech_epoch
            self._last_committed_speech_epoch = committed_epoch
            self._reset_recognition_evidence()
            self._turn_revision += 1
            self._last_transcript = ""
            self._final_transcript = ""
            self._provider_final_seen = False
            self._last_transcript_update_at = 0.0
            self._speech_candidate_at = 0.0
            self._candidate_speech_epoch = self._speech_epoch
            self._last_speculation_text = ""
            self._smart_turn_incomplete = False
            self._smart_turn_decision = None
            self._smart_turn_decision_epoch = -1
            self._smart_turn_decision_update_at = -1.0
            self._recent_pcm.clear()
            self._floor_claimed = False

        # There is no early return after moving pending text into committed
        # state: even if input resumes while publishing, its prefix is retained
        # in conversation context and the resumed epoch cancels old output.
        await self.push_frame(
            TranscriptionFrame(
                text=text,
                user_id="caller",
                timestamp=None,
                result={"phone_agent": commit_timing},
                finalized=True,
            )
        )
        if self._speaking and committed_epoch == self._speech_epoch and not self._acoustic_active:
            self._speaking = False
            await self.push_frame(UserStoppedSpeakingFrame())
        logger.info(
            "Committed stable caller turn source=%s chars=%d provider_final=%s",
            source,
            len(text),
            provider_final_seen,
        )

    def _can_speculate(self) -> bool:
        return not self._negation_revision_uncertain

    async def _watchdog_tick(self) -> None:
        if self._is_closing:
            return
        if self._last_transcript:
            await self._ensure_user_started(force=False)
        silence_elapsed = self._silence_elapsed()
        if silence_elapsed <= 0:
            return
        stable_elapsed = time.monotonic() - self._last_transcript_update_at
        # Use the same ordered input as ASR. Do not insert artificial silence
        # ahead of a captured speech tail to accelerate provider finalization.
        # Include short answers and incomplete phrases in acoustic analysis.
        needs_verdict = (
            self._smart_turn_decision_epoch != self._speech_epoch
            or self._smart_turn_decision_update_at != self._last_transcript_update_at
        )
        if (
            self._smart_turn is not None
            and (self._floor_claimed or bool(self._last_transcript))
            and needs_verdict and not self._smart_turn_pending
            and silence_elapsed >= 0.20
            and stable_elapsed >= self._transcript_stability_sec
            and len(self._recent_pcm) >= int(self._target_sample_rate * 2 * 0.20)
        ):
            self._smart_turn_pending = True
            self._smart_turn_task = asyncio.create_task(
                self._evaluate_smart_turn(
                    bytes(self._recent_pcm), self._speech_epoch,
                    (self._turn_revision, self._last_transcript_update_at, self._last_speech_at),
                ),
                name="phoneagent-turn-analysis",
            )
        if not self._last_transcript:
            if self._recognition_unavailable:
                # Ask for repetition only after recognition can hear it.
                # Exhausted reconnection emits a fatal upstream error.
                return
            # A speech-like sound without recognition must not lock the floor
            # forever. End its empty turn only after received silence, allowing
            # Pipecat's own empty-turn behavior (no invented transcript).
            if self._floor_claimed and silence_elapsed >= self._incomplete_endpoint_sec:
                empty_epoch = self._speech_epoch
                self._empty_resolved_speech_epoch = max(self._empty_resolved_speech_epoch, empty_epoch)
                self._reset_recognition_evidence()
                self._floor_claimed = False
                self._speaking = False
                self._speech_candidate_at = 0.0
                if not self._recognition_unavailable:
                    self._recognition_gap = False
                await self.push_frame(UserStoppedSpeakingFrame())
                if (
                    self._turn_recovery_handler is not None
                    and empty_epoch == self._speech_epoch
                    and not self.caller_owns_floor()
                    and not self._is_closing
                ):
                    await self._turn_recovery_handler()
            return
        epoch = self._speech_epoch
        revision = self._turn_revision
        speech_at = self._last_speech_at
        update_at = self._last_transcript_update_at
        if self._last_speculation_text and not self._can_speculate():
            self._last_speculation_text = ""
            await self._invoke_speculation_handler(self._speculation_cancel_handler, "recognition_uncertain")
        if (
            self._speculative_pipeline_enabled
            and self._can_speculate()
            and self._last_transcript != self._last_speculation_text
            and not self._looks_incomplete(self._last_transcript)
            and not self._smart_turn_incomplete
            and silence_elapsed >= self._speculative_prefetch_silence_sec
            and stable_elapsed >= self._speculative_prefetch_stability_sec
        ):
            self._last_speculation_text = self._last_transcript
            await self._invoke_speculation_handler(
                self._speculation_candidate_handler, self._last_speculation_text
            )
        # Re-read both acoustic and transcript state AFTER every awaited action.
        if (epoch, revision, speech_at, update_at) != (
            self._speech_epoch, self._turn_revision,
            self._last_speech_at, self._last_transcript_update_at,
        ):
            return
        if self._smart_turn_pending and self._silence_elapsed() < self._incomplete_endpoint_sec:
            return
        if (
            self._silence_elapsed() >= self._required_silence()
            and time.monotonic() - update_at >= (
                self._transcript_stability_sec if self._provider_final_seen
                else self._partial_transcript_stability_sec
            )
        ):
            await self._commit_pending_transcript(
                source="adaptive_silence", expected_update_at=update_at,
                expected_speech_epoch=epoch, expected_speech_at=speech_at,
            )

    async def _silence_watchdog_loop(self) -> None:
        try:
            while not self._is_closing:
                await asyncio.sleep(0.02)
                await self._watchdog_tick()
        except asyncio.CancelledError:
            pass

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Detect local speech before awaiting ASR, and preserve capture order."""
        if not audio or self._is_closing:
            return
        transport_revision = self._transport_revision
        self._last_audio_at = time.monotonic()
        self._audio_inflight = True
        self._audio_buffer.extend(audio)
        self._recent_pcm.extend(audio)
        max_recent_bytes = int(self._target_sample_rate * 2 * 8.0)
        if len(self._recent_pcm) > max_recent_bytes:
            del self._recent_pcm[:len(self._recent_pcm) - max_recent_bytes]
        try:
            state = await self._vad.analyze_audio(audio)
            self._acoustic_active = state in {VADState.STARTING, VADState.SPEAKING}
            if state == VADState.SPEAKING:
                self._received_silence_sec = 0.0
                self._track_speech_energy(self._energy_threshold_dbfs)
                if not self._vad_confirmed_active:
                    self._vad_confirmed_active = True
                    await self.push_frame(VADUserStartedSpeakingFrame(
                        start_secs=float(getattr(getattr(self._vad, "params", None), "start_secs", 0.12))
                    ))
                if self._speech_candidate_at == 0.0:
                    self._speech_candidate_at = time.monotonic()
                    self._candidate_speech_epoch = self._speech_epoch
                had_speculation = bool(self._last_speculation_text)
                self._last_speculation_text = ""
                await self._ensure_user_started(force=True)
                if had_speculation:
                    await self._invoke_speculation_handler(
                        self._speculation_cancel_handler, "speech_resumed"
                    )
            elif state == VADState.STARTING:
                self._received_silence_sec = 0.0
            else:
                self._received_silence_sec += len(audio) / (2 * self._target_sample_rate)
                self._track_speech_energy(-120.0)
                if state == VADState.QUIET and self._vad_confirmed_active:
                    self._vad_confirmed_active = False
                    await self.push_frame(VADUserStoppedSpeakingFrame(
                        stop_secs=float(getattr(getattr(self._vad, "params", None), "stop_secs", 0.20))
                    ))
            if (self._recognition_unavailable or transport_revision != self._transport_revision) and self.caller_owns_floor():
                self._recognition_gap = True
            if self._recognition_unavailable or self._recognition_gap:
                # Never feed a suffix of speech whose beginning was lost.
                self._audio_buffer.clear()
                return
            while len(self._audio_buffer) >= self._chunk_bytes and self._session_id:
                chunk = bytes(self._audio_buffer[:self._chunk_bytes])
                del self._audio_buffer[:self._chunk_bytes]
                seq = self._seq
                self._seq += 1
                if self._sender_task is None:
                    await self._send_chunk(chunk, seq)
                else:
                    await self._send_queue.put((chunk, seq, self._session_id))
        finally:
            self._audio_inflight = False
        if False:
            yield None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        if isinstance(frame, BotStartedSpeakingFrame):
            first_start = not self._bot_speaking
            self._bot_speaking = True
            self._bot_speaking_since = time.monotonic()
            if self.caller_owns_floor():
                await self.broadcast_interruption()
            elif (
                first_start
                and not self._acoustic_active
                and isinstance(self._vad, StreamingSileroVADAnalyzer)
            ):
                # The assistant taking the floor is a safe acoustic boundary.
                # Clear recurrent history here so the next barge-in does not
                # depend on unrelated caller audio from several seconds ago.
                # Never reset after caller speech has claimed the floor.
                self._vad.reset_stream()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._bot_speaking_since = 0.0
        await super().process_frame(frame, direction)

        if isinstance(frame, CancelFrame | EndFrame):
            await self._close_session()

    async def _send_chunk(self, data: bytes, seq: int) -> None:
        if not self._session_id or self._is_closing:
            return
        stream = self._stream
        b64_data = base64.b64encode(data).decode()
        body = {
            "sessionId": self._session_id,
            "data": b64_data,
            "sequenceNumber": seq,
        }

        if self._http_session is not None and not self._http_session.closed:
            url = f"{self._base_url}/{SERVICE}/SendAudioChunk"
            headers = {
                "Content-Type": APP_JSON,
                "Accept": APP_JSON,
                "x-codeium-csrf-token": self._csrf_token,
                "Origin": self._base_url,
            }
            try:
                async with self._http_session.post(
                    url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=2.0)
                ) as resp:
                    await resp.read()
                return
            except Exception:
                if not self._is_closing:
                    logger.warning("SendAudioChunk seq=%d via persistent session failed; retrying fallback", seq)

        def send_unary() -> None:
            req = urllib.request.Request(
                f"{self._base_url}/{SERVICE}/SendAudioChunk",
                data=json.dumps(body).encode(),
                method="POST",
                headers={
                    "Content-Type": APP_JSON,
                    "Accept": APP_JSON,
                    "x-codeium-csrf-token": self._csrf_token,
                    "Origin": self._base_url,
                },
            )
            with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=2) as r:
                r.read()

        try:
            await asyncio.to_thread(send_unary)
        except Exception:
            if not self._is_closing:
                logger.warning("SendAudioChunk seq=%d failed", seq)
                await self._fail_transport(stream, source="audio upload")

    async def _audio_sender_loop(self) -> None:
        """Keep bridge HTTP latency out of the real-time Pipecat audio callback."""
        try:
            while not self._is_closing:
                data, seq, session_id = await self._send_queue.get()
                self._send_inflight = True
                try:
                    if session_id == self._session_id:
                        await self._send_chunk(data, seq)
                finally:
                    self._send_inflight = False
                    self._send_queue.task_done()
        except asyncio.CancelledError:
            pass

    async def stop(self, frame: EndFrame) -> None:
        await self._close_session()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        await self._close_session()
        await super().cancel(frame)

    async def cleanup(self) -> None:
        await self._close_session()
        await self._vad.cleanup()
        self._vad._executor.shutdown(wait=False)
        if self._smart_turn is not None:
            await self._smart_turn.cleanup()
            self._smart_turn._executor.shutdown(wait=False)
        await super().cleanup()

    async def _close_session(self) -> None:
        self._is_closing = True
        self._reconnect_enabled = False
        self._turn_revision += 1
        reconnect, self._reconnect_task = self._reconnect_task, None
        if reconnect is not None and reconnect is not asyncio.current_task():
            reconnect.cancel()
            await asyncio.gather(reconnect, return_exceptions=True)
        if self._smart_turn_task is not None:
            self._smart_turn_task.cancel()
            await asyncio.gather(self._smart_turn_task, return_exceptions=True)
            self._smart_turn_task = None
        self._smart_turn_pending = False
        if self._last_speculation_text:
            self._last_speculation_text = ""
            await self._invoke_speculation_handler(
                self._speculation_cancel_handler,
                "stt_session_closed",
            )
        if self._watchdog_task and self._watchdog_task is not asyncio.current_task():
            self._watchdog_task.cancel()
            await asyncio.gather(self._watchdog_task, return_exceptions=True)
            self._watchdog_task = None

        if self._sender_task:
            self._sender_task.cancel()
            await asyncio.gather(self._sender_task, return_exceptions=True)
            self._sender_task = None

        if self._reader_task:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None

        if self._session_id:
            sid = self._session_id
            seq_num = self._seq + 1
            self._session_id = None

            def end_unary() -> None:
                try:
                    # Flush lookahead buffer with 200ms silence exactly like Speechto Speech Google
                    silence = b"\x00" * int(16000 * 2 * 0.20)
                    flush_req = urllib.request.Request(
                        f"{self._base_url}/{SERVICE}/SendAudioChunk",
                        data=json.dumps({
                            "sessionId": sid,
                            "data": base64.b64encode(silence).decode("utf-8"),
                            "sequenceNumber": seq_num,
                        }).encode(),
                        method="POST",
                        headers={
                            "Content-Type": APP_JSON,
                            "Accept": APP_JSON,
                            "x-codeium-csrf-token": self._csrf_token,
                            "Origin": self._base_url,
                        },
                    )
                    with urllib.request.urlopen(flush_req, context=self._ssl_ctx, timeout=2) as r:
                        r.read()
                except Exception:
                    pass

                try:
                    req = urllib.request.Request(
                        f"{self._base_url}/{SERVICE}/EndAudioSession",
                        data=json.dumps({"sessionId": sid}).encode(),
                        method="POST",
                        headers={
                            "Content-Type": APP_JSON,
                            "Accept": APP_JSON,
                            "x-codeium-csrf-token": self._csrf_token,
                            "Origin": self._base_url,
                        },
                    )
                    with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=2) as r:
                        r.read()
                except Exception:
                    pass

            await asyncio.to_thread(end_unary)

        if self._stream:
            self._stream.close()
            self._stream = None

        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
            self._http_session = None

        if self._smart_turn_executor is not None:
            self._smart_turn_executor.shutdown(wait=False)
            self._smart_turn_executor = None

    def transcribe_multimodal(self, audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
        """Transcribe audio via Gemini multimodal chat path as an unmetered fail-safe backup."""
        if not self._base_url or not self._csrf_token:
            self._discover_bridge()

        def _rpc(method: str, body: dict[str, Any], timeout: int = 60) -> tuple[int, dict[str, Any]]:
            url = f"{self._base_url}/{SERVICE}/{method}"
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={
                    "Content-Type": APP_JSON,
                    "Accept": APP_JSON,
                    "x-codeium-csrf-token": self._csrf_token,
                    "Origin": self._base_url,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, context=self._ssl_ctx, timeout=timeout) as r:
                    raw = r.read().decode("utf-8")
                    return r.status, json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self._base_url = None
                    self._csrf_token = None
                    self._discover_bridge()
                    return _rpc(method, body, timeout)
                raise

        st, resp = _rpc("StartCascade", {
            "requestedModel": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
            "source": 1,
            "trajectoryType": "TRAJECTORY_TYPE_CASCADE",
        })
        cid = resp.get("cascadeId") if isinstance(resp, dict) else None
        if not cid:
            raise RuntimeError(f"StartCascade failed: {st} {resp}")

        prompt = "Transcribe the audio you hear word for word. Reply with ONLY the transcribed text, nothing else."
        cascade_config = {
            "plannerConfig": {
                "requestedModel": {"model": "MODEL_GOOGLE_GEMINI_2_5_FLASH"},
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
        st, resp = _rpc("SendUserCascadeMessage", body)
        if st != 200:
            raise RuntimeError(f"SendUserCascadeMessage failed: {st} {resp}")

        deadline = time.time() + 60
        traj = None
        while time.time() < deadline:
            time.sleep(1.0)
            st, traj = _rpc("GetCascadeTrajectory", {"cascadeId": cid, "disableRehydration": True})
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
