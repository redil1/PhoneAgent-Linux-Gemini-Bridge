"""Tool-disabled Pipecat adapter for a locally authenticated Gemini CLI.

This is deliberately separate from Antigravity.  It uses the supported
``gemini`` command and its own Google OAuth login; it never reads, copies, or
replays Antigravity credentials or calls Antigravity's private localhost API.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    StartFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import LLMService
from pipecat.services.settings import LLMSettings

from .telemetry import emit_stage_latency


class GeminiCliError(RuntimeError):
    """The local Gemini CLI could not produce a completion."""


def resolve_gemini_binary(configured: str | None = None) -> str:
    """Resolve the CLI executable without inspecting any credential store."""

    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise GeminiCliError(f"configured Gemini CLI does not exist: {path}")
        return str(path)
    discovered = shutil.which("gemini")
    if discovered:
        return discovered
    raise GeminiCliError("Gemini CLI was not found on PATH")


def _safe_error_summary(raw: str) -> str:
    """Return a short diagnostic while removing common credential/PII shapes."""

    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "<redacted-email>", raw)
    text = re.sub(
        r"(?i)(token|secret|authorization|credential)(\s*[:=]\s*)\S+",
        r"\1\2<redacted>",
        text,
    )
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    useful = [
        line
        for line in lines
        if not line.startswith("at ")
        and not line.startswith("file://")
        and "node:internal" not in line
    ]
    return " | ".join((useful or lines)[:6])[:2000] or "Gemini CLI exited without an error message"


class GeminiCliClient:
    """Run one tool-disabled, stateless Gemini CLI process per turn."""

    def __init__(self, binary: str | None = None) -> None:
        self.binary = resolve_gemini_binary(binary)
        self._workspace = tempfile.TemporaryDirectory(prefix="phone-agent-gemini-")
        self._process: asyncio.subprocess.Process | None = None
        self._process_lock = asyncio.Lock()
        self.version = self._package_version()
        self.output_mode = "stream_json" if self.version == "0.39.1" else "legacy_buffered"
        settings_dir = Path(self._workspace.name, ".gemini")
        settings_dir.mkdir(mode=0o700)
        Path(settings_dir, "settings.json").write_text(
            json.dumps(
                {
                    "coreTools": [],
                    "mcpServers": {},
                    "contextFileName": ".phone-agent-no-context",
                    "telemetry": {"enabled": False},
                    "usageStatisticsEnabled": False,
                    "autoConfigureMaxOldSpaceSize": False,
                }
            ),
            encoding="utf-8",
        )

    def _package_version(self) -> str | None:
        target = Path(self.binary).resolve()
        for parent in list(target.parents)[:3]:
            metadata = parent / "package.json"
            if metadata.is_file():
                try:
                    package = json.loads(metadata.read_text(encoding="utf-8"))
                    if package.get("name") == "@google/gemini-cli":
                        return str(package.get("version", ""))
                except (OSError, ValueError):
                    continue
        return None

    def _prepare_run(self) -> tuple[list[str], dict[str, str]]:
        """Apply only verified CLI profiles; retain existing system constraints."""
        if self.version not in {"0.1.9", "0.39.1"}:
            raise GeminiCliError(
                "This Gemini CLI version has not been qualified for tool-disabled voice use"
            )
        env = os.environ.copy()
        env.update({"GEMINI_CLI_NO_RELAUNCH": "true", "NO_COLOR": "1", "TERM": "dumb"})
        arguments: list[str] = []
        settings: dict[str, Any]
        if self.version == "0.1.9":
            # That version loads home extensions outside workspace settings.
            extensions = Path.home() / ".gemini" / "extensions"
            if extensions.exists() and any(extensions.iterdir()):
                raise GeminiCliError(
                    "Legacy Gemini CLI extensions cannot be isolated; use the qualified modern CLI profile"
                )
            settings = {
                "coreTools": [],
                "mcpServers": {},
                "hideWindowTitle": True,
                "contextFileName": ".phone-agent-no-context",
                "telemetry": {"enabled": False},
                "usageStatisticsEnabled": False,
                "autoConfigureMaxOldSpaceSize": False,
            }
            arguments = ["--telemetry", "false"]
        else:
            if sys.platform == "darwin":
                default_system = "/Library/Application Support/GeminiCli/settings.json"
            elif sys.platform == "win32":
                default_system = r"C:\ProgramData\gemini-cli\settings.json"
            else:
                default_system = "/etc/gemini-cli/settings.json"
            source = Path(env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH", default_system))
            settings = {}
            if source.is_file():
                try:
                    decoded: object = json.loads(source.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise GeminiCliError("Cannot preserve Gemini CLI system settings") from exc
                if not isinstance(decoded, dict):
                    raise GeminiCliError("Gemini CLI system settings must be an object")
                settings = cast(dict[str, Any], decoded)
            settings = copy.deepcopy(settings)
            restrictions: dict[str, Any] = {
                "tools": {"core": [], "discoveryCommand": "", "callCommand": ""},
                "admin": {
                    "secureModeEnabled": True,
                    "mcp": {"enabled": False},
                    "extensions": {"enabled": False},
                    "skills": {"enabled": False},
                },
                "hooksConfig": {"enabled": False},
                "context": {"fileName": ".phone-agent-no-context", "includeDirectories": []},
                "telemetry": {"enabled": False},
                "general": {"enableAutoUpdate": False, "enableAutoUpdateNotification": False},
                "model": {"maxSessionTurns": 1},
            }

            def restrict(target: dict[str, Any], values: dict[str, Any]) -> None:
                for key, value in values.items():
                    if isinstance(value, dict):
                        if not isinstance(target.get(key), dict):
                            target[key] = {}
                        restrict(target[key], cast(dict[str, Any], value))
                    else:
                        target[key] = value

            restrict(settings, restrictions)
            system = Path(self._workspace.name) / "system-settings.json"
            system.write_text(json.dumps(settings), encoding="utf-8")
            system.chmod(0o600)
            env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(system)
            env.setdefault(
                "GEMINI_CLI_SYSTEM_DEFAULTS_PATH", str(source.with_name("system-defaults.json"))
            )
            arguments = [
                "--output-format",
                "stream-json",
                "--extensions",
                "none",
                "--prompt",
                "Respond to the supplied telephone conversation.",
            ]
        workspace_settings = Path(self._workspace.name) / ".gemini" / "settings.json"
        workspace_settings.write_text(json.dumps(settings), encoding="utf-8")
        workspace_settings.chmod(0o600)
        return arguments, env

    async def validate(self) -> None:
        await asyncio.to_thread(self._prepare_run)

    async def _read_json_output(self, reader: asyncio.StreamReader) -> AsyncIterator[str]:
        complete = False
        spoken = False
        received = 0
        while line := await reader.readline():
            received += len(line)
            if received > 262144:
                raise GeminiCliError("Gemini CLI output exceeded the voice response limit")
            try:
                decoded_event: object = json.loads(line)
            except (ValueError, UnicodeDecodeError) as exc:
                raise GeminiCliError("Gemini CLI emitted non-protocol output") from exc
            if not isinstance(decoded_event, dict):
                raise GeminiCliError("Gemini CLI emitted an invalid stream event")
            event = cast(dict[str, Any], decoded_event)
            kind = event.get("type")
            if kind == "message" and event.get("role") == "assistant":
                if (
                    complete
                    or event.get("delta") is not True
                    or not isinstance(event.get("content"), str)
                ):
                    raise GeminiCliError("Gemini CLI emitted an unsupported assistant event")
                if event["content"]:
                    spoken = spoken or bool(event["content"].strip())
                    yield event["content"]
            elif kind == "result":
                if event.get("status") != "success":
                    error = event.get("error")
                    detail = (
                        cast(dict[str, Any], error).get("message", "Unsuccessful Gemini CLI result")
                        if isinstance(error, dict)
                        else "Unsuccessful Gemini CLI result"
                    )
                    raise GeminiCliError(_safe_error_summary(str(detail)))
                complete = True
            elif kind in {"tool_use", "tool_result"}:
                raise GeminiCliError("Gemini CLI attempted an unexpected native tool operation")
            elif kind == "error" and event.get("severity") != "warning":
                raise GeminiCliError(
                    _safe_error_summary(str(event.get("message", "Gemini CLI stream error")))
                )
            elif kind not in {"init", "message", "error"}:
                raise GeminiCliError("Gemini CLI emitted an unknown stream event")
        if not complete or not spoken:
            raise GeminiCliError("Gemini CLI ended without a completed assistant response")

    async def stream_completion(
        self,
        *,
        model: str,
        prompt: str,
        timeout_secs: float,
    ) -> AsyncIterator[str]:
        if not prompt.strip():
            raise GeminiCliError("Gemini CLI prompt is empty")
        # Include lock acquisition, process startup and stdin backpressure in
        # the deadline, not just the final stdout read.
        async with asyncio.timeout(timeout_secs):
            async with self._process_lock:
                arguments, env = await asyncio.to_thread(self._prepare_run)
                process = await asyncio.create_subprocess_exec(
                    self.binary,
                    "--model",
                    model,
                    *arguments,
                    cwd=self._workspace.name,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=1048576,
                )
                self._process = process
                assert (
                    process.stdin is not None
                    and process.stdout is not None
                    and process.stderr is not None
                )
                stderr_task = asyncio.create_task(self._capture_stderr(process.stderr))
                try:
                    process.stdin.write(prompt.encode("utf-8"))
                    await process.stdin.drain()
                    process.stdin.close()
                    buffered = bytearray()
                    stream_error: GeminiCliError | None = None
                    if self.output_mode == "stream_json":
                        try:
                            async for text in self._read_json_output(process.stdout):
                                yield text
                        except GeminiCliError as exc:
                            if not process.stdout.at_eof():
                                raise
                            # Authentication/argument errors can happen before
                            # CLI stream initialization. Preserve stderr cause.
                            stream_error = exc
                    else:
                        # Legacy stdout mixes model text with startup/auth
                        # diagnostics. It is not a reliable incremental channel.
                        while chunk := await process.stdout.read(4096):
                            buffered.extend(chunk)
                            if len(buffered) > 262144:
                                raise GeminiCliError(
                                    "Gemini CLI output exceeded the voice response limit"
                                )
                    return_code = await process.wait()
                    stderr = (await stderr_task).decode(errors="replace")
                    if return_code != 0:
                        raise GeminiCliError(_safe_error_summary(stderr))
                    if stream_error is not None:
                        raise stream_error
                    if self.output_mode == "legacy_buffered":
                        text = buffered.decode("utf-8")
                        if (
                            "\x1b" in text
                            or "Code Assist login required" in text
                            or "Waiting for authentication" in text
                        ):
                            raise GeminiCliError(
                                "Legacy Gemini CLI emitted startup or authentication output"
                            )
                        if not text.strip():
                            raise GeminiCliError("Gemini CLI returned no assistant text")
                        yield text
                finally:
                    # Cancellation, malformed output and early generator close
                    # must all reap the process before losing its ownership.
                    if process.returncode is None:
                        await asyncio.shield(self._terminate_process(process))
                    process.stdin.close()
                    await asyncio.gather(process.stdin.wait_closed(), return_exceptions=True)
                    if not stderr_task.done():
                        stderr_task.cancel()
                    await asyncio.gather(stderr_task, return_exceptions=True)
                    if self._process is process:
                        self._process = None

    async def close(self) -> None:
        process = self._process
        if process is not None:
            await self._terminate_process(process)
            self._process = None
        self._workspace.cleanup()

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            await process.wait()
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    async def _capture_stderr(reader: asyncio.StreamReader) -> bytes:
        """Drain stderr without unbounded memory while preserving useful context."""

        head = bytearray()
        tail = bytearray()
        while chunk := await reader.read(4096):
            remaining_head = 8192 - len(head)
            if remaining_head > 0:
                head.extend(chunk[:remaining_head])
                chunk = chunk[remaining_head:]
            if chunk:
                tail.extend(chunk)
                if len(tail) > 24_576:
                    del tail[:-24_576]
        return bytes(head + tail)


class GeminiCliLLMService(LLMService):
    """Pipecat LLM backed by the official Gemini CLI OAuth flow."""

    supports_native_tools = False
    terminal_generation_errors = True

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        system_instruction: str,
        binary: str | None = None,
        turn_timeout_secs: float = 30.0,
        client: GeminiCliClient | None = None,
        **kwargs: Any,
    ) -> None:
        settings = LLMSettings(
            model=model,
            system_instruction=system_instruction,
            temperature=None,
            max_tokens=None,
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
        self._client = client or GeminiCliClient(binary)
        self._turn_timeout_secs = turn_timeout_secs
        self._latency_sink: Any = None

    def set_latency_sink(self, sink: Any) -> None:
        self._latency_sink = sink

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self._client.validate()

    async def stop(self, frame: EndFrame) -> None:
        await self._client.close()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        await self._client.close()
        await super().cancel(frame)

    async def cleanup(self) -> None:
        await self._client.close()
        await super().cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        await self.push_frame(LLMFullResponseStartFrame())
        await self.start_processing_metrics()
        await self.start_ttfb_metrics()
        first = True
        started = time.perf_counter()
        outcome = "completed"
        chars = 0
        try:
            prompt = self._render_context(frame.context)
            async for delta in self._client.stream_completion(
                model=str(self._settings.model),
                prompt=prompt,
                timeout_secs=self._turn_timeout_secs,
            ):
                if first and delta.strip():
                    await self.stop_ttfb_metrics()
                    await emit_stage_latency(
                        self._latency_sink,
                        "gemini_cli",
                        "first_text",
                        started,
                        mode=getattr(self._client, "output_mode", "unknown"),
                    )
                    first = False
                chars += len(delta)
                await self._push_llm_text(delta)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            outcome = "error"
            await self.push_error(error_msg=f"Gemini CLI completion failed: {exc}", exception=exc)
        finally:
            await emit_stage_latency(
                self._latency_sink,
                "gemini_cli",
                "response_complete",
                started,
                outcome=outcome,
                response_chars=chars,
                mode=getattr(self._client, "output_mode", "unknown"),
            )
            await self.stop_ttfb_metrics()
            await self.stop_processing_metrics()
            await self.push_frame(LLMFullResponseEndFrame())

    def _render_context(self, context: LLMContext) -> str:
        turns: list[str] = []
        system = str(self._settings.system_instruction or "").strip()
        if system:
            turns.append(f"SYSTEM:\n{system}")
        for message in context.get_messages():
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "user")).upper()
            content = self._content_text(message.get("content"))
            if content:
                turns.append(f"{role}:\n{content}")
        turns.append(
            "INSTRUCTION:\nReply only with the assistant's next concise, natural, "
            "speakable telephone response, or the exact PhoneAgent tool-call protocol "
            "when connected tool instructions require an action. Do not invoke native "
            "CLI tools, commands, MCP servers or extensions. Do not include Markdown in speech."
        )
        return "\n\n".join(turns)

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for raw_item in cast(list[Any], content):
                if not isinstance(raw_item, dict):
                    continue
                item = cast(dict[str, Any], raw_item)
                text = item.get("text")
                if item.get("type") in {"text", "input_text"} and isinstance(text, str):
                    parts.append(text)
            return " ".join(parts)
        return ""
