"""Versioned external-agent control contracts for PhoneAgent.

The control plane changes declarative configuration only. It deliberately has
no filesystem, shell, Android, codec, PCM, or media-pipeline operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .identity.models import IdentityProfile, MemoryBlock
from .identity.skills import SkillDraft
from .provider_credentials import credential_refs_schema, validate_credential_refs
from .secure_storage import atomic_write_private, harden_private_file

DEFAULT_CONTROL_PLANE_ROOT = Path.home() / ".config" / "phone-agent" / "control-plane"
LEGACY_RUNTIME_FIELDS = frozenset({
    "chatgpt_realtime_voice", "chatgpt_realtime_model", "chatgpt_realtime_transport",
    "chatgpt_realtime_reasoning_effort", "chatgpt_realtime_transcription_model",
    "chatgpt_realtime_input_languages", "chatgpt_realtime_noise_reduction",
    "chatgpt_realtime_vad_mode", "chatgpt_realtime_vad_eagerness",
    "chatgpt_realtime_vad_threshold", "chatgpt_realtime_vad_prefix_ms",
    "chatgpt_realtime_vad_silence_ms", "chatgpt_realtime_idle_timeout_ms", "chatgpt_realtime_speed",
})


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class StrictControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


def _empty_skill_drafts() -> list[SkillDraft]:
    return []


def _empty_memory_blocks() -> list[MemoryBlock]:
    return []


def _empty_checks() -> list[dict[str, Any]]:
    return []


class ConversationTuning(StrictControlModel):
    """Optional new controls preserve effective values in older package payloads."""

    smart_turn_enabled: bool | None = Field(default=None, strict=True)
    smart_turn_completion_threshold: float | None = Field(default=None, gt=0, le=1, strict=True)
    conversation_repair_enabled: bool | None = Field(default=None, strict=True)
    antigravity_live_local_corroboration: bool | None = Field(default=None, strict=True)
    antigravity_live_endpoint_ms: int | None = Field(default=None, ge=100, le=3000, strict=True)
    antigravity_live_incomplete_endpoint_ms: int | None = Field(default=None, ge=200, le=4000, strict=True)
    antigravity_live_stability_ms: int | None = Field(default=None, ge=40, le=1000, strict=True)
    antigravity_live_partial_stability_ms: int | None = Field(default=None, ge=100, le=2000, strict=True)
    antigravity_live_fallback_endpoint_ms: int | None = Field(default=None, ge=150, le=5000, strict=True)
    edge_tts_rate: str | None = Field(default=None, pattern=r'^[+-]\d+%$', max_length=24)
    edge_tts_volume: str | None = Field(default=None, pattern=r'^[+-]\d+%$', max_length=24)
    edge_tts_pitch: str | None = Field(default=None, pattern=r'^[+-]\d+Hz$', max_length=24)
    antigravity_live_chunk_ms: int | None = Field(default=None, ge=50, le=2000, strict=True)
    antigravity_live_context_bias: str | None = Field(default=None, max_length=4000)
    flux_eager_eot_threshold: float | None = Field(default=None, ge=0, le=1, strict=True)
    flux_eot_threshold: float | None = Field(default=None, ge=0, le=1, strict=True)
    flux_eot_timeout_ms: int | None = Field(default=None, ge=250, le=5000, strict=True)
    parakeet_endpoint_ms: int | None = Field(default=None, ge=200, le=3000, strict=True)
    parakeet_incomplete_endpoint_ms: int | None = Field(default=None, ge=300, le=4000, strict=True)
    speculative_prefetch_silence_ms: int | None = Field(default=None, ge=50, le=1000, strict=True)
    speculative_prefetch_stability_ms: int | None = Field(default=None, ge=40, le=1000, strict=True)
    speculative_fast_endpoint_ms: int | None = Field(default=None, ge=100, le=1200, strict=True)
    speculative_ambiguous_endpoint_ms: int | None = Field(default=None, ge=150, le=1800, strict=True)
    speculative_incomplete_endpoint_ms: int | None = Field(default=None, ge=200, le=3000, strict=True)
    speculative_commit_wait_ms: int | None = Field(default=None, ge=0, le=500, strict=True)
    conversational_reflex_cooldown_ms: int | None = Field(default=None, ge=0, le=60000, strict=True)
    ollama_keep_alive: str | None = Field(default=None, min_length=1, max_length=32)
    ollama_prewarm: bool | None = Field(default=None, strict=True)
    ollama_think: bool | None = Field(default=None, strict=True)
    ollama_temperature: float | None = Field(default=None, ge=0, le=2, strict=True)
    ollama_top_p: float | None = Field(default=None, ge=0, le=1, strict=True)
    ollama_top_k: int | None = Field(default=None, ge=0, le=1000, strict=True)
    ollama_min_p: float | None = Field(default=None, ge=0, le=1, strict=True)
    ollama_presence_penalty: float | None = Field(default=None, ge=-2, le=2, strict=True)
    ollama_num_predict: int | None = Field(default=None, ge=16, le=4096, strict=True)
    ollama_num_ctx: int | None = Field(default=None, ge=2048, le=131072, strict=True)
    ollama_turn_timeout_secs: int | None = Field(default=None, ge=2, le=300, strict=True)
    codex_reasoning_effort: Literal["low", "medium", "high", "xhigh"] | None = None
    codex_turn_timeout_secs: int | None = Field(default=None, ge=5, le=120, strict=True)
    gemini_cli_turn_timeout_secs: int | None = Field(default=None, ge=5, le=120, strict=True)
    tts_max_buffer_delay_ms: int | None = Field(default=None, ge=0, le=5000, strict=True)
    edge_tts_phrase_min_chars: int | None = Field(default=None, ge=8, le=120, strict=True)
    edge_tts_phrase_max_chars: int | None = Field(default=None, ge=16, le=240, strict=True)
    edge_tts_connect_timeout_secs: int | None = Field(default=None, ge=1, le=30, strict=True)
    edge_tts_receive_timeout_secs: int | None = Field(default=None, ge=2, le=120, strict=True)
    supertonic_steps: int | None = Field(default=None, ge=1, le=100, strict=True)
    supertonic_speed: float | None = Field(default=None, ge=0.7, le=2, strict=True)
    supertonic_intra_op_threads: int | None = Field(default=None, ge=0, le=64, strict=True)
    supertonic_inter_op_threads: int | None = Field(default=None, ge=0, le=64, strict=True)
    supertonic_fallback_to_edge: bool | None = Field(default=None, strict=True)
    vibevoice_ddpm_steps: int | None = Field(default=None, ge=1, le=100, strict=True)
    vibevoice_cfg_scale: float | None = Field(default=None, ge=0.5, le=5, strict=True)
    whatsapp_max_duration_secs: int | None = Field(default=None, ge=30, le=3600, strict=True)
    codex_binary: str | None = Field(default=None, max_length=4096)
    gemini_cli_binary: str | None = Field(default=None, max_length=4096)
    edge_tts_ffmpeg_binary: str | None = Field(default=None, min_length=1, max_length=4096)
    smart_turn_model_path: str | None = Field(default=None, max_length=4096)
    ollama_base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    openrouter_base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    vllm_base_url: str | None = Field(default=None, min_length=1, max_length=2048)
    lmstudio_base_url: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("ollama_base_url", "openrouter_base_url", "vllm_base_url", "lmstudio_base_url")
    @classmethod
    def _service_address(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError:
            raise ValueError("Service address is malformed") from None
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or any(char.isspace() for char in value)):
            raise ValueError("Service address must be HTTP(S) without credentials, query or fragment")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Service address port is invalid")
        return value

    @field_validator("codex_binary", "gemini_cli_binary", "edge_tts_ffmpeg_binary", "smart_turn_model_path")
    @classmethod
    def _host_path(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Host paths cannot contain control characters")
        return value


def conversation_tuning_env_name(name: str) -> str:
    aliases = {
        "conversation_repair_enabled": "PHONE_AGENT_CONVERSATION_REPAIR",
        "ollama_keep_alive": "OLLAMA_KEEP_ALIVE",
        "codex_reasoning_effort": "CODEX_REASONING_EFFORT",
        "codex_turn_timeout_secs": "CODEX_TURN_TIMEOUT_SECS",
        "gemini_cli_turn_timeout_secs": "GEMINI_CLI_TURN_TIMEOUT_SECS",
        "whatsapp_max_duration_secs": "PHONE_AGENT_WHATSAPP_MAX_SECS",
        "codex_binary": "CODEX_APP_SERVER_BINARY",
        "gemini_cli_binary": "GEMINI_CLI_BINARY",
        "ollama_base_url": "OLLAMA_BASE_URL",
        "openrouter_base_url": "OPENROUTER_BASE_URL",
    }
    return aliases.get(name, "PHONE_AGENT_" + name.replace("antigravity_live_", "antigravity_").upper())


def conversation_tuning_environment(config: Any) -> dict[str, str]:
    return {
        conversation_tuning_env_name(name): (str(value).lower() if isinstance(value, bool) else str(value))
        for name in ConversationTuning.model_fields
        if (value := getattr(config, name)) is not None
    }


def reported_conversation_tuning(config: Any) -> dict[str, Any]:
    values = {name: getattr(config, name) for name in ConversationTuning.model_fields}
    # Context bias can contain private business vocabulary. Compare its digest
    # in readiness events rather than logging the prompt text.
    values["antigravity_live_context_bias_sha256"] = hashlib.sha256(
        str(values.pop("antigravity_live_context_bias")).encode("utf-8")
    ).hexdigest()
    return values


class RuntimeControl(ConversationTuning):
    """Call-quality and provider parameters safe to change between calls."""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_runtime(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        migrated = {key: item for key, item in cast(dict[str, Any], value).items()
                    if key not in LEGACY_RUNTIME_FIELDS}
        if migrated.get("pipeline_mode") == "s2s_chatgpt_realtime":
            migrated["pipeline_mode"] = "cascade"
        return migrated

    credential_refs: dict[str, list[str]] | None = Field(default=None, json_schema_extra=credential_refs_schema())
    memory_enabled: bool | None = Field(
        default=None, strict=True,
        description="Automatic caller-history recall and persistence for subsequent calls; separate from approved package knowledge, audit records and per-call recording consent.",
    )

    @field_validator("credential_refs")
    @classmethod
    def _credential_refs(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        return None if value is None else validate_credential_refs(value)

    pipeline_mode: Literal["cascade"] = "cascade"
    call_channel: Literal["gsm", "whatsapp_phone", "whatsapp"] = "gsm"
    stt_provider: str = Field(default="parakeet_local", min_length=1, max_length=100)
    stt_model: str = Field(default="mlx-community/parakeet-tdt-0.6b-v3", max_length=240)
    stt_language: str = Field(default="en-US", min_length=2, max_length=20)
    llm_provider: str = Field(default="antigravity_gemini", min_length=1, max_length=100)
    llm_model: str = Field(default="gemini-3.1-flash-lite", max_length=240)
    tts_provider: str = Field(default="supertonic", min_length=1, max_length=100)
    tts_model: str = Field(default="supertonic-2", max_length=240)
    tts_voice_id: str = Field(default="M1", min_length=1, max_length=240)
    tts_aggregation: Literal["phrase", "sentence", "token"] = "sentence"
    google_tts_scene: str = Field(default="", max_length=4_000)
    google_tts_sample_context: str = Field(default="", max_length=4_000)
    speculative_pipeline_enabled: bool = False
    conversational_reflex_enabled: bool = False
    auto_answer_enabled: bool = True
    whatsapp_country_code: str = Field(default="212", pattern=r"^[0-9]{1,4}$")
    system_prompt: str = Field(default="", max_length=12_000)


class ConversationBehavior(StrictControlModel):
    """Package-owned legacy conversation controls; identity remains in its own profile."""

    defaults_resolved: bool = Field(default=False, strict=True)
    trait_intensity: dict[str, Annotated[float, Field(strict=True, ge=0, le=1)]] = Field(
        default_factory=dict, max_length=32
    )
    communication: dict[str, str | list[str]] = Field(default_factory=dict, max_length=32)
    human_conversation: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _bounded(self) -> ConversationBehavior:
        if len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False)) > 40_000:
            raise ValueError("Conversation behavior exceeds its 40,000-character bound")
        if self.defaults_resolved:
            required = {"analytical", "direct", "tolerant_of_vagueness", "empathetic", "persuasive"}
            if not required <= self.trait_intensity.keys() or "default_style" not in self.communication:
                raise ValueError("Resolved behavior must include every compiled trait and speaking style")
        return self


class HostSessionRequirements(StrictControlModel):
    device_id_sha256: str = Field(pattern=r"^(?:[a-f0-9]{64})?$")
    control_host: str = Field(min_length=1, max_length=253)
    control_port: int = Field(ge=1, le=65535, strict=True)
    protocol_control_port: int = Field(ge=1, le=65535, strict=True)
    rx_port: int = Field(ge=1, le=65535, strict=True)
    tx_port: int = Field(ge=1, le=65535, strict=True)
    sample_rate: Literal[16000]
    frame_ms: Literal[20]
    input_queue_frames: int = Field(ge=2, le=50, strict=True)
    event_stream_enabled: Literal[True]
    voice_lock_path_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    link_key_sha256: str = Field(pattern=r"^(?:[a-f0-9]{64})?$")
    use_adb_forward: bool = Field(strict=True)


class AgentPackage(StrictControlModel):
    schema_version: Literal[1] = 1
    package_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=3, max_length=2_000)
    identity: IdentityProfile
    task: dict[str, Any]
    runtime: RuntimeControl
    host_requirements: HostSessionRequirements | None = Field(
        default=None, description="Required host/session values, not host setters. Omitted legacy imports bind the current host before staging.",
    )
    behavior: ConversationBehavior = Field(default_factory=ConversationBehavior)
    skills: list[SkillDraft] = Field(default_factory=_empty_skill_drafts, max_length=64)
    memory_blocks: list[MemoryBlock] = Field(default_factory=_empty_memory_blocks, max_length=32)
    tools: dict[str, Any]
    openwa: dict[str, Any]
    web_research: dict[str, Any]
    business: dict[str, Any]
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("task", "tools", "openwa", "web_research", "business")
    @classmethod
    def _bounded_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False, default=str)) > 96_000:
            raise ValueError("AgentPackage component exceeds its size bound")
        return value

    @field_validator("memory_blocks")
    @classmethod
    def _only_mutable_memory(cls, values: list[MemoryBlock]) -> list[MemoryBlock]:
        if any(not block.mutable or block.kind.value == "self" for block in values):
            raise ValueError("AgentPackage may contain only mutable non-self memory blocks")
        ids = [block.block_id for block in values]
        if len(ids) != len(set(ids)):
            raise ValueError("AgentPackage memory block ids must be unique")
        return values

    @field_validator("labels")
    @classmethod
    def _labels(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > 32:
            raise ValueError("AgentPackage has too many labels")
        result: dict[str, str] = {}
        for key, value in values.items():
            name = str(key).strip()
            text = str(value).strip()
            if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", name) or len(text) > 240:
                raise ValueError("AgentPackage label is invalid")
            result[name] = text
        return result

    @model_validator(mode="after")
    def _total_size(self) -> AgentPackage:
        size = len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False))
        if size > 400_000:
            raise ValueError("AgentPackage exceeds the 400,000-character transport bound")
        return self


class PackageValidation(StrictControlModel):
    valid: bool
    package_hash: str
    effective_state_hash: str
    checks: list[dict[str, Any]] = Field(default_factory=_empty_checks, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)


class DeploymentRecord(StrictControlModel):
    schema_version: Literal[1] = 1
    snapshot_version: Literal[0, 1, 2, 3, 4, 5, 6, 7] = 0
    deployment_id: str = Field(pattern=r"^dep_[a-f0-9]{24}$")
    state: Literal["staged", "activating", "active", "superseded", "failed"] = "staged"
    package_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    base_state_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    package: AgentPackage
    validation: PackageValidation
    reason: str = Field(min_length=3, max_length=1_000)
    created_by: str = Field(min_length=2, max_length=120)
    created_at: str = Field(default_factory=_now)
    activated_at: str | None = None
    failure: str = Field(default="", max_length=1_000)


class ControlPlaneError(RuntimeError):
    pass


class ControlPlaneStore:
    def __init__(self, root: Path | None = None) -> None:
        configured = os.getenv("PHONE_AGENT_CONTROL_PLANE_ROOT", "").strip()
        self.root = (
            Path(configured).expanduser()
            if configured
            else root or DEFAULT_CONTROL_PLANE_ROOT
        )
        self.deployments_dir = self.root / "deployments"
        self.active_path = self.root / "active.json"
        self.deployments_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        os.chmod(self.deployments_dir, 0o700)

    def _path(self, deployment_id: str) -> Path:
        if not re.fullmatch(r"dep_[a-f0-9]{24}", deployment_id):
            raise ControlPlaneError("deployment id is invalid")
        return self.deployments_dir / f"{deployment_id}.json"

    def save(self, record: DeploymentRecord) -> DeploymentRecord:
        payload = record.model_dump(mode="json")
        path = self._path(record.deployment_id)
        if path.exists():
            harden_private_file(path)
            previous = json.loads(path.read_text(encoding="utf-8"))
            if (previous["package_hash"] != record.package_hash
                    or AgentPackage.model_validate(previous["package"]).model_dump(exclude_unset=True)
                    != record.package.model_dump(exclude_unset=True)):
                raise ControlPlaneError("Deployment package content is immutable; stage a new deployment")
            # Model migration is a read view. Updating lifecycle metadata must
            # preserve the original package payload associated with its hash.
            payload["package"] = previous["package"]
        atomic_write_private(
            path,
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        )
        return record

    def stage(
        self,
        package: AgentPackage,
        validation: PackageValidation,
        *,
        base_state_hash: str,
        reason: str,
        actor: str,
    ) -> DeploymentRecord:
        if package.host_requirements is None or package.runtime.memory_enabled is None or package.runtime.credential_refs is None or not package.behavior.defaults_resolved or any(
            getattr(package.runtime, name) is None for name in ConversationTuning.model_fields
        ):
            raise ControlPlaneError("Package defaults must be resolved before staging")
        if not validation.valid or validation.package_hash != _hash(package):
            raise ControlPlaneError("Staging requires validation of the exact resolved package")
        record = DeploymentRecord(
            snapshot_version=7,
            deployment_id="dep_" + secrets.token_hex(12),
            package_hash=_hash(package),
            base_state_hash=base_state_hash,
            package=package,
            validation=validation,
            reason=reason,
            created_by=actor,
        )
        return self.save(record)

    def load(self, deployment_id: str) -> DeploymentRecord:
        path = self._path(deployment_id)
        harden_private_file(path)
        try:
            return DeploymentRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise ControlPlaneError(f"deployment is invalid: {exc}") from exc

    def list(self, limit: int = 50) -> list[DeploymentRecord]:
        records: list[DeploymentRecord] = []
        for path in sorted(self.deployments_dir.glob("dep_*.json"), reverse=True):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                records.append(
                    DeploymentRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
                )
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        return sorted(records, key=lambda item: item.created_at, reverse=True)[:limit]

    def mark_activating(self, deployment_id: str) -> DeploymentRecord:
        record = self.load(deployment_id)
        if record.state != "staged":
            raise ControlPlaneError("only a staged deployment can activate")
        return self.save(record.model_copy(update={"state": "activating"}))

    def mark_failed(self, deployment_id: str, message: str) -> DeploymentRecord:
        record = self.load(deployment_id)
        return self.save(
            record.model_copy(update={"state": "failed", "failure": str(message)[:1_000]})
        )

    def mark_active(self, deployment_id: str) -> DeploymentRecord:
        record = self.load(deployment_id)
        if record.state != "activating":
            raise ControlPlaneError("deployment is not activating")
        for previous in self.list(limit=500):
            if previous.state == "active" and previous.deployment_id != deployment_id:
                self.save(previous.model_copy(update={"state": "superseded"}))
        active = self.save(
            record.model_copy(update={"state": "active", "activated_at": _now()})
        )
        atomic_write_private(
            self.active_path,
            json.dumps(
                {
                    "deployment_id": active.deployment_id,
                    "package_hash": active.package_hash,
                    "activated_at": active.activated_at,
                },
                indent=2,
            )
            + "\n",
        )
        return active

    def active(self) -> DeploymentRecord | None:
        if not self.active_path.exists():
            return None
        harden_private_file(self.active_path)
        try:
            payload = json.loads(self.active_path.read_text(encoding="utf-8"))
            return self.load(str(payload["deployment_id"]))
        except (OSError, KeyError, json.JSONDecodeError, ControlPlaneError):
            return None


def package_hash(package: AgentPackage) -> str:
    return _hash(package)


def state_hash(payload: dict[str, Any]) -> str:
    """Hash behavior, excluding bookkeeping only at its declared schema paths."""

    bookkeeping: dict[tuple[str, ...], set[str]] = {
        ("identity",): {"version", "created_at", "updated_at"},
        ("tools",): {"revision", "fingerprint"},
        ("openwa",): {"revision", "fingerprint"},
        ("web_research",): {"revision", "fingerprint"},
        ("business",): {"revision", "fingerprint"},
        ("memory_blocks", "*"): {"updated_at"},
    }

    def normalize(value: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            return {
                key: normalize(item, (*path, key))
                for key, item in sorted(mapping.items())
                if key not in bookkeeping.get(path, set())
            }
        if isinstance(value, list):
            sequence = cast(list[Any], value)
            return [normalize(item, (*path, "*")) for item in sequence]
        return value

    return _hash(normalize(payload))
