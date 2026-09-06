"""Shared test setup with isolated operator tools, identity, tasks, and memory.

Defaults must not read or change an operator's installed configuration. Tests
can still provide their own explicit paths or environment overrides.
"""

from __future__ import annotations

import pytest

from phone_agent_gateway.ai_bridge import openwa_integration, tool_control, web_research
from phone_agent_gateway.ai_bridge.identity import kernel, store
from phone_agent_gateway.ai_bridge.identity import memory as identity_memory
from phone_agent_gateway.ai_bridge.memory import memory_manager, memory_writer
from phone_agent_gateway.ai_bridge.personality import persona_compiler
from phone_agent_gateway.ai_bridge.tasks import task_engine, tool_registry


@pytest.fixture(autouse=True)
def _isolated_user_tools(tmp_path_factory, monkeypatch):
    empty = tmp_path_factory.mktemp("no-user-tools")
    managed = tmp_path_factory.mktemp("no-managed-tools")
    operator = tmp_path_factory.mktemp("operator-state")
    # PersonaCompiler resolves a default identity beside the user persona,
    # while memory proposals resolve IdentityStore's independent default. Both
    # must refer to this test's state, without overriding an explicit root.
    monkeypatch.setattr(persona_compiler, "DEFAULT_USER_PERSONA_PATH", operator / "persona.yaml")
    for module in (kernel, store, memory_writer):
        monkeypatch.setattr(module, "DEFAULT_IDENTITY_ROOT", operator / "identity")
    for module in (kernel, identity_memory):
        monkeypatch.setattr(module, "DEFAULT_MEMORY_DB", operator / "identity-memory.sqlite3")
    monkeypatch.setattr(memory_manager, "DEFAULT_MEMORY_STORE", operator / "caller-memory.json")
    monkeypatch.setattr(task_engine, "USER_CONTRACTS_DIR", operator / "tasks")
    for name in (
        "PHONE_AGENT_PERSONA_PATH",
        "PHONE_AGENT_IDENTITY_ROOT",
        "PHONE_AGENT_IDENTITY_MEMORY_DB",
        "PHONE_AGENT_MEMORY_PATH",
        "PHONE_AGENT_IDENTITY_PROPOSALS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(tool_registry, "USER_TOOLS_DIR", empty)
    monkeypatch.setattr(tool_control, "DEFAULT_TOOL_CONTROL_PATH", managed / "tools.json")
    monkeypatch.setattr(tool_control, "DEFAULT_APPROVAL_DIR", managed / "approvals")
    monkeypatch.setattr(
        openwa_integration,
        "DEFAULT_OPENWA_CONFIG_PATH",
        managed / "openwa.json",
    )
    monkeypatch.setattr(
        web_research,
        "DEFAULT_WEB_RESEARCH_CONFIG_PATH",
        managed / "web-research.json",
    )
    monkeypatch.delenv("PHONE_AGENT_TOOL_CONTROL", raising=False)
    monkeypatch.delenv("PHONE_AGENT_TOOL_APPROVAL_DIR", raising=False)
    monkeypatch.delenv("PHONE_AGENT_OPENWA_CONFIG", raising=False)
    monkeypatch.delenv("PHONE_AGENT_WEB_RESEARCH_CONFIG", raising=False)
    # Constructing the Studio starts a warm voice host in production so the first
    # dial does not pay the ~20 s speech-model load. That spawns a real
    # subprocess, so the suite runs with it off.
    monkeypatch.setenv("PHONE_AGENT_WARM_VOICE_HOST", "0")
    # Deployment settings must not leak into the suite. The production container
    # exports PHONE_AGENT_ALLOW_EXTERNAL=true, which made the bind/DNS-rebinding
    # guard test pass or fail depending on where it ran rather than on the code.
    for name in (
        "PHONE_AGENT_ALLOW_EXTERNAL",
        "PHONE_AGENT_ALLOWED_HOSTS",
        "PHONE_AGENT_WEB_HOST",
    ):
        monkeypatch.delenv(name, raising=False)
    tool_registry.clear_registry()
    yield
    tool_registry.clear_registry()
