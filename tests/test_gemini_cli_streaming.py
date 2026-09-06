"""Structured CLI output, isolation, deadlines and process ownership."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from phone_agent_gateway.ai_bridge.gemini_cli import GeminiCliClient, GeminiCliError


def client(tmp_path, monkeypatch, body, version="0.39.1"):
    root = tmp_path / "cli"
    binary = root / "bundle" / "gemini"
    binary.parent.mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps({"name": "@google/gemini-cli", "version": version})
    )
    binary.write_text(f"#!{sys.executable}\nimport json,sys,time\n" + body)
    binary.chmod(0o700)
    monkeypatch.setenv("GEMINI_CLI_SYSTEM_SETTINGS_PATH", str(tmp_path / "system.json"))
    return GeminiCliClient(str(binary))


@pytest.mark.asyncio
async def test_only_assistant_deltas_are_spoken_incrementally(tmp_path, monkeypatch):
    cli = client(
        tmp_path,
        monkeypatch,
        """sys.stdin.read()
for event in [
 {'type':'init'},
 {'type':'message','role':'user','content':'private prompt'},
 {'type':'message','role':'assistant','content':'Bonjour ','delta':True},
 {'type':'message','role':'assistant','content':'à vous.','delta':True},
 {'type':'result','status':'success'}]:
 print(json.dumps(event),flush=True)
 time.sleep(0.01)
""",
    )
    try:
        chunks = [
            chunk
            async for chunk in cli.stream_completion(model="test", prompt="hello", timeout_secs=2)
        ]
        assert chunks == ["Bonjour ", "à vous."]
        assert cli._process is None
    finally:
        await cli.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        [{"type": "result", "status": "error"}],
        [{"type": "message", "role": "assistant", "content": "partial", "delta": True}],
        [{"type": "tool_use", "tool_name": "unexpected"}],
        [{"type": "message", "role": "assistant", "content": "snapshot", "delta": False}],
    ],
)
async def test_invalid_or_incomplete_streams_are_errors(tmp_path, monkeypatch, output):
    cli = client(
        tmp_path,
        monkeypatch,
        "sys.stdin.read()\nfor event in "
        + repr(output)
        + ": print(json.dumps(event),flush=True)\n",
    )
    try:
        with pytest.raises(GeminiCliError):
            async for _ in cli.stream_completion(model="test", prompt="hello", timeout_secs=2):
                pass
        assert cli._process is None
    finally:
        await cli.close()


@pytest.mark.asyncio
async def test_legacy_auth_failure_never_releases_stdout_as_speech(tmp_path, monkeypatch):
    cli = client(
        tmp_path,
        monkeypatch,
        "sys.stdin.read()\nprint('internal startup output',flush=True)\nprint('Authentication failed',file=sys.stderr)\nsys.exit(1)\n",
        version="0.1.9",
    )
    chunks = []
    try:
        with pytest.raises(GeminiCliError):
            async for chunk in cli.stream_completion(model="test", prompt="hello", timeout_secs=2):
                chunks.append(chunk)
        assert chunks == []
    finally:
        await cli.close()


@pytest.mark.asyncio
async def test_early_generator_close_reaps_the_owned_process(tmp_path, monkeypatch):
    cli = client(
        tmp_path,
        monkeypatch,
        "sys.stdin.read()\nprint(json.dumps({'type':'message','role':'assistant','content':'Hello','delta':True}),flush=True)\ntime.sleep(10)\n",
    )
    iterator = cli.stream_completion(model="test", prompt="hello", timeout_secs=3)
    try:
        assert await anext(iterator) == "Hello"
        pid = cli._process.pid
        await iterator.aclose()
        assert cli._process is None
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        await cli.close()


@pytest.mark.asyncio
async def test_stdin_backpressure_is_inside_the_turn_deadline(tmp_path, monkeypatch):
    cli = client(tmp_path, monkeypatch, "time.sleep(10)\n")
    try:
        with pytest.raises(TimeoutError):
            async for _ in cli.stream_completion(
                model="test", prompt="x" * 2_000_000, timeout_secs=0.15
            ):
                pass
        assert cli._process is None
    finally:
        await cli.close()


@pytest.mark.asyncio
async def test_tool_restrictions_preserve_system_auth_policy(tmp_path, monkeypatch):
    cli = client(tmp_path, monkeypatch, "pass\n")
    system = tmp_path / "system.json"
    original = {
        "security": {"auth": {"enforcedType": "oauth-personal"}},
        "admin": {"mcp": {"enabled": True}},
    }
    system.write_text(json.dumps(original))
    try:
        arguments, env = cli._prepare_run()
        settings = json.loads(Path(env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]).read_text())
        assert settings["security"] == original["security"]
        assert settings["tools"]["core"] == []
        assert not settings["admin"]["mcp"]["enabled"]
        assert not settings["admin"]["extensions"]["enabled"]
        assert not settings["hooksConfig"]["enabled"]
        assert arguments[:4] == ["--output-format", "stream-json", "--extensions", "none"]
        assert "--prompt" in arguments
        assert json.loads(system.read_text()) == original
    finally:
        await cli.close()
