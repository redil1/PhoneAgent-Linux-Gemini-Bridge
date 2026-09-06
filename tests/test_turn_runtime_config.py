"""Turn controls preserve acoustic patience through environment configuration."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from phone_agent_gateway.ai_bridge import runtime_config
from phone_agent_gateway.ai_bridge.runtime_config import ConfigurationError, ProviderConfig


@pytest.fixture
def clean_turn_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Private operator settings must not choose the behavior these tests verify.
    monkeypatch.setattr(runtime_config, "load_user_secrets", lambda: [])
    for name in (
        "PHONE_AGENT_ANTIGRAVITY_ENDPOINT_MS",
        "PHONE_AGENT_ANTIGRAVITY_INCOMPLETE_ENDPOINT_MS",
        "PHONE_AGENT_ANTIGRAVITY_FALLBACK_ENDPOINT_MS",
        "PHONE_AGENT_SPECULATIVE_PIPELINE",
        "PHONE_AGENT_SPECULATIVE_PREFETCH_SILENCE_MS",
        "PHONE_AGENT_SPECULATIVE_FAST_ENDPOINT_MS",
        "PHONE_AGENT_SPECULATIVE_AMBIGUOUS_ENDPOINT_MS",
        "PHONE_AGENT_SPECULATIVE_INCOMPLETE_ENDPOINT_MS",
        "PHONE_AGENT_SMART_TURN_ENABLED",
        "PHONE_AGENT_SMART_TURN_MODEL_PATH",
        "PHONE_AGENT_SMART_TURN_COMPLETION_THRESHOLD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_enabling_speculation_preserves_default_turn_patience(
    clean_turn_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    normal = ProviderConfig.from_env(require_credentials=False)
    monkeypatch.setenv("PHONE_AGENT_SPECULATIVE_PIPELINE", "true")
    speculative = ProviderConfig.from_env(require_credentials=False)

    assert normal.speculative_pipeline_enabled is False
    assert speculative.speculative_pipeline_enabled is True
    for config in (ProviderConfig(), normal, speculative):
        assert config.antigravity_live_endpoint_ms == 600
        assert config.antigravity_live_fallback_endpoint_ms == 900
        assert config.antigravity_live_incomplete_endpoint_ms == 3000
        assert config.speculative_fast_endpoint_ms == config.antigravity_live_endpoint_ms
        assert (
            config.speculative_ambiguous_endpoint_ms
            == config.antigravity_live_fallback_endpoint_ms
        )
        assert (
            config.speculative_incomplete_endpoint_ms
            == config.antigravity_live_incomplete_endpoint_ms
        )
        assert config.speculative_prefetch_silence_ms < config.antigravity_live_endpoint_ms
        assert config.smart_turn_enabled is True
        assert config.smart_turn_completion_threshold == 0.5


def test_smart_turn_configuration_accepts_calibration_and_local_model(
    clean_turn_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHONE_AGENT_SMART_TURN_COMPLETION_THRESHOLD", "0.73")
    monkeypatch.setenv("PHONE_AGENT_SMART_TURN_MODEL_PATH", "/models/smart-turn-v3.2-cpu.onnx")
    monkeypatch.setenv("PHONE_AGENT_SMART_TURN_ENABLED", "false")

    config = ProviderConfig.from_env(require_credentials=False)

    assert config.smart_turn_completion_threshold == 0.73
    assert config.smart_turn_model_path == "/models/smart-turn-v3.2-cpu.onnx"
    assert config.smart_turn_enabled is False


@pytest.mark.parametrize("threshold", ["0", "-0.1", "1.1", "nan", "inf"])
def test_invalid_smart_turn_threshold_fails_in_environment_and_direct_config(
    threshold: str,
    clean_turn_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHONE_AGENT_SMART_TURN_COMPLETION_THRESHOLD", threshold)

    with pytest.raises(ConfigurationError, match="SMART_TURN_COMPLETION_THRESHOLD"):
        ProviderConfig.from_env(require_credentials=False)
    with pytest.raises(ConfigurationError, match="SMART_TURN_COMPLETION_THRESHOLD"):
        replace(ProviderConfig(), smart_turn_completion_threshold=float(threshold)).validate(
            require_credentials=False
        )


def test_smart_turn_threshold_supports_maximum_patience_setting(
    clean_turn_environment: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHONE_AGENT_SMART_TURN_COMPLETION_THRESHOLD", "1")

    assert ProviderConfig.from_env(require_credentials=False).smart_turn_completion_threshold == 1


def test_smart_turn_is_registered_as_a_durable_control() -> None:
    registry_path = Path(runtime_config.__file__).with_name("feature_flags.json")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    controls = {control["name"]: control for control in registry["durable_controls"]}

    assert controls["PHONE_AGENT_SMART_TURN_ENABLED"]["classification"] == (
        "model_behavior_configuration"
    )
