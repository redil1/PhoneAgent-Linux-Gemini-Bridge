from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from phone_agent_gateway.ai_bridge.control_plane import (
    AgentPackage,
    ControlPlaneStore,
    RuntimeControl,
)
from phone_agent_gateway.ai_bridge.frappe_integration import FrappeConfigStore
from phone_agent_gateway.ai_bridge.knowledge_evidence import fact_hash
from phone_agent_gateway.ai_bridge.openwa_integration import OpenWAConfigStore
from phone_agent_gateway.ai_bridge.personality.persona_compiler import (
    DEFAULT_EXAMPLES_PATH,
    DEFAULT_PERSONA_PATH,
    PersonaCompiler,
)
from phone_agent_gateway.ai_bridge.production_security import AuditLedger
from phone_agent_gateway.ai_bridge.runtime_config import ProviderConfig
from phone_agent_gateway.ai_bridge.tasks.task_engine import TaskEngine
from phone_agent_gateway.ai_bridge.tool_control import ToolControlStore
from phone_agent_gateway.ai_bridge.web_research import WebResearchConfigStore
from phone_agent_gateway.ai_bridge.web_server import PhoneAgentWebServer


@pytest.mark.parametrize('failure', ['disabled', 'not_allowed', 'wrong_task'])
def test_package_cannot_promise_whatsapp_without_an_effective_binding(tmp_path, failure):
    server = _server(tmp_path)
    package = server._current_agent_package().model_dump(mode='json')
    package['task']['objective'] = 'Send the requested summary via WhatsApp after consent.'
    package['task']['allowed_tools'] = [] if failure == 'not_allowed' else ['whatsapp_send_text_current_customer']
    package['openwa']['enabled'] = failure != 'disabled'
    package['openwa'].update(api_key='test-api-key', session_id='test-session')
    package['openwa']['tools'] = [{'name': 'whatsapp_send_text_current_customer', 'enabled': True,
                                  'task_ids': ['other_task'] if failure == 'wrong_task' else [package['task']['id']]}]
    result = server._validate_agent_package(AgentPackage.model_validate(package))
    assert not result.valid
    check = next(c for c in result.checks if c['id'] == 'task.channel_capabilities')
    assert check['missing'] == ['whatsapp.send']


def test_package_requires_a_separate_email_binding_even_when_whatsapp_is_configured(tmp_path):
    server = _server(tmp_path)
    package = server._current_agent_package().model_dump(mode='json')
    package['task']['objective'] = 'Send the summary by email and WhatsApp after consent.'
    package['task']['allowed_tools'] = ['whatsapp_send_text_current_customer']
    package['openwa']['enabled'] = True
    package['openwa'].update(api_key='test-api-key', session_id='test-session')
    package['openwa']['tools'] = [{'name': 'whatsapp_send_text_current_customer', 'enabled': True,
                                  'task_ids': [package['task']['id']]}]
    result = server._validate_agent_package(AgentPackage.model_validate(package))
    assert not result.valid
    check = next(c for c in result.checks if c['id'] == 'task.channel_capabilities')
    assert check['configured'] == ['whatsapp.send']
    assert check['missing'] == ['email.send']
    package['task']['objective'] = 'Send the summary by WhatsApp after consent.'
    assert server._validate_agent_package(AgentPackage.model_validate(package)).valid


def test_declared_capability_works_for_a_custom_tool_name(tmp_path):
    server = _server(tmp_path)
    package = server._current_agent_package().model_dump(mode='json')
    package['task']['required_capabilities'] = ['email.send']
    package['task']['allowed_tools'] = ['deliver_summary']
    package['tools']['connections'] = [{
        'id': 'mail_bridge', 'label': 'Configured mail bridge', 'kind': 'http', 'enabled': True,
        'url': 'http://127.0.0.1:9099/send', 'allow_insecure_http': True,
        'tools': [{'source_name': 'deliver', 'exposed_name': 'deliver_summary',
                   'description': 'Deliver a summary through the configured mail bridge.',
                   'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
                   'enabled': True, 'read_only': False, 'capabilities': ['email.send'],
                   'task_ids': [package['task']['id']]}],
    }]
    result = server._validate_agent_package(AgentPackage.model_validate(package))
    assert result.valid
    check = next(c for c in result.checks if c['id'] == 'task.channel_capabilities')
    assert check['configured'] == ['email.send']
    package['tools']['connections'][0]['tools'][0]['read_only'] = True
    with pytest.raises(ValueError, match='writable'):
        server._validate_agent_package(AgentPackage.model_validate(package))


def test_unavailable_allowed_tool_is_a_validation_failure(tmp_path):
    server = _server(tmp_path)
    package = server._current_agent_package().model_dump(mode='json')
    package['task']['allowed_tools'] = ['missing_tool']
    assert not server._validate_agent_package(AgentPackage.model_validate(package)).valid


@pytest.mark.asyncio
async def test_turn_and_voice_tuning_roundtrips_through_package_settings_and_worker(tmp_path):
    server = _server(tmp_path)
    changes = {'antigravity_live_endpoint_ms': 750, 'antigravity_live_fallback_endpoint_ms': 1000,
               'antigravity_live_incomplete_endpoint_ms': 3200, 'antigravity_live_partial_stability_ms': 800,
               'smart_turn_completion_threshold': 0.6, 'edge_tts_rate': '+5%',
               'edge_tts_volume': '-5%', 'edge_tts_pitch': '+2Hz'}
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post('/api/config', json=changes)
        assert response.status == 200, await response.text()
        public = await (await client.get('/api/config')).json()
        assert all(public[key] == value for key, value in changes.items())
        package = server._current_agent_package()
        assert all(getattr(package.runtime, key) == value for key, value in changes.items())
        candidate = server._runtime_candidate(package.runtime)
        assert all(getattr(candidate, key) == value for key, value in changes.items())
        child = server._child_environment()
        assert child['PHONE_AGENT_ANTIGRAVITY_ENDPOINT_MS'] == '750'
        assert child['PHONE_AGENT_ANTIGRAVITY_PARTIAL_STABILITY_MS'] == '800'
        assert child['PHONE_AGENT_EDGE_TTS_RATE'] == '+5%'
        assert child['PHONE_AGENT_EDGE_TTS_VOLUME'] == '-5%'
        assert child['PHONE_AGENT_EDGE_TTS_PITCH'] == '+2Hz'
        assert child['PHONE_AGENT_CONTROL_PLANE_ROOT'] == str(server.control_plane_store.root)
        assert child['PHONE_AGENT_TASKS_DIR'] == str(server.task_engine.user_contracts_dir)
        reloaded = _server(tmp_path)
        reloaded._load_saved_settings()
        assert all(getattr(reloaded.config, key) == value for key, value in changes.items())
        bad = await client.post('/api/config', json={'antigravity_live_endpoint_ms': True})
        assert bad.status == 400
        assert server.config.antigravity_live_endpoint_ms == 750


def test_older_package_omissions_preserve_effective_turn_tuning(tmp_path):
    from dataclasses import replace
    server = _server(tmp_path)
    server.config = replace(server.config, antigravity_live_endpoint_ms=750, edge_tts_rate='+5%')
    old = RuntimeControl()
    candidate = server._runtime_candidate(old)
    assert candidate.antigravity_live_endpoint_ms == 750
    assert candidate.edge_tts_rate == '+5%'


ADDITIONAL_TUNING = {
    "antigravity_live_chunk_ms": 320, "antigravity_live_context_bias": "Example Product, Produit Exemple",
    "flux_eager_eot_threshold": 0.61, "flux_eot_threshold": 0.8, "flux_eot_timeout_ms": 1800,
    "parakeet_endpoint_ms": 700, "parakeet_incomplete_endpoint_ms": 3200,
    "speculative_prefetch_silence_ms": 200,
    "speculative_prefetch_stability_ms": 110, "speculative_fast_endpoint_ms": 700,
    "speculative_ambiguous_endpoint_ms": 1100, "speculative_incomplete_endpoint_ms": 2900,
    "speculative_commit_wait_ms": 150, "conversational_reflex_cooldown_ms": 9000,
    "ollama_keep_alive": "5m", "ollama_prewarm": False, "ollama_think": True,
    "ollama_temperature": 0.4, "ollama_top_p": 0.7, "ollama_top_k": 25, "ollama_min_p": 0.1,
    "ollama_presence_penalty": 0.2, "ollama_num_predict": 256, "ollama_num_ctx": 32768,
    "ollama_turn_timeout_secs": 45, "codex_reasoning_effort": "medium", "codex_turn_timeout_secs": 40,
    "gemini_cli_turn_timeout_secs": 45, "tts_max_buffer_delay_ms": 120,
    "edge_tts_phrase_min_chars": 20, "edge_tts_phrase_max_chars": 90,
    "edge_tts_connect_timeout_secs": 8, "edge_tts_receive_timeout_secs": 30,
    "supertonic_steps": 7, "supertonic_speed": 1.15, "supertonic_intra_op_threads": 2,
    "supertonic_inter_op_threads": 1, "supertonic_fallback_to_edge": False,
    "vibevoice_ddpm_steps": 12, "vibevoice_cfg_scale": 1.5, "whatsapp_max_duration_secs": 1200,
}


@pytest.mark.asyncio
async def test_all_additional_tuning_reaches_settings_package_and_parsed_worker(tmp_path, monkeypatch):
    from phone_agent_gateway.ai_bridge.control_plane import reported_conversation_tuning
    from phone_agent_gateway.ai_bridge.phone_voice_agent import PhoneVoiceAgent
    from phone_agent_gateway.ai_bridge.runtime_config import RuntimeConfig

    server = _server(tmp_path)
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/config", json=ADDITIONAL_TUNING)
        assert response.status == 200, await response.text()
        public = await (await client.get("/api/config")).json()
        assert all(public[key] == value for key, value in ADDITIONAL_TUNING.items())
        exported = server._current_agent_package()
        assert all(getattr(exported.runtime, key) == value for key, value in ADDITIONAL_TUNING.items())
        reloaded = _server(tmp_path)
        reloaded._load_saved_settings()
        assert all(getattr(reloaded.config, key) == value for key, value in ADDITIONAL_TUNING.items())
        for key, value in server._child_environment().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("PHONE_AGENT_LINK_KEY_BASE64", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        worker_config = RuntimeConfig.from_env(require_provider_credentials=False)
        assert all(getattr(worker_config.providers, key) == value for key, value in ADDITIONAL_TUNING.items())
        agent = PhoneVoiceAgent(worker_config)
        events = []
        monkeypatch.setattr(agent, "_emit_event", events.append)
        agent._emit_voice_host_ready()
        assert events[0]["config"] == server._expected_voice_host_config()
        assert "Example Product" not in json.dumps(events)
        assert reported_conversation_tuning(worker_config.providers)["antigravity_live_context_bias_sha256"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"ollama_top_k": True}, {"ollama_think": "false"}, {"supertonic_speed": float("inf")},
    {"codex_reasoning_effort": "unbounded"}, {"edge_tts_connect_timeout_secs": 0},
    {"edge_tts_phrase_min_chars": 100, "edge_tts_phrase_max_chars": 20},
    {"parakeet_endpoint_ms": 2000, "parakeet_incomplete_endpoint_ms": 1000},
    {"parakeet_energy_threshold_dbfs": -38.0},
])
async def test_invalid_additional_tuning_cannot_mutate_settings(tmp_path, changes):
    server = _server(tmp_path)
    before = server.config
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/config", json=changes)
        assert response.status == 400, await response.text()
        assert server.config == before
        assert not server.settings_path.exists()


def test_nonstandard_tuning_environment_names_invalidate_resident_signature(tmp_path):
    server = _server(tmp_path)
    env = server._child_environment()
    before = server._voice_host_environment_signature(env)
    for name in ("OLLAMA_KEEP_ALIVE", "CODEX_REASONING_EFFORT", "GEMINI_CLI_TURN_TIMEOUT_SECS"):
        assert server._voice_host_environment_signature({**env, name: "changed"}) != before


@pytest.mark.asyncio
async def test_additional_tuning_activates_and_rolls_back_every_recorded_value(tmp_path):
    server = _server(tmp_path)
    initial = server.config
    baseline = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    await server._activate_control_deployment(baseline.deployment_id)
    payload = server._current_agent_package().model_dump(mode="json")
    payload["runtime"].update(ADDITIONAL_TUNING)
    candidate = _stage_behavior_package(server, payload)
    assert candidate.snapshot_version == 7
    await server._activate_control_deployment(candidate.deployment_id)
    assert all(getattr(server.config, key) == value for key, value in ADDITIONAL_TUNING.items())
    headers = {"Authorization": f"Bearer {server._control_token}"}
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/control/rollback", headers=headers,
                                     json={"deployment_id": baseline.deployment_id, "reason": "Restore original tuning", "created_by": "test-operator"})
        assert response.status == 200, await response.text()
    assert all(getattr(server.config, key) == getattr(initial, key) for key in ADDITIONAL_TUNING)


def test_worker_and_studio_share_the_same_memory_scope(tmp_path, monkeypatch):
    from phone_agent_gateway.ai_bridge.agent_policy import AgentPolicyRuntime
    from phone_agent_gateway.ai_bridge.memory.memory_manager import LayeredMemoryManager
    server = _server(tmp_path)
    server.task_engine.save_contract({'id': 'scoped_task', 'title': 'Scoped Task',
                                      'objective': 'Handle one product context.', 'knowledge': {'product': 'Example'}})
    server.task_id = 'scoped_task'
    for key, value in server._child_environment().items():
        if key.startswith('PHONE_AGENT_'):
            monkeypatch.setenv(key, value)
    # The process-local memory default was bound at import time in this test;
    # an actual child imports it after receiving the environment.
    runtime = AgentPolicyRuntime(caller_id='unknown:scope-worker', task_id='scoped_task', language='en-US',
                                 memory_enabled=False, memory_manager=LayeredMemoryManager(server.memory_manager.storage_path))
    assert runtime.memory_scope == server._active_caller_memory().namespace
    assert runtime.memory_manager.storage_path == server._active_caller_memory().storage_path


@pytest.mark.asyncio
async def test_session_memory_policy_reaches_worker_and_rolls_back(tmp_path, monkeypatch):
    from phone_agent_gateway.ai_bridge.runtime_config import RuntimeConfig

    server = _server(tmp_path)
    headers = {"Authorization": f"Bearer {server._control_token}"}
    baseline = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    await server._activate_control_deployment(baseline.deployment_id)
    assert baseline.package.runtime.memory_enabled is True
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/config", json={"memory_enabled": False})
        assert response.status == 200, await response.text()
        assert not server.memory_enabled
        assert json.loads(server.settings_path.read_text())["memory_enabled"] is False
        reloaded = _server(tmp_path)
        reloaded._load_saved_settings()
        assert reloaded.memory_enabled is False
        assert server._current_agent_package().runtime.memory_enabled is False
        for key, value in server._child_environment().items():
            monkeypatch.setenv(key, value)
        worker = RuntimeConfig.from_env(require_provider_credentials=False)
        assert worker.memory_enabled is False
        assert server._expected_voice_host_config()["memory_enabled"] is False
        response = await client.post("/api/control/rollback", headers=headers,
                                     json={"deployment_id": baseline.deployment_id, "reason": "Restore session memory policy", "created_by": "test-operator"})
        assert response.status == 200, await response.text()
        assert server.memory_enabled is True
        for invalid in ("false", 0, None):
            response = await client.post("/api/config", json={"memory_enabled": invalid})
            assert response.status == 400
            assert server.memory_enabled is True


@pytest.mark.asyncio
async def test_failed_package_activation_restores_memory_policy(tmp_path, monkeypatch):
    server = _server(tmp_path)
    payload = server._current_agent_package().model_dump(mode="json")
    payload["runtime"]["memory_enabled"] = False
    staged = _stage_behavior_package(server, payload)

    def fail_commit(*args):
        assert server.memory_enabled is False
        raise RuntimeError("Injected memory-policy activation failure")

    monkeypatch.setattr(server.control_plane_store, "mark_active", fail_commit)
    with pytest.raises(RuntimeError, match="Injected memory-policy"):
        await server._activate_control_deployment(staged.deployment_id)
    assert server.memory_enabled is True
    assert not server.settings_path.exists()


@pytest.mark.asyncio
async def test_retired_recording_flag_cannot_override_per_call_consent(tmp_path, monkeypatch):
    from phone_agent_gateway.ai_bridge.call_recording import RecordingConfig
    from phone_agent_gateway.ai_bridge.runtime_config import RuntimeConfig
    from phone_agent_gateway.ai_bridge.runtime_schema import DeploymentProfileSchema

    server = _server(tmp_path)
    monkeypatch.setenv("PHONE_AGENT_RECORD_CALLS", "true")
    monkeypatch.setenv("PHONE_AGENT_RECORDING_ENABLED", "true")
    monkeypatch.setenv("PHONE_AGENT_RECORDING_CONSENT", "true")
    for consent in (False, True):
        env = server._child_environment(recording_consent=consent)
        assert "PHONE_AGENT_RECORD_CALLS" not in env
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        recording = RecordingConfig.from_env()
        assert recording.enabled is consent
        assert recording.consent_granted is consent
    assert "record_calls" not in RuntimeConfig.__dataclass_fields__
    assert "record_calls" not in DeploymentProfileSchema.model_json_schema()["properties"]
    assert DeploymentProfileSchema.model_validate({"record_calls": False}).memory_enabled is True
    with pytest.raises(ValueError, match="authorized per call"):
        DeploymentProfileSchema.model_validate({"record_calls": True})
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/config", json={"record_calls": True})
        assert response.status == 400


@pytest.mark.asyncio
async def test_host_requirements_detect_pairing_rotation_without_environment_changes(tmp_path, monkeypatch):
    key_path = tmp_path / "pairing.key"
    key_path.write_bytes(b"a" * 32)
    monkeypatch.delenv("PHONE_AGENT_LINK_KEY_BASE64", raising=False)
    monkeypatch.setenv("PHONE_AGENT_LINK_KEY_FILE", str(key_path))
    server = _server(tmp_path)
    staged = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    assert staged.package.host_requirements is not None
    key_path.write_bytes(b"b" * 32)
    assert server._configuration_state_hash() != staged.base_state_hash
    with pytest.raises(RuntimeError, match="changed after staging"):
        await server._activate_control_deployment(staged.deployment_id)
    validation = server._validate_agent_package(staged.package)
    check = next(item for item in validation.checks if item["id"] == "runtime.host_requirements")
    assert check["mismatched_fields"] == ["link_key_sha256"]


def test_host_requirements_are_constraints_not_transport_setters(tmp_path):
    server = _server(tmp_path)
    package = server._current_agent_package().model_dump(mode="json")
    original = server._expected_host_session()
    package["host_requirements"]["input_queue_frames"] = original["input_queue_frames"] + 1
    validation = server._validate_agent_package(server._resolve_agent_package(AgentPackage.model_validate(package)))
    assert not validation.valid
    check = next(item for item in validation.checks if item["id"] == "runtime.host_requirements")
    assert check["mismatched_fields"] == ["input_queue_frames"]
    assert server._expected_host_session() == original


def test_explicit_session_environment_does_not_mutate_or_read_global_values(monkeypatch):
    import os

    from phone_agent_gateway.ai_bridge.runtime_config import RuntimeConfig

    monkeypatch.setenv("PHONE_AGENT_CONTROL_PORT", "19001")
    before = dict(os.environ)
    environment = {"PHONE_AGENT_CONTROL_PORT": "19002", "PHONE_AGENT_MEMORY_ENABLED": "false",
                   "PHONE_AGENT_LINK_KEY_BASE64": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}
    parsed = RuntimeConfig.from_session_environment(environment, providers=ProviderConfig())
    assert parsed.control_port == 19002 and parsed.memory_enabled is False
    assert dict(os.environ) == before


@pytest.mark.asyncio
async def test_host_settings_reach_worker_and_survive_activation_rollback(tmp_path, monkeypatch):
    from phone_agent_gateway.ai_bridge.runtime_config import RuntimeConfig

    server = _server(tmp_path)
    baseline = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    await server._activate_control_deployment(baseline.deployment_id)
    changes = {
        "codex_binary": "/fixture/bin/codex", "gemini_cli_binary": "/fixture/bin/gemini",
        "edge_tts_ffmpeg_binary": "/fixture/bin/ffmpeg", "smart_turn_model_path": "/fixture/models/turn.onnx",
        "ollama_base_url": "http://127.0.0.1:19001", "openrouter_base_url": "http://127.0.0.1:19002/v1",
        "vllm_base_url": "http://127.0.0.1:19003/v1", "lmstudio_base_url": "http://127.0.0.1:19004/v1",
    }
    # Fixture paths/ports are never executed or contacted. This exercises the
    # real configuration pipeline, not service availability qualification.
    payload = server._current_agent_package().model_dump(mode="json")
    payload["runtime"].update(changes)
    candidate = _stage_behavior_package(server, payload)
    await server._activate_control_deployment(candidate.deployment_id)
    assert all(getattr(server.config, key) == value for key, value in changes.items())
    for key, value in server._child_environment().items():
        monkeypatch.setenv(key, value)
    worker = RuntimeConfig.from_env(require_provider_credentials=False)
    assert all(getattr(worker.providers, key) == value for key, value in changes.items())
    env = server._child_environment()
    signature = server._voice_host_environment_signature(env)
    for key in ("OLLAMA_BASE_URL", "OPENROUTER_BASE_URL", "CODEX_APP_SERVER_BINARY", "GEMINI_CLI_BINARY"):
        assert server._voice_host_environment_signature({**env, key: "changed"}) != signature
    restored = _stage_behavior_package(server, baseline.package.model_dump(mode="json"))
    await server._activate_control_deployment(restored.deployment_id)
    assert all(getattr(server.config, key) == getattr(baseline.package.runtime, key) for key in changes)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"ollama_base_url": "https://user:fixture-private-token@example.invalid/v1"},
    {"vllm_base_url": "https://example.invalid/v1?key=fixture-private-token"},
    {"lmstudio_base_url": "https://example.invalid/#fixture-private-token"},
    {"openrouter_base_url": "file:///tmp/model"}, {"ollama_base_url": "http://localhost:99999"},
    {"ollama_base_url": "http://localhost:fixture-private-token"},
    {"codex_binary": "/tmp/bad\npath"}, {"edge_tts_ffmpeg_binary": ""},
])
async def test_invalid_host_settings_reject_without_echoing_embedded_secrets(tmp_path, changes):
    server = _server(tmp_path)
    before = server.config
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/config", json=changes)
        body = await response.text()
        assert response.status == 400, body
        assert "fixture-private-token" not in body
    assert server.config == before


def test_package_material_facts_require_matching_review_evidence(tmp_path):
    server = _server(tmp_path)
    package = server._current_agent_package().model_dump(mode='json')
    package['task']['knowledge'] = {'price': 'The price is 3500 USD.'}
    report = server._validate_agent_package(AgentPackage.model_validate(package))
    assert not report.valid
    check = next(c for c in report.checks if c['id'] == 'task.material_fact_evidence')
    assert check['findings'][0]['reason'] == 'missing_evidence'
    package['task']['knowledge_evidence'] = {'price': {
        'value_hash': fact_hash(package['task']['knowledge']['price']), 'source_kind': 'operator',
        'source_ref': 'fixture:approved-price', 'reviewed_by': 'test-pricing-owner',
        'reviewed_at': '2026-01-01T00:00:00+00:00',
    }}
    assert server._validate_agent_package(AgentPackage.model_validate(package)).valid


@pytest.mark.asyncio
async def test_automatic_business_sync_requires_opt_in_and_bound_event_identity(tmp_path, monkeypatch):
    from phone_agent_gateway.ai_bridge.frappe_integration import FrappeClient, FrappeConfig

    server = _server(tmp_path)
    calls = []

    async def record(self, method, arguments=None):
        calls.append((method, arguments))
        return {"verified": True}

    monkeypatch.setattr(FrappeClient, "call", record)
    config = FrappeConfig(enabled=True, api_key="fixture", api_secret="fixture", record_call_outcomes_enabled=False)
    monkeypatch.setattr(server.frappe_config_store, "load", lambda: config)
    event = {"caller_id": "+15555550123", "call_id": "old-call", "task_id": "fixture-task",
             "outcome": "qualified", "agent_duration_seconds": 12.5, "direction": "inbound", "channel": "gsm"}
    await server._sync_frappe_call_outcome(event)
    assert calls == []
    config.record_call_outcomes_enabled = True
    server.current_phone_number = "+15555550999"
    await server._sync_frappe_call_outcome({"outcome": "qualified"})
    assert calls == []
    await server._sync_frappe_call_outcome(event)
    assert calls[0][1]["phone"] == "15555550123"
    assert calls[0][1]["call_id"] == "old-call"
    assert calls[0][1]["duration_seconds"] == 12.5


@pytest.mark.asyncio
async def test_credential_refs_persist_activate_rollback_and_reach_worker_without_secret_output(tmp_path, monkeypatch):
    from phone_agent_gateway.ai_bridge.phone_voice_agent import PhoneVoiceAgent
    from phone_agent_gateway.ai_bridge.provider_credentials import default_credential_refs
    from phone_agent_gateway.ai_bridge.runtime_config import RuntimeConfig

    monkeypatch.setenv("PHONE_AGENT_LINK_KEY_BASE64", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    server = _server(tmp_path)
    refs = default_credential_refs()
    refs["vllm_api_key"] = ["env:PHONE_AGENT_SECRET_TEST_A"]
    monkeypatch.setenv("PHONE_AGENT_SECRET_TEST_A", "synthetic-private-A")
    monkeypatch.setenv("PHONE_AGENT_SECRET_TEST_B", "synthetic-private-B")
    headers = {"Authorization": f"Bearer {server._control_token}"}
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/config", json={"credential_refs": refs})
        assert response.status == 200, await response.text()
        assert server.config.vllm_api_key == "synthetic-private-A"
        assert "synthetic-private" not in server.settings_path.read_text()
        baseline = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
        await server._activate_control_deployment(baseline.deployment_id)
        payload = server._current_agent_package().model_dump(mode="json")
        assert "synthetic-private" not in json.dumps(payload)
        payload["runtime"]["credential_refs"]["vllm_api_key"] = ["env:PHONE_AGENT_SECRET_TEST_B"]
        staged = _stage_behavior_package(server, payload)
        assert "synthetic-private" not in server.control_plane_store._path(staged.deployment_id).read_text()
        await server._activate_control_deployment(staged.deployment_id)
        assert server.config.vllm_api_key == "synthetic-private-B"
        for key, value in server._child_environment().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("PHONE_AGENT_LINK_KEY_BASE64", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
        worker = RuntimeConfig.from_env(require_provider_credentials=False)
        assert worker.providers.vllm_api_key == "synthetic-private-B"
        agent = PhoneVoiceAgent(worker)
        events = []
        monkeypatch.setattr(agent, "_emit_event", events.append)
        agent._emit_voice_host_ready()
        assert events[0]["config"] == server._expected_voice_host_config()
        assert "synthetic-private" not in json.dumps(events)
        response = await client.post("/api/control/rollback", headers=headers,
                                     json={"deployment_id": baseline.deployment_id, "reason": "Restore credential binding", "created_by": "test-operator"})
        assert response.status == 200, await response.text()
        assert server.config.vllm_api_key == "synthetic-private-A"
        assert server.config.credential_refs == refs


def test_package_checks_credential_availability_without_executing_provider(tmp_path, monkeypatch):
    server = _server(tmp_path)
    payload = server._current_agent_package().model_dump(mode="json")
    # Configuration-only check: no provider services are instantiated or called.
    payload["runtime"].update(llm_provider="openrouter", llm_model="fixture-model")
    payload["runtime"]["credential_refs"]["openrouter_api_key"] = ["env:PHONE_AGENT_SECRET_MISSING_TEST"]
    monkeypatch.delenv("PHONE_AGENT_SECRET_MISSING_TEST", raising=False)
    result = server._validate_agent_package(AgentPackage.model_validate(payload))
    check = next(c for c in result.checks if c["id"] == "runtime.credential_references")
    assert not result.valid and check["missing"] == ["openrouter_api_key"]
    monkeypatch.setenv("PHONE_AGENT_SECRET_MISSING_TEST", "synthetic-available")
    result = server._validate_agent_package(AgentPackage.model_validate(payload))
    assert result.valid
    assert "synthetic-available" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_raw_credentials_are_rejected_without_reflection_or_mutation(tmp_path):
    server = _server(tmp_path)
    refs = server.config.credential_refs.copy()
    refs["vllm_api_key"] = ["synthetic-private-raw"]
    before = server.config
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/config", json={"credential_refs": refs})
        assert response.status == 400
        assert "synthetic-private-raw" not in await response.text()
    assert server.config == before


@pytest.mark.asyncio
@pytest.mark.parametrize(('integration', 'store'), [
    ('openwa', 'openwa_config_store'), ('web-research', 'web_research_config_store'), ('frappe', 'frappe_config_store')])
async def test_legacy_mcp_and_studio_integration_bodies_use_the_same_schema(tmp_path, integration, store):
    server = _server(tmp_path)
    config = getattr(server, store).public_state()
    async with TestClient(TestServer(server.app)) as client:
        raw = await client.post('/api/' + integration, json=config)
        assert raw.status == 200, await raw.text()
        wrapped = await client.post('/api/' + integration, json={'config': config})
        assert wrapped.status == 200, await wrapped.text()
        bad = await client.post('/api/' + integration, json={**config, 'unknown_field': True})
        assert bad.status == 400
        empty = await client.post('/api/' + integration, json={})
        assert empty.status == 400


def _server(tmp_path: Path) -> PhoneAgentWebServer:
    persona_path = tmp_path / "persona.yaml"
    persona_path.write_bytes(DEFAULT_PERSONA_PATH.read_bytes())
    compiler = PersonaCompiler(
        persona_path=persona_path,
        examples_path=DEFAULT_EXAMPLES_PATH,
    )
    return PhoneAgentWebServer(
        config=ProviderConfig(
            pipeline_mode="cascade",
            call_channel="gsm",
            stt_provider="parakeet_local",
            llm_provider="ollama",
            tts_provider="edge_tts",
            tts_voice_id="en-US-AndrewMultilingualNeural",
            stt_language="en-US",
        ),
        persona_compiler=compiler,
        task_engine=TaskEngine(user_contracts_dir=tmp_path / "tasks"),
        settings_path=tmp_path / "studio.json",
        audit_ledger=AuditLedger(tmp_path / "audit.jsonl"),
        tool_control_store=ToolControlStore(tmp_path / "tools.json"),
        openwa_config_store=OpenWAConfigStore(tmp_path / "openwa.json"),
        web_research_config_store=WebResearchConfigStore(tmp_path / "research.json"),
        frappe_config_store=FrappeConfigStore(tmp_path / "frappe.json"),
        control_plane_store=ControlPlaneStore(tmp_path / "control-plane"),
    )


@pytest.mark.asyncio
async def test_complete_agent_package_stages_and_activates_atomically(tmp_path: Path) -> None:
    server = _server(tmp_path)
    headers = {"Authorization": f"Bearer {server._control_token}"}
    async with TestClient(TestServer(server.app)) as client:
        assert (await client.get("/api/control/schema")).status == 401
        schema_response = await client.get("/api/control/schema", headers=headers)
        schema = await schema_response.json()
        assert schema_response.status == 200
        assert schema["schema"]["title"] == "AgentPackage"
        assert 'value_hash' in schema['task_extensions']['knowledge_evidence']['additionalProperties']['required']
        assert "gsm_media" in schema["immutable_boundaries"]

        current_response = await client.get("/api/control/package", headers=headers)
        current = await current_response.json()
        package = current["package"]
        package["package_id"] = "support_specialist"
        package["display_name"] = "Support specialist"
        package["objective"] = "Resolve customer support calls accurately and naturally."
        package["runtime"]["system_prompt"] = "Prioritize the caller's support issue."

        validation_response = await client.post(
            "/api/control/validate", headers=headers, json={"package": package}
        )
        validation = await validation_response.json()
        assert validation_response.status == 200
        assert validation["validation"]["valid"] is True

        staged_response = await client.post(
            "/api/control/stage",
            headers=headers,
            json={
                "package": package,
                "reason": "Production support package",
                "created_by": "codex-control-agent",
            },
        )
        staged = await staged_response.json()
        assert staged_response.status == 201
        deployment_id = staged["deployment"]["deployment_id"]
        assert staged["deployment"]["state"] == "staged"

        activated_response = await client.post(
            "/api/control/activate",
            headers=headers,
            json={"deployment_id": deployment_id},
        )
        activated = await activated_response.json()
        assert activated_response.status == 200, activated
        assert activated["deployment"]["state"] == "active"
        assert server.system_prompt == "Prioritize the caller's support issue."
        assert server.control_plane_store.active().deployment_id == deployment_id
        assert (tmp_path / "studio.json").is_file()
        assert (tmp_path / "tasks" / f"{server.task_id}.yaml").is_file()

        deployments = await client.get("/api/control/deployments", headers=headers)
        deployment_data = await deployments.json()
        assert deployment_data["deployments"][0]["deployment_id"] == deployment_id


def _stage_behavior_package(server, payload):
    package = server._resolve_agent_package(AgentPackage.model_validate(payload))
    validation = server._validate_agent_package(package)
    assert validation.valid
    return server.control_plane_store.stage(
        package, validation, base_state_hash=server._configuration_state_hash(),
        reason="Conversation behavior regression", actor="test-operator",
    )


@pytest.mark.asyncio
async def test_http_stage_freezes_defaults_for_activation_restart_and_rollback(tmp_path, monkeypatch):
    from dataclasses import replace

    import phone_agent_gateway.ai_bridge.personality.persona_compiler as persona_module
    from phone_agent_gateway.ai_bridge.control_plane import ConversationTuning, package_hash

    server = _server(tmp_path)
    server.config = replace(server.config, antigravity_live_endpoint_ms=750, edge_tts_pitch="+3Hz")
    payload = server._current_agent_package().model_dump(mode="json")
    payload.pop("behavior")
    for name in ConversationTuning.model_fields:
        payload["runtime"].pop(name)
    headers = {"Authorization": f"Bearer {server._control_token}"}
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/control/validate", headers=headers, json={"package": payload})
        validation = await response.json()
        assert response.status == 200
        assert validation["resolved_runtime"]["antigravity_live_endpoint_ms"] == 750
        assert validation["resolved_behavior"]["defaults_resolved"] is True
        response = await client.post("/api/control/stage", headers=headers,
                                     json={"package": payload, "reason": "Freeze defaults", "created_by": "test-operator"})
        assert response.status == 201, await response.text()
        staged_data = (await response.json())["deployment"]
        staged = server.control_plane_store.load(staged_data["deployment_id"])
        assert staged.snapshot_version == 7
        assert staged.package_hash == validation["validation"]["package_hash"]
        assert staged.package_hash == package_hash(staged.package)

        changed_defaults = tmp_path / "changed-defaults.yaml"
        changed_defaults.write_text("human_conversation:\n  presence: [UnexpectedUpgradeWording]\n  emotion: [UnexpectedUpgradeEmotion]\n")
        monkeypatch.setattr(persona_module, "DEFAULT_HUMAN_CONVERSATION_PATH", changed_defaults)
        await server._activate_control_deployment(staged.deployment_id)
        assert server.config.antigravity_live_endpoint_ms == 750
        assert server.persona_compiler.conversation_behavior() == staged.package.behavior.model_dump(mode="json")
        fresh = PersonaCompiler(persona_path=server.persona_compiler.persist_path)
        assert "UnexpectedUpgrade" not in fresh.compile()

        server.config = replace(server.config, antigravity_live_endpoint_ms=1100, edge_tts_pitch="+9Hz")
        server.persona_compiler.update_persona({"human_conversation": {"presence": ["ChangedByOperator"]}})
        response = await client.post("/api/control/rollback", headers=headers,
                                     json={"deployment_id": staged.deployment_id, "reason": "Restore resolved snapshot", "created_by": "test-operator"})
        assert response.status == 200, await response.text()
        assert server.config.antigravity_live_endpoint_ms == 750
        assert server.config.edge_tts_pitch == "+3Hz"
        assert "ChangedByOperator" not in server.persona_compiler.compile()
        assert server.persona_compiler.conversation_behavior() == staged.package.behavior.model_dump(mode="json")


def test_storage_refuses_unresolved_or_differently_validated_packages(tmp_path):
    server = _server(tmp_path)
    payload = server._current_agent_package().model_dump(mode="json")
    payload.pop("behavior")
    unresolved = AgentPackage.model_validate(payload)
    validation = server._validate_agent_package(unresolved)
    with pytest.raises(RuntimeError, match="defaults must be resolved"):
        server.control_plane_store.stage(unresolved, validation, base_state_hash="sha256:" + "a" * 64,
                                         reason="Unresolved regression", actor="test-operator")
    resolved = server._resolve_agent_package(unresolved)
    with pytest.raises(RuntimeError, match="exact resolved package"):
        server.control_plane_store.stage(resolved, validation, base_state_hash="sha256:" + "a" * 64,
                                         reason="Wrong validation regression", actor="test-operator")


@pytest.mark.asyncio
async def test_legacy_record_is_readable_but_cannot_claim_exact_rollback(tmp_path):
    server = _server(tmp_path)
    staged = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    path = server.control_plane_store._path(staged.deployment_id)
    raw = json.loads(path.read_text())
    raw.pop("snapshot_version")
    path.write_text(json.dumps(raw))
    assert server.control_plane_store.load(staged.deployment_id).snapshot_version == 0
    with pytest.raises(RuntimeError, match="lacks resolved defaults"):
        await server._activate_control_deployment(staged.deployment_id)
    headers = {"Authorization": f"Bearer {server._control_token}"}
    async with TestClient(TestServer(server.app)) as client:
        response = await client.post("/api/control/rollback", headers=headers,
                                     json={"deployment_id": staged.deployment_id, "reason": "Old record test", "created_by": "test-operator"})
        assert response.status == 400
        assert "defaults were not recorded" in (await response.json())["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize("omit_behavior", [False, True])
async def test_package_behavior_replaces_legacy_words_and_survives_restart_and_rollback(
    tmp_path, omit_behavior,
):
    server = _server(tmp_path)
    compiler = server.persona_compiler
    compiler.update_persona({"communication": {"default_style": "LegacyProduct wording"},
                             "human_conversation": {"presence": ["LegacyProduct instructions"]}})
    original = server._current_agent_package().model_dump(mode="json")
    original["package_id"] = "original_business"
    original_record = _stage_behavior_package(server, original)
    await server._activate_control_deployment(original_record.deployment_id)

    replacement = server._current_agent_package().model_dump(mode="json")
    replacement["package_id"] = "replacement_business"
    replacement["behavior"] = {"communication": {"default_style": "ReplacementProduct wording"},
                               "human_conversation": {"presence": ["ReplacementProduct instructions"]}}
    if omit_behavior:
        replacement.pop("behavior")
    replacement_record = _stage_behavior_package(server, replacement)
    await server._activate_control_deployment(replacement_record.deployment_id)
    prompt = compiler.compile()
    assert "LegacyProduct" not in prompt
    assert ("ReplacementProduct" in prompt) is not omit_behavior
    reloaded = PersonaCompiler(persona_path=compiler.persist_path)
    assert reloaded.conversation_behavior() == compiler.conversation_behavior()
    assert "LegacyProduct" not in reloaded.compile()

    # Rollback uses a new activation of the recorded original package.
    rollback = _stage_behavior_package(server, original_record.package.model_dump(mode="json"))
    await server._activate_control_deployment(rollback.deployment_id)
    assert "LegacyProduct" in compiler.compile()
    assert "ReplacementProduct" not in compiler.compile()
    assert server._current_agent_package().behavior == original_record.package.behavior


@pytest.mark.asyncio
async def test_failed_activation_restores_persona_file_and_in_memory_behavior(tmp_path, monkeypatch):
    server = _server(tmp_path)
    compiler = server.persona_compiler
    before_file = compiler.persist_path.read_bytes()
    before_behavior = compiler.conversation_behavior()
    before_prompt = compiler.compile()
    package = server._current_agent_package().model_dump(mode="json")
    package["behavior"]["communication"]["default_style"] = "FailedProduct wording"
    staged = _stage_behavior_package(server, package)

    def fail_after_behavior_write(*args):
        assert "FailedProduct" in compiler.compile()
        raise RuntimeError("Injected activation commit failure")

    monkeypatch.setattr(server.control_plane_store, "mark_active", fail_after_behavior_write)
    with pytest.raises(RuntimeError, match="Injected activation"):
        await server._activate_control_deployment(staged.deployment_id)
    assert compiler.persist_path.read_bytes() == before_file
    assert compiler.conversation_behavior() == before_behavior
    assert compiler.compile() == before_prompt
    assert server.control_plane_store.load(staged.deployment_id).state == "failed"


@pytest.mark.asyncio
async def test_persona_edit_after_package_staging_invalidates_activation(tmp_path):
    server = _server(tmp_path)
    staged = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    server.persona_compiler.update_persona({"communication": {"default_style": "A newer operator edit"}})
    with pytest.raises(RuntimeError, match="changed after staging"):
        await server._activate_control_deployment(staged.deployment_id)
    assert "A newer operator edit" in server.persona_compiler.compile()


def test_package_behavior_rejects_malformed_and_deceptive_instructions(tmp_path):
    server = _server(tmp_path)
    package = server._current_agent_package().model_dump(mode="json")
    package["behavior"]["trait_intensity"]["patient"] = True
    with pytest.raises(ValueError):
        AgentPackage.model_validate(package)
    package["behavior"]["trait_intensity"]["patient"] = 0.9
    package["behavior"]["communication"] = {"default_style": "Pretend to be a real human"}
    with pytest.raises(ValueError, match="AI disclosure"):
        server._validate_agent_package(AgentPackage.model_validate(package))


@pytest.mark.asyncio
async def test_stale_package_and_in_call_activation_fail_closed(tmp_path: Path) -> None:
    server = _server(tmp_path)
    headers = {"Authorization": f"Bearer {server._control_token}"}
    async with TestClient(TestServer(server.app)) as client:
        package = (await (await client.get("/api/control/package", headers=headers)).json())[
            "package"
        ]
        package["package_id"] = "stale_test"
        staged = await (
            await client.post(
                "/api/control/stage",
                headers=headers,
                json={
                    "package": package,
                    "reason": "Stale write regression",
                    "created_by": "hermes-control-agent",
                },
            )
        ).json()
        server.system_prompt = "Changed after staging"
        response = await client.post(
            "/api/control/activate",
            headers=headers,
            json={"deployment_id": staged["deployment"]["deployment_id"]},
        )
        assert response.status == 409
        assert "changed after staging" in (await response.json())["message"]


@pytest.mark.parametrize("key", ["version", "revision", "fingerprint", "created_at", "updated_at", "package_id", "display_name"])
def test_business_keys_are_not_mistaken_for_bookkeeping(key):
    from phone_agent_gateway.ai_bridge.control_plane import state_hash

    before = {"package_id": "business_a", "task": {"knowledge": {key: "before"}}}
    after = {"package_id": "business_a", "task": {"knowledge": {key: "after"}}}
    assert state_hash(before) != state_hash(after)
    assert state_hash(before) != state_hash({**before, "package_id": "business_b"})
    assert state_hash({"skills": [{"version": "1"}]}) != state_hash({"skills": [{"version": "2"}]})


def test_only_declared_bookkeeping_paths_are_normalized():
    from phone_agent_gateway.ai_bridge.control_plane import state_hash

    before = {"identity": {"version": 1, "created_at": "a", "updated_at": "a"},
              "tools": {"revision": 1, "fingerprint": "a", "connections": [{"headers": {"version": "1"}}]},
              "memory_blocks": [{"updated_at": "a", "valid_until": "2030-01-01"}]}
    after = {**before, "identity": {"version": 2, "created_at": "b", "updated_at": "b"},
             "tools": {**before["tools"], "revision": 2, "fingerprint": "b"},
             "memory_blocks": [{"updated_at": "b", "valid_until": "2030-01-01"}]}
    assert state_hash(before) == state_hash(after)
    after["tools"]["connections"] = [{"headers": {"version": "2"}}]
    assert state_hash(before) != state_hash(after)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["version", "updated_at"])
async def test_staged_package_cannot_overwrite_newer_business_metadata(tmp_path, key):
    server = _server(tmp_path)
    task = server.task_engine.require_contract(server.task_id)
    server.task_engine.save_contract({**task, "knowledge": {key: "before"}})
    staged = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    server.task_engine.save_contract({**task, "knowledge": {key: "after"}})
    with pytest.raises(RuntimeError, match="changed after staging"):
        await server._activate_control_deployment(staged.deployment_id)
    assert server.task_engine.require_contract(server.task_id)["knowledge"][key] == "after"


@pytest.mark.asyncio
async def test_masked_integration_key_change_invalidates_staging_token(tmp_path):
    server = _server(tmp_path)
    config = server.openwa_config_store.public_state()
    server.openwa_config_store.save({**config, "api_key": "synthetic-key-a"})
    semantic_hash = server._effective_control_state_hash()
    staged = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    private_config = server.openwa_config_store.load().model_dump(mode="json")
    private_config["api_key"] = "synthetic-key-b"
    # Model external credential rotation without a revision increment.
    server.openwa_config_store.path.write_text(json.dumps(private_config))
    # Public masks and effective non-secret behavior are unchanged, but the
    # complete state token must detect that the underlying binding changed.
    assert server._effective_control_state_hash() == semantic_hash
    assert server._configuration_state_hash() != staged.base_state_hash
    with pytest.raises(RuntimeError, match="changed after staging"):
        await server._activate_control_deployment(staged.deployment_id)
    assert server.openwa_config_store.load().api_key == "synthetic-key-b"


@pytest.mark.asyncio
async def test_activation_rechecks_call_state_after_waiting_for_lock(tmp_path, monkeypatch):
    import asyncio

    server = _server(tmp_path)
    staged = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    entered = asyncio.Event()
    call_active = False

    def call_in_progress():
        entered.set()
        return call_active

    monkeypatch.setattr(server, "_dial_in_progress", call_in_progress)
    await server._control_activation_lock.acquire()
    activation = asyncio.create_task(server._activate_control_deployment(staged.deployment_id))
    await asyncio.wait_for(entered.wait(), timeout=1)
    call_active = True
    server._control_activation_lock.release()
    with pytest.raises(RuntimeError, match="blocked during a call"):
        await activation
    assert server.control_plane_store.load(staged.deployment_id).state == "staged"


@pytest.mark.asyncio
async def test_effective_hash_matches_readback_after_bookkeeping_updates(tmp_path):
    from phone_agent_gateway.ai_bridge.control_plane import state_hash

    server = _server(tmp_path)
    staged = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    await server._activate_control_deployment(staged.deployment_id)
    expected = staged.package.model_dump(mode="json")
    actual = server._current_agent_package().model_dump(mode="json")
    differences = [key for key in expected if state_hash({key: expected[key]}) != state_hash({key: actual[key]})]
    assert differences == [], {key: (expected["task"].get(key), actual["task"].get(key))
                               for key in expected["task"].keys() | actual["task"].keys()
                               if expected["task"].get(key) != actual["task"].get(key)}
    assert server._effective_control_state_hash() == staged.validation.effective_state_hash


def test_resolved_task_preserves_explicit_empty_tool_allowlist(tmp_path):
    server = _server(tmp_path)
    payload = server._current_agent_package().model_dump(mode="json")
    payload["task"]["allowed_tools"] = []
    payload["task"]["objective"] = "  Resolve the caller's request.  "
    resolved = server._resolve_agent_package(AgentPackage.model_validate(payload))
    assert resolved.task["allowed_tools"] == []
    assert resolved.task["objective"] == "Resolve the caller's request."
    server.task_engine.save_contract(resolved.task)
    assert server.task_engine.require_contract(server.task_id)["allowed_tools"] == []


@pytest.mark.asyncio
async def test_control_events_are_bounded_cursor_readable_and_redacted(tmp_path: Path) -> None:
    server = _server(tmp_path)
    headers = {"Authorization": f"Bearer {server._control_token}"}
    await server.broadcast(
        {"type": "transcript", "role": "user", "text": "Hello", "caller_id": "+33123456789"}
    )
    async with TestClient(TestServer(server.app)) as client:
        response = await client.get(
            "/api/control/events?after=0&limit=10", headers=headers
        )
        payload = await response.json()
    assert response.status == 200
    assert payload["events"][0]["sequence"] == 1
    assert payload["events"][0]["caller_id"].startswith("sha256:")
    assert "+33123456789" not in json.dumps(payload)


def test_web_server_migrates_persisted_s2s_studio_settings(tmp_path: Path) -> None:
    settings_file = tmp_path / "studio.json"
    settings_file.write_text(json.dumps({"pipeline_mode": "s2s_chatgpt_realtime"}), encoding="utf-8")
    persona_path = tmp_path / "persona.yaml"
    persona_path.write_bytes(DEFAULT_PERSONA_PATH.read_bytes())
    compiler = PersonaCompiler(persona_path=persona_path, examples_path=DEFAULT_EXAMPLES_PATH)
    server = PhoneAgentWebServer(
        config=ProviderConfig(pipeline_mode="cascade"),
        persona_compiler=compiler,
        task_engine=TaskEngine(user_contracts_dir=tmp_path / "tasks"),
        settings_path=settings_file,
        audit_ledger=AuditLedger(tmp_path / "audit.jsonl"),
    )
    assert server.config.pipeline_mode == "cascade"


def test_runtime_candidate_migrates_s2s_agent_package_runtime(tmp_path: Path) -> None:
    persona_path = tmp_path / "persona.yaml"
    persona_path.write_bytes(DEFAULT_PERSONA_PATH.read_bytes())
    compiler = PersonaCompiler(persona_path=persona_path, examples_path=DEFAULT_EXAMPLES_PATH)
    server = PhoneAgentWebServer(
        config=ProviderConfig(pipeline_mode="cascade"),
        persona_compiler=compiler,
        task_engine=TaskEngine(user_contracts_dir=tmp_path / "tasks"),
        settings_path=tmp_path / "studio.json",
        audit_ledger=AuditLedger(tmp_path / "audit.jsonl"),
    )
    rc = RuntimeControl(pipeline_mode="s2s_chatgpt_realtime")
    candidate = server._runtime_candidate(rc)
    assert candidate.pipeline_mode == "cascade"


@pytest.mark.asyncio
async def test_retired_controls_disappear_from_schema_settings_and_worker_environment(tmp_path, monkeypatch):
    from phone_agent_gateway.ai_bridge.control_plane import LEGACY_RUNTIME_FIELDS

    server = _server(tmp_path)
    schema = RuntimeControl.model_json_schema()
    assert not (LEGACY_RUNTIME_FIELDS & schema["properties"].keys())
    assert schema["properties"]["pipeline_mode"]["const"] == "cascade"
    assert not (LEGACY_RUNTIME_FIELDS & ProviderConfig.__dataclass_fields__.keys())
    monkeypatch.setenv("PHONE_AGENT_CHATGPT_VOICE", "retired-voice")
    monkeypatch.setenv("PHONE_AGENT_CHATGPT_VAD_THRESHOLD", "invalid-retired-value")
    env = server._child_environment()
    assert not any(name.startswith("PHONE_AGENT_CHATGPT_") for name in env)
    async with TestClient(TestServer(server.app)) as client:
        current = await (await client.get("/api/config")).json()
        assert not (LEGACY_RUNTIME_FIELDS & current.keys())
        for field in LEGACY_RUNTIME_FIELDS:
            response = await client.post("/api/config", json={field: "retired-value"})
            assert response.status == 400
            assert "retired" in (await response.json())["message"]


def test_legacy_settings_and_package_import_preserve_cascade_values(tmp_path):
    from phone_agent_gateway.ai_bridge.control_plane import LEGACY_RUNTIME_FIELDS

    server = _server(tmp_path)
    data = server._public_config()
    data.update({field: {"ignored": "legacy-only"} for field in LEGACY_RUNTIME_FIELDS})
    data.update(pipeline_mode="s2s_chatgpt_realtime", antigravity_live_endpoint_ms=780,
                llm_provider="antigravity_gemini", llm_model="fixture-bridge-model")
    server.settings_path.write_text(json.dumps(data))
    server._load_saved_settings()
    assert server.config.pipeline_mode == "cascade"
    assert server.config.antigravity_live_endpoint_ms == 780
    assert server.config.llm_model == "fixture-bridge-model"
    server._persist_settings()
    assert not (LEGACY_RUNTIME_FIELDS & json.loads(server.settings_path.read_text()).keys())
    payload = server._current_agent_package().model_dump(mode="json")
    payload["runtime"].update(data)
    # Import only actual runtime fields, not public status and task metadata.
    payload["runtime"] = {key: value for key, value in payload["runtime"].items()
                          if key in RuntimeControl.model_fields or key in LEGACY_RUNTIME_FIELDS}
    imported = AgentPackage.model_validate(payload)
    assert imported.runtime.pipeline_mode == "cascade"
    assert imported.runtime.antigravity_live_endpoint_ms == 780
    assert not (LEGACY_RUNTIME_FIELDS & imported.runtime.model_dump().keys())


@pytest.mark.parametrize("omit_legacy_timestamps", [False, True])
def test_legacy_deployment_metadata_update_preserves_original_hashed_package(tmp_path, omit_legacy_timestamps):
    import hashlib

    server = _server(tmp_path)
    staged = _stage_behavior_package(server, server._current_agent_package().model_dump(mode="json"))
    path = server.control_plane_store._path(staged.deployment_id)
    raw = json.loads(path.read_text())
    raw["snapshot_version"] = 4
    raw["package"]["runtime"].update(pipeline_mode="s2s_chatgpt_realtime", chatgpt_realtime_voice="historic-voice")
    if omit_legacy_timestamps:
        raw["package"]["identity"].pop("created_at")
        raw["package"]["identity"].pop("updated_at")
    original_package = raw["package"]
    original_hash = "sha256:" + hashlib.sha256(json.dumps(original_package, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    raw["package_hash"] = raw["validation"]["package_hash"] = original_hash
    path.write_text(json.dumps(raw))
    view = server.control_plane_store.load(staged.deployment_id)
    assert view.package.runtime.pipeline_mode == "cascade"
    assert "chatgpt_realtime_voice" not in view.package.runtime.model_dump()
    server.control_plane_store.mark_failed(staged.deployment_id, "Historical metadata regression")
    saved = json.loads(path.read_text())
    assert saved["state"] == "failed"
    assert saved["package"] == original_package
    assert saved["package_hash"] == original_hash
    changed = view.model_copy(update={"package": view.package.model_copy(update={"objective": "Attempted overwrite"})})
    with pytest.raises(RuntimeError, match="immutable"):
        server.control_plane_store.save(changed)
    assert json.loads(path.read_text())["package"] == original_package
