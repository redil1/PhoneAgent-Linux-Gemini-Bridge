"""The shipped runtime is neutral; business behavior arrives through MCP packages."""

from __future__ import annotations

import re
from pathlib import Path

from phone_agent_gateway.ai_bridge import mcp_server
from phone_agent_gateway.ai_bridge.runtime_config import RuntimeConfig

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = ROOT / "ai_bridge"
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".py", ".toml", ".yaml"}
FORBIDDEN_PRODUCT_MARKERS = (
    re.compile(r"\biptv\b", re.IGNORECASE),
    re.compile(r"\boxzoon\b", re.IGNORECASE),
    re.compile(r"smartiptvstream", re.IGNORECASE),
    re.compile(r"routinerelief", re.IGNORECASE),
)


def test_shared_runtime_contains_no_removed_product_or_company_markers() -> None:
    violations: list[str] = []
    for path in sorted(RUNTIME_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(pattern.search(text) for pattern in FORBIDDEN_PRODUCT_MARKERS):
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_unconfigured_runtime_defaults_to_neutral_task(monkeypatch) -> None:
    monkeypatch.delenv("PHONE_AGENT_TASK_ID", raising=False)
    config = RuntimeConfig.from_env(require_provider_credentials=False)
    assert config.task_id == "general_conversation"


def test_mcp_exposes_complete_agent_package_lifecycle() -> None:
    names = {tool["name"] for tool in mcp_server.TOOLS}
    assert {
        "phone_agent_get_active_package",
        "phone_agent_validate_package",
        "phone_agent_stage_package",
        "phone_agent_activate_deployment",
        "phone_agent_rollback_deployment",
    } <= names
