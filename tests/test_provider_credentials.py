from __future__ import annotations

import json

import pytest

from phone_agent_gateway.ai_bridge.provider_credentials import (
    credential_refs_from_env,
    credential_refs_schema,
    default_credential_refs,
    resolve_provider_credentials,
    validate_credential_refs,
)
from phone_agent_gateway.ai_bridge.runtime_config import ProviderConfig


def test_ordered_references_and_explicit_disable_never_use_unlisted_environment():
    refs = default_credential_refs()
    environment = {"GOOGLE_API_KEY": "first", "GEMINI_API_KEY": "legacy-second",
                   "PHONE_AGENT_SECRET_CUSTOM": "custom-value"}
    assert resolve_provider_credentials(refs, environment)["google_api_key"] == "first"
    refs["google_api_key"] = ["env:PHONE_AGENT_SECRET_MISSING", "env:PHONE_AGENT_SECRET_CUSTOM"]
    assert resolve_provider_credentials(refs, environment)["google_api_key"] == "custom-value"
    refs["google_api_key"] = []
    assert resolve_provider_credentials(refs, environment)["google_api_key"] == ""


@pytest.mark.parametrize("value", ["fixture-secret", "env:HOME", "env:PHONE_AGENT_LINK_KEY_BASE64",
                                    "env:PHONE_AGENT_SECRET_", "env:OPENAI_API_KEY", "file:/tmp/key"])
def test_secret_values_and_unrelated_environment_references_are_rejected(value):
    refs = default_credential_refs()
    refs["google_api_key"] = [value]
    with pytest.raises(ValueError) as caught:
        validate_credential_refs(refs)
    assert value not in str(caught.value)


def test_reference_shape_and_schema_are_complete():
    import jsonschema
    refs = default_credential_refs()
    jsonschema.validate(refs, {"type": "object", **credential_refs_schema()})
    refs["google_api_key"] = ["env:HOME"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(refs, {"type": "object", **credential_refs_schema()})
    refs.pop("google_api_key")
    with pytest.raises(ValueError):
        validate_credential_refs(refs)
    with pytest.raises(ValueError, match="invalid"):
        credential_refs_from_env({"PHONE_AGENT_PROVIDER_CREDENTIAL_REFS": "invalid-private-json"})


def test_worker_resolves_only_configured_reference_without_putting_values_in_refs(monkeypatch):
    refs = default_credential_refs()
    refs["vllm_api_key"] = ["env:PHONE_AGENT_SECRET_LOCAL_MODEL"]
    monkeypatch.setenv("PHONE_AGENT_PROVIDER_CREDENTIAL_REFS", json.dumps(refs))
    monkeypatch.setenv("PHONE_AGENT_SECRET_LOCAL_MODEL", "synthetic-local-credential")
    monkeypatch.setenv("VLLM_API_KEY", "wrong-legacy-value")
    config = ProviderConfig.from_env(require_credentials=False)
    assert config.vllm_api_key == "synthetic-local-credential"
    assert "synthetic-local-credential" not in json.dumps(config.credential_refs)
    assert "synthetic-local-credential" not in repr(config)


def test_every_provider_credential_field_resolves_its_own_private_reference(monkeypatch):
    refs = default_credential_refs()
    for field in refs:
        name = "PHONE_AGENT_SECRET_TEST_" + field.upper()
        refs[field] = ["env:" + name]
        monkeypatch.setenv(name, "synthetic-private-" + field)
    monkeypatch.setenv("PHONE_AGENT_PROVIDER_CREDENTIAL_REFS", json.dumps(refs))
    config = ProviderConfig.from_env(require_credentials=False)
    assert all(getattr(config, field) == "synthetic-private-" + field for field in refs)
    assert "synthetic-private" not in repr(config)


def test_google_tts_consumer_cannot_bypass_disabled_reference_with_legacy_env(monkeypatch):
    from phone_agent_gateway.ai_bridge import production_pipeline as pipeline
    from phone_agent_gateway.ai_bridge.edge_tts_service import EdgeTTSService

    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-used")
    monkeypatch.setattr(pipeline, "_create_local_turn_service", lambda *args: object())
    monkeypatch.setattr(pipeline, "create_llm_service", lambda *args: object())
    refs = default_credential_refs()
    refs["google_api_key"] = []
    config = ProviderConfig(stt_provider="parakeet_local", tts_provider="google_genai",
                            tts_voice_id="en-US-AvaNeural", credential_refs=refs, google_api_key="")
    providers = pipeline.create_provider_services(config, 16000)
    assert isinstance(providers.tts, EdgeTTSService)
