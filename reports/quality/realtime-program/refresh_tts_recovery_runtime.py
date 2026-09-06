"""Refresh the verified idle checkout service, preserving its process environment.

Never dials or changes saved configuration. Environment values stay in memory;
only non-sensitive health/configuration fields are written to the result file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKUP = Path("/private/tmp/phoneagent-tts-recovery-before")
KEYS = (
    "stt_provider", "stt_language", "llm_provider", "tts_provider", "tts_voice_id",
    "speculative_pipeline_enabled", "conversational_reflex_enabled", "task_id",
)


def api(route):
    with urllib.request.urlopen(f"http://127.0.0.1:8090/api/{route}", timeout=2) as response:
        return json.load(response)


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def idle():
    status = api("status")
    if status.get("call_state") != "IDLE":
        raise RuntimeError("Studio is not idle; refresh deferred")
    return status


def launch(environment):
    log = Path.home() / "phone-agent-logs" / "studio-turn-fix.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "ab", buffering=0) as output:
        return subprocess.Popen(
            [str(ROOT / ".venv/bin/python"), "-m", "ai_bridge.web_server"],
            cwd=ROOT, env=environment, start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        )


def health(process, expected, *, updated):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Refreshed Studio exited before becoming healthy")
        try:
            status = api("status")
            config = api("config")
            owner = command("lsof", "-nP", "-iTCP:8090", "-sTCP:LISTEN", "-t")
            if owner != str(process.pid):
                raise RuntimeError("Port 8090 is not owned by refreshed process")
            if {key: config.get(key) for key in KEYS} != expected:
                raise RuntimeError("Provider/task configuration changed during refresh")
            if updated and config.get("turn_management", {}).get("complete_pause_ms") != 600:
                raise RuntimeError("Updated turn-controller configuration not loaded")
            return {
                "pid": process.pid, "status": status.get("status"),
                "call_state": status.get("call_state"),
                "configuration": {key: config.get(key) for key in KEYS},
                "turn_management": config.get("turn_management"),
                "smart_turn_enabled": config.get("smart_turn_enabled"),
            }
        except (OSError, ValueError):
            time.sleep(0.3)
    raise RuntimeError("Refreshed Studio did not become healthy within 30 seconds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    pid = args.pid
    cmd = command("ps", "-p", str(pid), "-o", "command=")
    if "-m ai_bridge.web_server" not in cmd:
        raise RuntimeError("Process is not the expected checkout Studio")
    cwd = command("lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn")
    if f"n{ROOT}" not in cwd.splitlines():
        raise RuntimeError("Studio working directory does not match checkout")
    if command("lsof", "-nP", "-iTCP:8090", "-sTCP:LISTEN", "-t") != str(pid):
        raise RuntimeError("Studio port owner changed")
    idle()
    config = api("config")
    expected = {key: config.get(key) for key in KEYS}
    raw = command("ps", "eww", "-p", str(pid), "-o", "command=")
    matches = list(re.finditer(r"(?<!\S)([A-Za-z_][A-Za-z_0-9]*)=", raw))
    environment = {
        match[1]: raw[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(raw)].rstrip()
        for index, match in enumerate(matches)
    }
    if environment.get("HOME") != str(Path.home()) or not environment.get("PATH"):
        raise RuntimeError("Could not safely preserve the existing runtime environment")
    edited_hashes = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in json.loads((BACKUP / "source-hashes.json").read_text())
        if name.startswith("ai_bridge/")
    }
    if not args.execute:
        print(json.dumps({"ready": True, "pid": pid, "configuration": expected}, indent=2))
        return
    idle()  # Last check immediately before stopping this exact process.
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 45
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    if alive(pid):
        raise RuntimeError("Existing Studio did not exit; no replacement was started")
    process = launch(environment)
    result = {"previous_pid": pid, "activated": False}
    try:
        result["runtime"] = health(process, expected, updated=True)
        result["activated"] = True
    except Exception:
        process.terminate()
        process.wait(timeout=15)
        for name, expected_hash in edited_hashes.items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected_hash:
                raise RuntimeError("Concurrent edit detected; automatic source rollback refused") from None
        for name in edited_hashes:
            (ROOT / name).write_bytes((BACKUP / name).read_bytes())
        restored = launch(environment)
        result["rollback"] = health(restored, expected, updated=False)
    target = Path(__file__).with_name("runtime-tts-recovery-refresh-result.json")
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
