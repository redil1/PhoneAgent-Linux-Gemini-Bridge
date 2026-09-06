"""Antigravity Gemini LLM Service for Pipecat with Automatic Ollama Fallback.

Connects directly to the local Antigravity Language Server bridge
(https://127.0.0.1:53850-53872) using your authenticated Google account
to run Gemini 2.5 Flash / Flash Lite / Flash 3.7 with zero credentials needed.
Includes automatic fallback to local Ollama if the bridge is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import ssl
import subprocess
import sys
import time
from typing import Any

import aiohttp
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    StartFrame,
)
from pipecat.metrics.metrics import LLMTokenUsage
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings

from .ollama_native import OllamaNativeClient
from .telemetry import emit_stage_latency

logger = logging.getLogger("AntigravityGeminiLLM")

SERVICE = "exa.language_server_pb.LanguageServerService"

MODEL_MAP = {
    "gemini-3.8-flash": "MODEL_PLACEHOLDER_M318",
    "gemini-3.8-flash-med": "MODEL_PLACEHOLDER_M319",
    "gemini-3.8-flash-low": "MODEL_PLACEHOLDER_M320",
    "gemini-3.7-flash": "MODEL_PLACEHOLDER_M298",
    "gemini-3.7-flash-control": "MODEL_PLACEHOLDER_M298",
    "gemini-3.7-flash-high": "MODEL_PLACEHOLDER_M298",
    "gemini-3.7-flash-medium": "MODEL_PLACEHOLDER_M299",
    "gemini-3.7-flash-low": "MODEL_PLACEHOLDER_M300",
    "gemini-3.7-flash-tiered": "MODEL_PLACEHOLDER_M301",
    "gemini-3.1-flash-lite": "MODEL_PLACEHOLDER_M50",
    "gemini-3-flash": "MODEL_GOOGLE_GEMINI_3_FLASH",
    "gemini-2.5-flash": "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    "gemini-2.5-flash-lite": "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE",
    "gemini-2.5-flash-thinking": "MODEL_GOOGLE_GEMINI_2_5_FLASH_THINKING",
    "gemini-2.5-pro": "MODEL_GOOGLE_GEMINI_2_5_PRO",
}


def _format_context_prompt(context: LLMContext, system_instruction: str = "") -> str:
    """Flatten LLMContext messages into a clean conversational prompt without duplication."""
    parts = []
    has_system = False
    sys_inst = system_instruction.strip()
    if sys_inst:
        parts.append(f"System instructions:\n{sys_inst}\n")
        has_system = True

    for msg in context.get_messages():
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "user").lower()
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("text")
            )
        content_str = str(content).strip()
        if not content_str:
            continue
        if role in {"system", "developer"}:
            if not has_system:
                parts.append(f"System instructions:\n{content_str}\n")
                has_system = True
            elif content_str != sys_inst and content_str not in sys_inst:
                parts.append(f"Additional instructions: {content_str}")
        elif role == "assistant":
            parts.append(f"Assistant: {content_str}")
        else:
            parts.append(f"User: {content_str}")

    return "\n\n".join(parts)


def _prefetch_prompt_key(prompt: str) -> str:
    """Normalize harmless STT whitespace and final punctuation revisions only."""

    return " ".join(prompt.split()).rstrip(" .!?")


class AntigravityGeminiLLMService(LLMService):
    """Zero-credential Gemini LLM Service powered by local Antigravity Language Server."""

    terminal_generation_errors = True

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        system_instruction: str = "",
        temperature: float = 0.4,
        turn_timeout_secs: float = 15.0,
        speculative_commit_wait_ms: int = 160,
        enable_ollama_fallback: bool = False,
        fallback_model: str = "qwen2.5:3b",
        fallback_base_url: str = "http://127.0.0.1:11434",
        **kwargs: Any,
    ) -> None:
        settings = LLMSettings(
            model=model,
            system_instruction=system_instruction,
            temperature=temperature,
            max_tokens=1024,
            top_p=None,
            top_k=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            filter_incomplete_user_turns=False,
            user_turn_completion_config=None,
            extra={},
        )
        super().__init__(settings=settings, **kwargs)
        self._model_name = model
        self._enum_model = MODEL_MAP.get(model, model)
        self._turn_timeout_secs = turn_timeout_secs
        self._base_url: str | None = None
        self._csrf_token: str | None = None
        self._ssl_ctx = self._create_ssl_context()
        self._session: aiohttp.ClientSession | None = None
        self._enable_ollama_fallback = enable_ollama_fallback
        self._fallback_client = (
            OllamaNativeClient(
                base_url=fallback_base_url,
                turn_timeout_secs=turn_timeout_secs,
            )
            if enable_ollama_fallback
            else None
        )
        self._fallback_model = fallback_model
        self._temperature = temperature
        self._use_fallback = False
        self._speculative_commit_wait_sec = speculative_commit_wait_ms / 1000.0
        self._prefetch_prompt_key: str | None = None
        self._prefetch_task: asyncio.Task[str] | None = None
        self._prefetch_hits = 0
        self._prefetch_misses = 0
        self._latency_sink: Any = None

    def set_latency_sink(self, sink: Any) -> None:
        self._latency_sink = sink

    def start_prefetch(self, context: LLMContext) -> asyncio.Task[str] | None:
        """Start one cancellable Gemini candidate for an interim caller turn."""

        if self._use_fallback or self._session is None:
            return None
        prompt = _format_context_prompt(
            context,
            system_instruction=str(self._settings.system_instruction or ""),
        )
        prompt_key = _prefetch_prompt_key(prompt)
        if self._prefetch_prompt_key == prompt_key and self._prefetch_task is not None:
            return self._prefetch_task
        self.cancel_prefetch("candidate_replaced")
        self._prefetch_prompt_key = prompt_key
        self._prefetch_task = asyncio.create_task(
            self._generate_gemini(prompt),
            name="phoneagent-speculative-gemini",
        )
        return self._prefetch_task

    def cancel_prefetch(self, reason: str = "cancelled") -> None:
        task = self._prefetch_task
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            logger.debug("discarded speculative Gemini candidate reason=%s", reason)
        self._prefetch_prompt_key = None
        self._prefetch_task = None

    async def _consume_prefetch(self, prompt: str) -> str | None:
        started = time.perf_counter()
        task = self._prefetch_task
        if task is None or self._prefetch_prompt_key != _prefetch_prompt_key(prompt):
            if task is not None:
                self.cancel_prefetch("final_prompt_mismatch")
            self._prefetch_misses += 1
            await emit_stage_latency(
                self._latency_sink, "antigravity_gemini", "prefetch_miss", started,
                reason="no_candidate" if task is None else "final_prompt_mismatch",
            )
            return None
        try:
            if task.done():
                text = task.result()
            else:
                try:
                    text = await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=self._speculative_commit_wait_sec,
                    )
                except TimeoutError:
                    # The final prompt is an exact match.  Keep waiting on the
                    # already-running request instead of throwing its progress
                    # away and issuing a duplicate Gemini call.
                    logger.debug("joining exact speculative Gemini request at commit")
                    text = await task
        except asyncio.CancelledError:
            raise
        except Exception:
            self.cancel_prefetch("candidate_unavailable_at_commit")
            self._prefetch_misses += 1
            await emit_stage_latency(
                self._latency_sink, "antigravity_gemini", "prefetch_miss", started,
                reason="candidate_unavailable_at_commit",
            )
            return None
        self._prefetch_prompt_key = None
        self._prefetch_task = None
        self._prefetch_hits += 1
        logger.info("speculative Gemini cache hit")
        await emit_stage_latency(
            self._latency_sink, "antigravity_gemini", "prefetch_hit", started,
            hits=self._prefetch_hits, misses=self._prefetch_misses,
        )
        return text

    def _create_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _discover_bridge(self) -> bool:
        if self._base_url and self._csrf_token:
            return True

        async def _scan() -> bool:
            candidates: list[tuple[int, str]] = []
            candidate_ports: set[int] = set()

            # 1. Environment variables
            env_port = os.getenv("ANTIGRAVITY_PORT", "").strip()
            env_token = os.getenv("ANTIGRAVITY_CSRF_TOKEN", "").strip()
            if env_port.isdigit() and env_token:
                candidates.append((int(env_port), env_token))
                candidate_ports.add(int(env_port))

            # 2. Dynamic Process Inspection (Linux & macOS)
            def _scan_processes() -> tuple[list[tuple[int, str]], set[int]]:
                found_cand: list[tuple[int, str]] = []
                found_ports: set[int] = set()

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
                                found_ports.add(int(m.group(1)))
                    except Exception:
                        pass

                try:
                    ps_out = subprocess.check_output(
                        ["ps", "-eo", "pid,command"], text=True, stderr=subprocess.DEVNULL
                    )
                except Exception as exc:
                    logger.debug("Process discovery exception: %s", exc)
                    return found_cand, found_ports

                for line in ps_out.splitlines():
                    if "language_server" not in line and "antigravity" not in line.lower() and "agy" not in line:
                        continue
                    pid_m = re.match(r"\s*(\d+)", line)
                    csrf_m = re.search(r"--csrf_token\s+([a-f0-9-]+)", line)
                    if not (pid_m and csrf_m):
                        continue
                    pid = pid_m.group(1)
                    token = csrf_m.group(1)
                    try:
                        lsof_out = subprocess.check_output(
                            ["lsof", "-Pan", "-p", pid, "-a", "-iTCP", "-sTCP:LISTEN"],
                            text=True,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception:
                        continue
                    for lline in lsof_out.splitlines():
                        if "LISTEN" in lline:
                            pm = re.search(r"127\.0\.0\.1:(\d+)", lline)
                            if pm:
                                p = int(pm.group(1))
                                found_cand.append((p, token))
                                found_ports.add(p)
                return found_cand, found_ports

            proc_cand, proc_ports = await asyncio.to_thread(_scan_processes)
            candidates.extend(proc_cand)
            candidate_ports.update(proc_ports)

            # 3. Test discovered candidates with a lightweight verification probe
            connector = aiohttp.TCPConnector(ssl=self._ssl_ctx)
            timeout = aiohttp.ClientTimeout(total=2.0)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as temp_session:
                for port, token in candidates:
                    url = f"https://127.0.0.1:{port}/{SERVICE}/GetModelResponse"
                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "x-codeium-csrf-token": token,
                        "Origin": f"https://127.0.0.1:{port}",
                    }
                    body = {
                        "prompt": "ping",
                        "model": self._enum_model,
                    }
                    try:
                        async with temp_session.post(url, json=body, headers=headers) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                if data.get("response"):
                                    self._base_url = f"https://127.0.0.1:{port}"
                                    self._csrf_token = token
                                    logger.info(
                                        "Discovered and verified Antigravity Language Server on %s (model=%s)",
                                        self._base_url,
                                        self._model_name,
                                    )
                                    return True
                    except Exception as exc:
                        logger.debug("Candidate port %d probe failed: %s", port, exc)

                # 4. Port scan for Antigravity HTML signature and csrfToken
                ports_to_check = sorted(candidate_ports) + [
                    p for p in range(53850, 53875) if p not in candidate_ports
                ]
                for port in ports_to_check:
                    try:
                        url = f"https://127.0.0.1:{port}/"
                        async with temp_session.get(url) as resp:
                            html = await resp.text()
                            m = re.search(r'csrfToken":"([^"]+)"', html)
                            if m and "antigravity" in html:
                                self._base_url = f"https://127.0.0.1:{port}"
                                self._csrf_token = m.group(1)
                                logger.info(
                                    "Discovered Antigravity Language Server via scan on %s", self._base_url
                                )
                                return True
                    except Exception:
                        continue

            return False

        if await _scan():
            return True

        if sys.platform == "darwin":
            for app_name in ("Antigravity", "Antigravity IDE"):
                try:
                    await asyncio.to_thread(subprocess.run, ["open", "-ga", app_name], stderr=subprocess.DEVNULL, check=False)
                    await asyncio.sleep(1.5)
                    if await _scan():
                        return True
                except Exception:
                    pass

        return False

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        connector = aiohttp.TCPConnector(ssl=self._ssl_ctx, limit=10, keepalive_timeout=60)
        self._session = aiohttp.ClientSession(connector=connector)
        discovered = await self._discover_bridge()
        if not discovered:
            if self._enable_ollama_fallback and self._fallback_client:
                logger.warning("Antigravity bridge not found at start; engaging Ollama fallback")
                self._use_fallback = True
                await self._fallback_client.start()
            else:
                logger.info(
                    "Antigravity bridge will be verified on first LLM query (model=%s)",
                    self._model_name,
                )
        else:
            logger.info(
                "Antigravity Gemini LLM ready model=%s (%s)", self._model_name, self._enum_model
            )

    async def stop(self, frame: EndFrame) -> None:
        self.cancel_prefetch("service_stopped")
        if self._session:
            await self._session.close()
            self._session = None
        if self._fallback_client:
            await self._fallback_client.close()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        self.cancel_prefetch("service_cancelled")
        if self._session:
            await self._session.close()
            self._session = None
        if self._fallback_client:
            await self._fallback_client.cancel_active()
            await self._fallback_client.close()
        await super().cancel(frame)

    async def cleanup(self) -> None:
        self.cancel_prefetch("service_cleanup")
        if self._session:
            await self._session.close()
            self._session = None
        if self._fallback_client:
            await self._fallback_client.close()
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        await self.push_frame(LLMFullResponseStartFrame())
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()

        try:
            prompt = _format_context_prompt(
                frame.context,
                system_instruction=str(self._settings.system_instruction or ""),
            )

            text = await self._consume_prefetch(prompt)
            if not self._use_fallback:
                if text is None:
                    try:
                        text = await self._generate_gemini(prompt)
                    except Exception as exc:
                        if self._enable_ollama_fallback and self._fallback_client:
                            logger.warning(
                                "Antigravity Gemini call failed (%s); switching to Ollama fallback", exc
                            )
                            self._use_fallback = True
                            await self._fallback_client.start()
                        else:
                            raise

            if self._use_fallback and self._fallback_client:
                full_response = []
                try:
                    messages = [
                        {"role": m.get("role", "user"), "content": m.get("content", "")}
                        for m in frame.context.messages
                    ]
                    first = True
                    async for event in self._fallback_client.stream_chat(
                        model=self._fallback_model,
                        messages=messages,
                        keep_alive="-1",
                        think=False,
                        options={"temperature": self._temperature},
                    ):
                        if event.content:
                            if first:
                                await self.stop_ttfb_metrics()
                                first = False
                            full_response.append(event.content)
                            await self._push_llm_text(event.content)
                        if event.done:
                            await self.start_llm_usage_metrics(
                                LLMTokenUsage(
                                    prompt_tokens=event.prompt_tokens,
                                    completion_tokens=event.completion_tokens,
                                    total_tokens=event.prompt_tokens + event.completion_tokens,
                                )
                            )
                    return
                except Exception as fallback_exc:
                    logger.exception("Fallback Ollama generation failed: %s", fallback_exc)
                    await self.push_error(
                        error_msg=f"LLM generation failed: {fallback_exc}",
                        exception=fallback_exc,
                    )
                    return

            if text is None:
                raise RuntimeError("Empty response generated by LLM")

            # Antigravity Gemini generated text successfully
            await self.stop_ttfb_metrics()
            # Push tokens into downstream pipeline in natural streaming words
            words = text.split(" ")
            for i, word in enumerate(words):
                chunk = word if i == 0 else " " + word
                await self._push_llm_text(chunk)

            approx_tokens = max(1, len(text.split()))
            await self.start_llm_usage_metrics(
                LLMTokenUsage(
                    prompt_tokens=len(prompt.split()),
                    completion_tokens=approx_tokens,
                    total_tokens=len(prompt.split()) + approx_tokens,
                )
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("LLM generation failed: %s", exc)
            await self.push_error(error_msg=f"Gemini completion failed: {exc}", exception=exc)
        finally:
            await self.stop_ttfb_metrics()
            await self.stop_processing_metrics()
            await self.push_frame(LLMFullResponseEndFrame())

    async def _generate_gemini(self, prompt: str) -> str:
        started = time.perf_counter()
        try:
            # This setting is a turn budget, not a fresh budget for each
            # retry/model. Previously four attempts could multiply it by four.
            async with asyncio.timeout(self._turn_timeout_secs):
                return await self._generate_gemini_within_budget(prompt)
        except TimeoutError:
            await emit_stage_latency(
                self._latency_sink, "antigravity_gemini", "turn_timeout", started,
                prompt_chars=len(prompt), budget_ms=self._turn_timeout_secs * 1000,
            )
            raise

    async def _generate_gemini_within_budget(self, prompt: str) -> str:
        if not self._session or not self._base_url or not self._csrf_token:
            if not await self._discover_bridge() or not self._session:
                raise RuntimeError("Antigravity Language Server bridge is not available")

        # Multi-model Gemini fallback sequence (Speech-to-Speech architecture)
        models_to_try = [self._enum_model]
        backup_model = (
            "MODEL_PLACEHOLDER_M298"
            if self._enum_model != "MODEL_PLACEHOLDER_M298"
            else "MODEL_GOOGLE_GEMINI_2_5_FLASH"
        )
        if backup_model not in models_to_try:
            models_to_try.append(backup_model)

        last_error = None
        for enum_m in models_to_try:
            for attempt in range(2):
                started = time.perf_counter()
                response_status: int | None = None
                url = f"{self._base_url}/{SERVICE}/GetModelResponse"
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "x-codeium-csrf-token": self._csrf_token,
                    "Origin": self._base_url,
                }
                body = {
                    "prompt": prompt,
                    "model": enum_m,
                }
                timeout = aiohttp.ClientTimeout(total=self._turn_timeout_secs)
                try:
                    async with self._session.post(url, json=body, headers=headers, timeout=timeout) as resp:
                        response_status = resp.status
                        if resp.status == 401:
                            # Token may have rotated; rediscover and retry
                            self._base_url = None
                            self._csrf_token = None
                            if await self._discover_bridge():
                                continue
                            raise RuntimeError("401 Unauthorized from Antigravity Language Server")
                        if resp.status != 200:
                            err_text = await resp.text()
                            raise RuntimeError(
                                f"GetModelResponse returned HTTP {resp.status}: {err_text[:200]}"
                            )
                        data = await resp.json()
                        response_text = data.get("response", "").strip()
                        if not response_text:
                            raise RuntimeError("Empty response from GetModelResponse")
                        await emit_stage_latency(
                            self._latency_sink, "antigravity_gemini", "response_complete", started,
                            model=enum_m, attempt=attempt + 1,
                            prompt_chars=len(prompt), response_chars=len(response_text),
                            streaming=False,
                        )
                        return response_text
                except Exception as exc:
                    await emit_stage_latency(
                        self._latency_sink, "antigravity_gemini", "attempt_failed", started,
                        model=enum_m, attempt=attempt + 1, http_status=response_status,
                        error_type=type(exc).__name__, prompt_chars=len(prompt),
                    )
                    last_error = exc
                    if attempt == 0:
                        await asyncio.sleep(0.2)
                        continue
                    break

        raise RuntimeError(f"Antigravity Gemini generation failed across models: {last_error}")
