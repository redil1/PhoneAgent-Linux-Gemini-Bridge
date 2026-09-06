"""Validated environment configuration for the Mac voice runtime."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .feature_flags import FeatureFlagError, feature_flag_enabled, transition_control_value
from .provider_credentials import (
    credential_refs_from_env,
    default_credential_refs,
    resolve_provider_credentials,
    validate_credential_refs,
)


class ConfigurationError(ValueError):
    """Required or unsafe runtime configuration."""


SECRETS_PATH = Path.home() / ".config" / "phone-agent" / "secrets.env"


def load_user_secrets(path: Path | None = None) -> list[str]:
    """Merge the operator's private key file into the environment.

    The LaunchAgent's environment is rebuilt from scratch by the installer, so a
    provider key written into the plist disappears on the next install. Keeping
    it in one mode-0600 file the runtime reads at startup survives upgrades and
    keeps the value out of the plist, the install backups, and the checkout.

    Real environment variables always win, so an operator can still override a
    stored key for a single run. Returns the names that were applied, never the
    values, so a caller can log what it picked up without leaking a secret.
    """

    secrets_path = path or SECRETS_PATH
    try:
        raw = secrets_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    applied: list[str] = []
    for line in raw.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, _, value = entry.partition("=")
        name = name.strip()
        value = value.strip().strip("'\"")
        if not name or not value or os.environ.get(name):
            continue
        os.environ[name] = value
        applied.append(name)
    return applied


_EDGE_PERCENT_RE = re.compile(r"^[+-]\d+%$")
_EDGE_PITCH_RE = re.compile(r"^[+-]\d+Hz$")

DEFAULT_GOOGLE_TTS_SCENE = (
    "Une conversation téléphonique individuelle entre un représentant téléphonique IA "
    "clairement identifié et un appelant. La voix parle depuis un environnement professionnel "
    "calme, avec une liaison téléphonique claire. Elle est attentive, chaleureuse et naturelle. "
    "Le produit, l'organisation, le rôle et le but proviennent du paquet d'agent actif. Il s'agit "
    "d'une conversation spontanée et réelle, jamais d'une publicité, d'une narration ou "
    "d'un argumentaire récité."
)
DEFAULT_GOOGLE_TTS_SAMPLE_CONTEXT = (
    "Le représentant vient d'écouter la dernière réponse de l'appelant et poursuit naturellement "
    "le même échange. Employer un français métropolitain contemporain, avec un rythme "
    "français, une accentuation naturelle, des enchaînements fluides et des liaisons discrètes "
    "uniquement lorsqu'un locuteur natif les ferait. Adopter une voix chaleureuse, assurée, "
    "calme, concise et profondément humaine. Laisser le sens de chaque phrase guider "
    "l'intonation et la ponctuation guider la respiration, sans pauses mécaniques. Éviter "
    "toute prononciation influencée par l'anglais, toute surarticulation, émotion théâtrale, "
    "intonation radiophonique ou cadence robotique. Prononcer uniquement le texte fourni, "
    "sans ajouter, supprimer, traduire ni répéter de mots."
)
GOOGLE_TTS_CONTEXT_MAX_CHARS = 2_000


def _env_bool(name: str, default: bool, *, environment: Mapping[str, str] | None = None) -> bool:
    value = (os.environ if environment is None else environment).get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _env_int(name: str, default: int, minimum: int, maximum: int, *, environment: Mapping[str, str] | None = None) -> int:
    try:
        value = int((os.environ if environment is None else environment).get(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value



STT_MODEL_DEFAULTS: dict[str, str] = {
    "sensevoice": "iic/SenseVoiceSmall",
    "sensevoice_small": "iic/SenseVoiceSmall",
    "deepgram_flux": "flux-general-en",
    "whisper_mlx": "mlx-community/whisper-large-v3-turbo-q4",
    "whisper_cuda": "large-v3-turbo",
    "whisper_turbo": "large-v3-turbo",
    "distil_whisper": "distil-large-v3",
    "whisper_local": "large-v3-turbo",
    "antigravity_live": "gemini-3.1-flash-live-preview",
    "parakeet_local": "mlx-community/parakeet-tdt-0.6b-v3",
}

TTS_MODEL_DEFAULTS: dict[str, str] = {
    "cartesia": "sonic-3",
    "openai": "tts-1",
    "deepgram": "aura-asteria-en",
    "edge_tts": "edge-online-neural",
    "kokoro": "hexgrad/Kokoro-82M",
    "google_genai": "gemini-3.1-flash-tts-preview",
    "supertonic": "supertonic-2",
    "vibevoice": "mlx-community/VibeVoice-Realtime-0.5B-8bit",
    "faster_qwen3": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "faster_qwen3_tts": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
    "qwen3_tts": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
}

TTS_VOICE_DEFAULTS: dict[str, str] = {
    "cartesia": "248be419-c632-4f23-add1-001000000000",
    "openai": "alloy",
    "deepgram": "aura-asteria-en",
    "edge_tts": "en-US-AvaNeural",
    "kokoro": "af_heart",
    "google_genai": "Algenib",
    "supertonic": "M1",
    "vibevoice": "en-Emma_woman",
    "faster_qwen3": "ryan",
    "faster_qwen3_tts": "ryan",
    "qwen3_tts": "ryan",
}

LLM_MODEL_DEFAULTS: dict[str, str] = {
    "antigravity_gemini": "gemini-2.5-flash",
    "ollama": "qwen2.5:3b",
    "openrouter": "openai/gpt-4.1",
    "openai": "gpt-4.1",
    "gemini": "gemini-2.5-flash",
    "gemini_cli": "gemini-2.5-flash",
    "codex_app": "gpt-5.6-luna",
}

@dataclass(frozen=True, slots=True)
class ProviderConfig:
    credential_refs: dict[str, list[str]] = field(default_factory=default_credential_refs)
    stt_provider: str = "antigravity_live"
    stt_model: str = "gemini-3.1-flash-live-preview"
    stt_language: str = "en-US"
    flux_eager_eot_threshold: float = 0.55
    flux_eot_threshold: float = 0.70
    flux_eot_timeout_ms: int = 1600
    antigravity_live_chunk_ms: int = 200
    antigravity_live_context_bias: str = ""
    antigravity_live_local_corroboration: bool = False
    # A short hesitation is not permission to answer. Acoustic completion can
    # release sooner than uncertain turns, while unfinished thoughts get room
    # to continue. Speculation prepares silently inside these same boundaries.
    antigravity_live_endpoint_ms: int = 600
    antigravity_live_incomplete_endpoint_ms: int = 3000
    antigravity_live_stability_ms: int = 100
    antigravity_live_partial_stability_ms: int = 650
    antigravity_live_fallback_endpoint_ms: int = 900
    parakeet_endpoint_ms: int = 600
    parakeet_incomplete_endpoint_ms: int = 3000
    speculative_pipeline_enabled: bool = True
    speculative_prefetch_silence_ms: int = 120
    speculative_prefetch_stability_ms: int = 80
    speculative_fast_endpoint_ms: int = 600
    speculative_ambiguous_endpoint_ms: int = 900
    speculative_incomplete_endpoint_ms: int = 3000
    speculative_commit_wait_ms: int = 100
    smart_turn_enabled: bool = True
    smart_turn_model_path: str = ""
    # Preserve the publisher's native decision boundary until call recordings
    # support a different calibration for the deployment's languages and audio.
    smart_turn_completion_threshold: float = 0.5
    conversation_repair_enabled: bool = True
    # Fast conversational reactions hide remote provider latency.
    conversational_reflex_enabled: bool = True
    conversational_reflex_cooldown_ms: int = 6000
    llm_provider: str = "antigravity_gemini"
    llm_model: str = "gemini-2.5-flash"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_keep_alive: str = "-1"
    ollama_prewarm: bool = True
    ollama_think: bool = False
    ollama_temperature: float = 0.7
    ollama_top_p: float = 0.8
    ollama_top_k: int = 20
    ollama_min_p: float = 0.0
    ollama_presence_penalty: float = 0.0
    ollama_num_predict: int = 192
    # The production sales prompt plus native tool schemas starts near 6k
    # tokens. 8k left too little room for a real multi-turn call and there is
    # no lossy in-call summarizer in the cascade. 16k fits comfortably on the
    # 48 GB A6000 while preserving exact recent dialogue.
    ollama_num_ctx: int = 16384
    ollama_turn_timeout_secs: int = 30
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    vllm_base_url: str = "http://127.0.0.1:8000/v1"
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    codex_binary: str = ""
    codex_reasoning_effort: str = "low"
    codex_turn_timeout_secs: int = 30
    gemini_cli_binary: str = ""
    gemini_cli_turn_timeout_secs: int = 30
    tts_provider: str = "edge_tts"
    tts_model: str = "edge-online-neural"
    tts_voice_id: str = "en-US-AvaNeural"
    tts_aggregation: str = "sentence"
    tts_max_buffer_delay_ms: int = 80
    google_tts_scene: str = DEFAULT_GOOGLE_TTS_SCENE
    google_tts_sample_context: str = DEFAULT_GOOGLE_TTS_SAMPLE_CONTEXT
    edge_tts_rate: str = "+0%"
    edge_tts_volume: str = "+0%"
    edge_tts_pitch: str = "+0Hz"
    edge_tts_ffmpeg_binary: str = "ffmpeg"
    edge_tts_phrase_min_chars: int = 8
    edge_tts_phrase_max_chars: int = 60
    edge_tts_connect_timeout_secs: int = 5
    edge_tts_receive_timeout_secs: int = 20
    supertonic_steps: int = 8
    supertonic_speed: float = 1.05
    supertonic_intra_op_threads: int = 0
    supertonic_inter_op_threads: int = 0
    supertonic_fallback_to_edge: bool = True
    vibevoice_ddpm_steps: int = 10
    vibevoice_cfg_scale: float = 1.3
    deepgram_api_key: str = field(default="", repr=False)
    openai_api_key: str = field(default="", repr=False)
    openrouter_api_key: str = field(default="", repr=False)
    vllm_api_key: str = field(default="", repr=False)
    lmstudio_api_key: str = field(default="", repr=False)
    google_api_key: str = field(default="", repr=False)
    cartesia_api_key: str = field(default="", repr=False)
    pipeline_mode: str = "cascade"
    # Which channel carries the call. The two share no resources and only one
    # call runs at a time, so selecting WhatsApp cannot disturb the GSM path.
    call_channel: str = "gsm"
    whatsapp_country_code: str = "212"
    whatsapp_max_duration_secs: int = 900

    def provider_change_defaults(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Fill omitted dependent fields when an engine changes.

        Explicit saved/API values remain authoritative. An old provider's
        defaults must not be carried into an incompatible new provider.
        """
        result = dict(updates)
        for provider_field, table, model_field in (
            ("stt_provider", STT_MODEL_DEFAULTS, "stt_model"),
            ("llm_provider", LLM_MODEL_DEFAULTS, "llm_model"),
            ("tts_provider", TTS_MODEL_DEFAULTS, "tts_model"),
        ):
            provider = str(result.get(provider_field, getattr(self, provider_field)))
            if provider != getattr(self, provider_field) and provider in table:
                result.setdefault(model_field, table[provider])
                if provider_field == "tts_provider":
                    result.setdefault("tts_voice_id", TTS_VOICE_DEFAULTS[provider])
                    result.setdefault("tts_aggregation", "phrase" if provider == "edge_tts" else "sentence")
        return result

    @classmethod
    def from_env(cls, *, require_credentials: bool = True) -> ProviderConfig:
        # Every provider path is built from this method, including the child
        # call process, so the private key file is merged here rather than at
        # one entry point that the others would miss.
        load_user_secrets()
        credential_refs = credential_refs_from_env()
        private_credentials = resolve_provider_credentials(credential_refs)
        stt_provider = os.getenv("PHONE_AGENT_STT_PROVIDER", "antigravity_live").strip().lower()

        tts_provider = os.getenv("PHONE_AGENT_TTS_PROVIDER", "edge_tts").strip().lower()


        tts_model = os.getenv("PHONE_AGENT_TTS_MODEL", "").strip() or TTS_MODEL_DEFAULTS.get(
            tts_provider, ""
        )
        supertonic_default_steps = 5 if tts_model == "supertonic-2" else 8
        llm_provider = os.getenv("PHONE_AGENT_LLM_PROVIDER", "antigravity_gemini").strip().lower()

        llm_model = os.getenv("PHONE_AGENT_LLM_MODEL", "").strip() or LLM_MODEL_DEFAULTS.get(
            llm_provider, ""
        )
        try:
            local_corroboration_enabled = feature_flag_enabled(
                "PHONE_AGENT_ANTIGRAVITY_LOCAL_CORROBORATION", default=False
            )
            speculative_pipeline_enabled = feature_flag_enabled(
                "PHONE_AGENT_SPECULATIVE_PIPELINE", default=False
            )
            conversational_reflex_enabled = feature_flag_enabled(
                "PHONE_AGENT_CONVERSATIONAL_REFLEX", default=False
            )
            supertonic_fallback_to_edge = feature_flag_enabled(
                "PHONE_AGENT_SUPERTONIC_FALLBACK_TO_EDGE", default=True
            )
            pipeline_mode = transition_control_value(
                "PHONE_AGENT_PIPELINE_MODE", default="cascade"
            )
        except FeatureFlagError as exc:
            raise ConfigurationError(str(exc)) from exc
        config = cls(
            credential_refs=credential_refs,
            deepgram_api_key=private_credentials["deepgram_api_key"],
            openai_api_key=private_credentials["openai_api_key"],
            openrouter_api_key=private_credentials["openrouter_api_key"],
            vllm_api_key=private_credentials["vllm_api_key"],
            lmstudio_api_key=private_credentials["lmstudio_api_key"],
            google_api_key=private_credentials["google_api_key"],
            cartesia_api_key=private_credentials["cartesia_api_key"],
            stt_provider=stt_provider,
            stt_model=os.getenv("PHONE_AGENT_STT_MODEL", "").strip()
            or STT_MODEL_DEFAULTS.get(stt_provider, ""),
            stt_language=os.getenv("PHONE_AGENT_STT_LANGUAGE", "en-US").strip(),
            flux_eager_eot_threshold=_env_float(
                "PHONE_AGENT_FLUX_EAGER_EOT_THRESHOLD", 0.55, 0.0, 1.0
            ),
            flux_eot_threshold=_env_float("PHONE_AGENT_FLUX_EOT_THRESHOLD", 0.70, 0.0, 1.0),
            flux_eot_timeout_ms=_env_int("PHONE_AGENT_FLUX_EOT_TIMEOUT_MS", 1600, 250, 5000),
            antigravity_live_chunk_ms=_env_int("PHONE_AGENT_ANTIGRAVITY_CHUNK_MS", 200, 50, 2000),
            antigravity_live_local_corroboration=local_corroboration_enabled,
            antigravity_live_context_bias=os.getenv(
                "PHONE_AGENT_ANTIGRAVITY_CONTEXT_BIAS", ""
            ).strip(),
            antigravity_live_endpoint_ms=_env_int(
                "PHONE_AGENT_ANTIGRAVITY_ENDPOINT_MS", 600, 100, 3000
            ),
            antigravity_live_incomplete_endpoint_ms=_env_int(
                "PHONE_AGENT_ANTIGRAVITY_INCOMPLETE_ENDPOINT_MS", 3000, 200, 4000
            ),
            antigravity_live_stability_ms=_env_int(
                "PHONE_AGENT_ANTIGRAVITY_STABILITY_MS", 100, 40, 1000
            ),
            antigravity_live_partial_stability_ms=_env_int(
                "PHONE_AGENT_ANTIGRAVITY_PARTIAL_STABILITY_MS", 650, 100, 2000
            ),
            antigravity_live_fallback_endpoint_ms=_env_int(
                "PHONE_AGENT_ANTIGRAVITY_FALLBACK_ENDPOINT_MS", 900, 150, 5000
            ),
            parakeet_endpoint_ms=_env_int("PHONE_AGENT_PARAKEET_ENDPOINT_MS", 600, 200, 3000),
            parakeet_incomplete_endpoint_ms=_env_int(
                "PHONE_AGENT_PARAKEET_INCOMPLETE_ENDPOINT_MS", 3000, 300, 4000
            ),
            speculative_pipeline_enabled=speculative_pipeline_enabled,
            speculative_prefetch_silence_ms=_env_int(
                "PHONE_AGENT_SPECULATIVE_PREFETCH_SILENCE_MS", 120, 50, 1000
            ),
            speculative_prefetch_stability_ms=_env_int(
                "PHONE_AGENT_SPECULATIVE_PREFETCH_STABILITY_MS", 80, 40, 1000
            ),
            speculative_fast_endpoint_ms=_env_int(
                "PHONE_AGENT_SPECULATIVE_FAST_ENDPOINT_MS", 600, 100, 1200
            ),
            speculative_ambiguous_endpoint_ms=_env_int(
                "PHONE_AGENT_SPECULATIVE_AMBIGUOUS_ENDPOINT_MS", 900, 150, 1800
            ),
            speculative_incomplete_endpoint_ms=_env_int(
                "PHONE_AGENT_SPECULATIVE_INCOMPLETE_ENDPOINT_MS", 3000, 200, 3000
            ),
            speculative_commit_wait_ms=_env_int(
                "PHONE_AGENT_SPECULATIVE_COMMIT_WAIT_MS", 100, 0, 500
            ),
            smart_turn_enabled=_env_bool("PHONE_AGENT_SMART_TURN_ENABLED", True),
            smart_turn_model_path=os.getenv("PHONE_AGENT_SMART_TURN_MODEL_PATH", "").strip(),
            smart_turn_completion_threshold=_env_float(
                "PHONE_AGENT_SMART_TURN_COMPLETION_THRESHOLD", 0.5, 0.0, 1.0
            ),
            conversation_repair_enabled=_env_bool("PHONE_AGENT_CONVERSATION_REPAIR", True),
            conversational_reflex_enabled=conversational_reflex_enabled,
            conversational_reflex_cooldown_ms=_env_int(
                "PHONE_AGENT_CONVERSATIONAL_REFLEX_COOLDOWN_MS", 8000, 0, 60000
            ),
            llm_provider=llm_provider,
            llm_model=llm_model,
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip(),
            ollama_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE", "-1").strip(),
            ollama_prewarm=_env_bool("PHONE_AGENT_OLLAMA_PREWARM", True),
            ollama_think=_env_bool("PHONE_AGENT_OLLAMA_THINK", False),
            ollama_temperature=_env_float("PHONE_AGENT_OLLAMA_TEMPERATURE", 0.7, 0.0, 2.0),
            ollama_top_p=_env_float("PHONE_AGENT_OLLAMA_TOP_P", 0.8, 0.0, 1.0),
            ollama_top_k=_env_int("PHONE_AGENT_OLLAMA_TOP_K", 20, 0, 1000),
            ollama_min_p=_env_float("PHONE_AGENT_OLLAMA_MIN_P", 0.0, 0.0, 1.0),
            ollama_presence_penalty=_env_float(
                "PHONE_AGENT_OLLAMA_PRESENCE_PENALTY", 0.0, -2.0, 2.0
            ),
            ollama_num_predict=_env_int("PHONE_AGENT_OLLAMA_NUM_PREDICT", 192, 16, 4096),
            ollama_num_ctx=_env_int("PHONE_AGENT_OLLAMA_NUM_CTX", 16384, 2048, 131072),
            ollama_turn_timeout_secs=_env_int("PHONE_AGENT_OLLAMA_TURN_TIMEOUT_SECS", 30, 2, 300),
            openrouter_base_url=os.getenv(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ).strip(),
            codex_binary=os.getenv("CODEX_APP_SERVER_BINARY", "").strip(),
            codex_reasoning_effort=os.getenv("CODEX_REASONING_EFFORT", "low").strip().lower(),
            codex_turn_timeout_secs=_env_int("CODEX_TURN_TIMEOUT_SECS", 30, 5, 120),
            gemini_cli_binary=os.getenv("GEMINI_CLI_BINARY", "").strip(),
            gemini_cli_turn_timeout_secs=_env_int("GEMINI_CLI_TURN_TIMEOUT_SECS", 30, 5, 120),
            tts_provider=tts_provider,
            tts_model=tts_model,
            tts_voice_id=os.getenv("PHONE_AGENT_TTS_VOICE", "").strip()
            or os.getenv("CARTESIA_VOICE_ID", "").strip()
            or TTS_VOICE_DEFAULTS.get(tts_provider, "af_heart"),
            tts_aggregation=os.getenv(
                "PHONE_AGENT_TTS_AGGREGATION",
                {"edge_tts": "phrase", "kokoro": "sentence"}.get(tts_provider, "sentence"),
            )
            .strip()
            .lower(),
            tts_max_buffer_delay_ms=_env_int("PHONE_AGENT_TTS_MAX_BUFFER_DELAY_MS", 80, 0, 5000),
            google_tts_scene=os.getenv(
                "PHONE_AGENT_GOOGLE_TTS_SCENE", DEFAULT_GOOGLE_TTS_SCENE
            ).strip(),
            google_tts_sample_context=os.getenv(
                "PHONE_AGENT_GOOGLE_TTS_SAMPLE_CONTEXT", DEFAULT_GOOGLE_TTS_SAMPLE_CONTEXT
            ).strip(),
            edge_tts_rate=os.getenv("PHONE_AGENT_EDGE_TTS_RATE", "+0%").strip(),
            edge_tts_volume=os.getenv("PHONE_AGENT_EDGE_TTS_VOLUME", "+0%").strip(),
            edge_tts_pitch=os.getenv("PHONE_AGENT_EDGE_TTS_PITCH", "+0Hz").strip(),
            edge_tts_ffmpeg_binary=os.getenv(
                "PHONE_AGENT_EDGE_TTS_FFMPEG_BINARY", "ffmpeg"
            ).strip(),
            edge_tts_phrase_min_chars=_env_int("PHONE_AGENT_EDGE_TTS_PHRASE_MIN_CHARS", 8, 4, 120),
            edge_tts_phrase_max_chars=_env_int(
                "PHONE_AGENT_EDGE_TTS_PHRASE_MAX_CHARS", 60, 16, 240
            ),
            edge_tts_connect_timeout_secs=_env_int(
                "PHONE_AGENT_EDGE_TTS_CONNECT_TIMEOUT_SECS", 5, 1, 30
            ),
            edge_tts_receive_timeout_secs=_env_int(
                "PHONE_AGENT_EDGE_TTS_RECEIVE_TIMEOUT_SECS", 20, 2, 120
            ),
            supertonic_steps=_env_int(
                "PHONE_AGENT_SUPERTONIC_STEPS", supertonic_default_steps, 1, 100
            ),
            supertonic_speed=_env_float("PHONE_AGENT_SUPERTONIC_SPEED", 1.05, 0.7, 2.0),
            supertonic_intra_op_threads=_env_int(
                "PHONE_AGENT_SUPERTONIC_INTRA_OP_THREADS", 0, 0, 64
            ),
            supertonic_inter_op_threads=_env_int(
                "PHONE_AGENT_SUPERTONIC_INTER_OP_THREADS", 0, 0, 64
            ),
            supertonic_fallback_to_edge=supertonic_fallback_to_edge,
            vibevoice_ddpm_steps=_env_int("PHONE_AGENT_VIBEVOICE_DDPM_STEPS", 10, 1, 100),
            vibevoice_cfg_scale=_env_float("PHONE_AGENT_VIBEVOICE_CFG_SCALE", 1.3, 0.5, 5.0),
            vllm_base_url=os.getenv(
                "PHONE_AGENT_VLLM_BASE_URL", "http://127.0.0.1:8000/v1"
            ).strip(),
            lmstudio_base_url=os.getenv(
                "PHONE_AGENT_LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"
            ).strip(),
            pipeline_mode=pipeline_mode,
            call_channel=os.getenv("PHONE_AGENT_CALL_CHANNEL", "gsm").strip().lower(),
            whatsapp_country_code=os.getenv("PHONE_AGENT_WHATSAPP_COUNTRY", "212").strip(),
            whatsapp_max_duration_secs=_env_int("PHONE_AGENT_WHATSAPP_MAX_SECS", 900, 30, 3600),
        )
        config.validate(require_credentials=require_credentials)
        return config

    def validate(self, *, require_credentials: bool) -> None:
        validate_credential_refs(self.credential_refs)
        # Which channel carries the call is independent of how speech is
        # produced, so this is checked for cascade too. Nested under the
        # Realtime branch it silently accepted anything in cascade mode.
        if self.call_channel not in {"gsm", "whatsapp", "whatsapp_phone"}:
            raise ValueError(
                "call channel must be 'gsm', 'whatsapp' or 'whatsapp_phone', "
                f"got {self.call_channel!r}"
            )
        if self.pipeline_mode == "s2s_chatgpt_realtime":
            raise ConfigurationError(
                "PHONE_AGENT_PIPELINE_MODE 's2s_chatgpt_realtime' is deprecated and removed; "
                "please migrate to 'cascade'"
            )
        if self.pipeline_mode != "cascade":
            raise ConfigurationError(
                f"PHONE_AGENT_PIPELINE_MODE must be 'cascade', got {self.pipeline_mode!r}"
            )
        supported = {
            "stt": (
                self.stt_provider,
                {
                    "sensevoice",
                    "sensevoice_small",
                    "antigravity_live",
                    "deepgram_flux",
                    "parakeet_local",
                    "whisper_mlx",
                    "whisper_cuda",
                    "whisper_turbo",
                    "distil_whisper",
                    "whisper_local",
                },
            ),
            "llm": (
                self.llm_provider,
                {
                    "antigravity_gemini",
                    "codex_app",
                    "gemini",
                    "gemini_cli",
                    "ollama",
                    "openai",
                    "openrouter",
                    "vllm",
                    "lmstudio",
                },
            ),
            "tts": (
                self.tts_provider,
                {
                    "cartesia",
                    "edge_tts",
                    "google_genai",
                    "kokoro",
                    "supertonic",
                    "vibevoice",
                    "faster_qwen3",
                    "faster_qwen3_tts",
                    "qwen3_tts",
                },
            ),
        }
        for role, (selected, allowed) in supported.items():
            if selected not in allowed:
                raise ConfigurationError(
                    f"unsupported {role} provider {selected!r}; supported: {sorted(allowed)}"
                )
        if not self.stt_language.lower().startswith(("en", "fr")):
            raise ConfigurationError("PHONE_AGENT_STT_LANGUAGE must be an English or French locale")
        if self.tts_aggregation not in {"phrase", "sentence", "token"}:
            raise ConfigurationError(
                "PHONE_AGENT_TTS_AGGREGATION must be 'phrase', 'sentence', or 'token'"
            )
        if self.tts_aggregation == "phrase" and self.tts_provider != "edge_tts":
            raise ConfigurationError(
                "PHONE_AGENT_TTS_AGGREGATION=phrase is currently supported only by edge_tts"
            )
        if self.tts_provider == "google_genai" and self.tts_model not in {
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-flash-preview-tts",
        }:
            raise ConfigurationError(
                "PHONE_AGENT_TTS_MODEL must be gemini-3.1-flash-tts-preview or "
                "gemini-2.5-flash-preview-tts for Google Gemini TTS"
            )
        if len(self.google_tts_scene) > GOOGLE_TTS_CONTEXT_MAX_CHARS:
            raise ConfigurationError(
                f"PHONE_AGENT_GOOGLE_TTS_SCENE must be at most "
                f"{GOOGLE_TTS_CONTEXT_MAX_CHARS} characters"
            )
        if len(self.google_tts_sample_context) > GOOGLE_TTS_CONTEXT_MAX_CHARS:
            raise ConfigurationError(
                f"PHONE_AGENT_GOOGLE_TTS_SAMPLE_CONTEXT must be at most "
                f"{GOOGLE_TTS_CONTEXT_MAX_CHARS} characters"
            )
        if self.tts_provider == "supertonic":
            if self.tts_model not in {"supertonic-2", "supertonic-3"}:
                raise ConfigurationError(
                    "PHONE_AGENT_TTS_MODEL must be supertonic-2 or supertonic-3"
                )
            if not re.fullmatch(r"[MF][1-5]", self.tts_voice_id):
                raise ConfigurationError("PHONE_AGENT_TTS_VOICE must be M1-M5 or F1-F5")
        if self.tts_provider == "kokoro":
            # Kokoro runs on PyTorch with CUDA (or CPU fallback), with model repo
            # defaulting to hexgrad/Kokoro-82M while retaining backward-compatible aliases.
            valid_kokoro_models = {
                "hexgrad/Kokoro-82M",
                "kokoro-82m",
                "kokoro-bf16",
                "kokoro-4bit",
                "kokoro-v1.0",
            }
            if self.tts_model not in valid_kokoro_models and not self.tts_model.startswith(
                "hexgrad/"
            ):
                raise ConfigurationError(
                    "PHONE_AGENT_TTS_MODEL for kokoro must be hexgrad/Kokoro-82M or kokoro-82m"
                )
            # A Kokoro voice encodes its own language in the prefix, and the
            # phonemizer is driven separately by the call language. Mismatching
            # them produces an English voice reading French phonemes rather than
            # any visible error, so reject the combination up front.
            if not re.fullmatch(r"[abefhijpz][fm]_[a-z]+", self.tts_voice_id):
                raise ConfigurationError(
                    "PHONE_AGENT_TTS_VOICE for kokoro must be a voice id such as "
                    "af_heart or ff_siwis"
                )
            french_call = self.stt_language.lower().startswith("fr")
            french_voice = self.tts_voice_id.startswith("ff_")
            if french_call != french_voice:
                raise ConfigurationError(
                    "Kokoro voice and call language must match: use ff_siwis for a French "
                    f"call or an af_/am_/bf_/bm_ voice for English (language="
                    f"{self.stt_language!r}, voice={self.tts_voice_id!r})"
                )
        if self.tts_provider == "vibevoice" and not re.fullmatch(
            r"[a-z]{2}-[A-Za-z0-9_]+", self.tts_voice_id
        ):
            raise ConfigurationError(
                "PHONE_AGENT_TTS_VOICE for vibevoice must look like en-Emma_woman or fr-Spk0_man"
            )
        if not _EDGE_PERCENT_RE.fullmatch(self.edge_tts_rate):
            raise ConfigurationError("PHONE_AGENT_EDGE_TTS_RATE must look like +0% or -10%")
        if not _EDGE_PERCENT_RE.fullmatch(self.edge_tts_volume):
            raise ConfigurationError("PHONE_AGENT_EDGE_TTS_VOLUME must look like +0% or -10%")
        if not _EDGE_PITCH_RE.fullmatch(self.edge_tts_pitch):
            raise ConfigurationError("PHONE_AGENT_EDGE_TTS_PITCH must look like +0Hz or -10Hz")
        if not self.edge_tts_ffmpeg_binary:
            raise ConfigurationError("PHONE_AGENT_EDGE_TTS_FFMPEG_BINARY cannot be empty")
        if self.edge_tts_phrase_min_chars > self.edge_tts_phrase_max_chars:
            raise ConfigurationError(
                "PHONE_AGENT_EDGE_TTS_PHRASE_MIN_CHARS cannot exceed "
                "PHONE_AGENT_EDGE_TTS_PHRASE_MAX_CHARS"
            )
        if self.parakeet_incomplete_endpoint_ms < self.parakeet_endpoint_ms:
            raise ConfigurationError(
                "PHONE_AGENT_PARAKEET_INCOMPLETE_ENDPOINT_MS cannot be lower than "
                "PHONE_AGENT_PARAKEET_ENDPOINT_MS"
            )
        if not isinstance(self.antigravity_live_local_corroboration, bool):  # pyright: ignore[reportUnnecessaryIsInstance] -- Untyped stored/API data also constructs this dataclass.
            raise ConfigurationError("Local speech corroboration must be true or false")
        if self.antigravity_live_local_corroboration:
            try:
                feature_flag_enabled("PHONE_AGENT_ANTIGRAVITY_LOCAL_CORROBORATION", default=False,
                                     environment={"PHONE_AGENT_ANTIGRAVITY_LOCAL_CORROBORATION": "true"})
            except FeatureFlagError as exc:
                raise ConfigurationError(str(exc)) from exc
        if self.antigravity_live_incomplete_endpoint_ms < self.antigravity_live_endpoint_ms:
            raise ConfigurationError(
                "PHONE_AGENT_ANTIGRAVITY_INCOMPLETE_ENDPOINT_MS cannot be lower than "
                "PHONE_AGENT_ANTIGRAVITY_ENDPOINT_MS"
            )
        if self.antigravity_live_fallback_endpoint_ms < self.antigravity_live_endpoint_ms:
            raise ConfigurationError(
                "PHONE_AGENT_ANTIGRAVITY_FALLBACK_ENDPOINT_MS cannot be lower than "
                "PHONE_AGENT_ANTIGRAVITY_ENDPOINT_MS"
            )
        if not 0.0 < self.smart_turn_completion_threshold <= 1.0:
            raise ConfigurationError(
                "PHONE_AGENT_SMART_TURN_COMPLETION_THRESHOLD must be greater than 0 "
                "and at most 1"
            )
        if self.speculative_ambiguous_endpoint_ms < self.speculative_fast_endpoint_ms:
            raise ConfigurationError(
                "PHONE_AGENT_SPECULATIVE_AMBIGUOUS_ENDPOINT_MS cannot be lower than "
                "PHONE_AGENT_SPECULATIVE_FAST_ENDPOINT_MS"
            )
        if self.speculative_incomplete_endpoint_ms < self.speculative_ambiguous_endpoint_ms:
            raise ConfigurationError(
                "PHONE_AGENT_SPECULATIVE_INCOMPLETE_ENDPOINT_MS cannot be lower than "
                "PHONE_AGENT_SPECULATIVE_AMBIGUOUS_ENDPOINT_MS"
            )
        if self.codex_reasoning_effort not in {"low", "medium", "high", "xhigh"}:
            raise ConfigurationError("CODEX_REASONING_EFFORT must be low, medium, high, or xhigh")
        if not self.ollama_keep_alive or len(self.ollama_keep_alive) > 32:
            raise ConfigurationError("OLLAMA_KEEP_ALIVE must be 1 to 32 characters")
        if not require_credentials:
            return
        missing: list[str] = []
        if self.stt_provider == "deepgram_flux" and not self.deepgram_api_key:
            missing.append("DEEPGRAM_API_KEY")
        if self.llm_provider == "openai" and not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if self.llm_provider == "openrouter" and not self.openrouter_api_key:
            missing.append("OPENROUTER_API_KEY")
        if self.llm_provider == "gemini" and not self.google_api_key:
            missing.append("GOOGLE_API_KEY")
        if self.tts_provider == "cartesia" and not self.cartesia_api_key:
            missing.append("CARTESIA_API_KEY")
        if self.tts_provider == "cartesia" and not self.tts_voice_id:
            missing.append("CARTESIA_VOICE_ID")
        # Google Gemini TTS is deliberately absent from this list. It is the only
        # provider with a working free substitute, and treating its missing key
        # as fatal killed the voice host on every restart instead of placing the
        # call. create_provider_services falls back to the Edge voice and says so.
        if missing:
            raise ConfigurationError(
                f"missing production provider configuration: {', '.join(missing)}"
            )


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    device_id: str | None
    control_host: str
    control_port: int
    protocol_control_port: int
    rx_port: int
    tx_port: int
    sample_rate: int
    frame_ms: int
    input_queue_frames: int
    auto_answer: bool
    memory_enabled: bool
    task_id: str
    event_stream_enabled: bool
    voice_lock_path: Path
    system_prompt: str
    link_authentication_key: bytes | None = field(repr=False)
    providers: ProviderConfig = field(repr=False)
    # False once a handset tunnels in: the relay owns the gateway ports
    # and an adb forward would fight it for them. Defaulted so every existing
    # construction keeps the cabled behaviour it had.
    use_adb_forward: bool = True

    def __post_init__(self) -> None:
        if self.sample_rate != 16000 or self.frame_ms != 20:
            raise ConfigurationError("The qualified phone transport requires 16000 Hz audio and 20 ms frames")
        if not 2 <= self.input_queue_frames <= 50:
            raise ConfigurationError("Phone input queue must contain between 2 and 50 frames")

    def host_session_state(self) -> dict[str, Any]:
        """Report parsed host requirements without exposing pairing material or private paths."""
        return {
            "device_id_sha256": hashlib.sha256(self.device_id.encode()).hexdigest() if self.device_id else "",
            "control_host": self.control_host, "control_port": self.control_port,
            "protocol_control_port": self.protocol_control_port, "rx_port": self.rx_port, "tx_port": self.tx_port,
            "sample_rate": self.sample_rate, "frame_ms": self.frame_ms,
            "input_queue_frames": self.input_queue_frames,
            "event_stream_enabled": self.event_stream_enabled,
            "voice_lock_path_sha256": hashlib.sha256(str(self.voice_lock_path.resolve()).encode()).hexdigest(),
            "link_key_sha256": hashlib.sha256(self.link_authentication_key).hexdigest() if self.link_authentication_key else "",
            "use_adb_forward": self.use_adb_forward,
        }

    @classmethod
    def from_env(cls, *, require_provider_credentials: bool = True) -> RuntimeConfig:
        return cls._from_session_environment(os.environ, require_provider_credentials=require_provider_credentials)

    @classmethod
    def from_session_environment(cls, environment: Mapping[str, str], *, providers: ProviderConfig) -> RuntimeConfig:
        return cls._from_session_environment(environment, providers=providers, require_provider_credentials=False)

    @classmethod
    def _from_session_environment(cls, environment: Mapping[str, str], *, require_provider_credentials: bool,
                                  providers: ProviderConfig | None = None) -> RuntimeConfig:
        raw_key = environment.get("PHONE_AGENT_LINK_KEY_BASE64", "").strip()
        key_path = environment.get("PHONE_AGENT_LINK_KEY_FILE", "").strip()
        if not raw_key and not key_path:
            default_key = Path.home() / ".config" / "phone-agent" / "link.key"
            if default_key.is_file():
                key_path = str(default_key)
        link_key: bytes | None = None
        if raw_key and key_path:
            raise ConfigurationError(
                "configure only one of PHONE_AGENT_LINK_KEY_BASE64 or PHONE_AGENT_LINK_KEY_FILE"
            )
        if raw_key:
            if len(raw_key) > 8192:
                raise ConfigurationError("PHONE_AGENT_LINK_KEY_BASE64 exceeds its size bound")
            try:
                link_key = base64.b64decode(raw_key, validate=True)
            except ValueError as exc:
                raise ConfigurationError("PHONE_AGENT_LINK_KEY_BASE64 is invalid") from exc
            if not 32 <= len(link_key) <= 4096:
                raise ConfigurationError("PHONE_AGENT_LINK_KEY_BASE64 must decode to between 32 and 4096 bytes")
        elif key_path:
            try:
                link_key = Path(key_path).expanduser().read_bytes()
            except OSError as exc:
                raise ConfigurationError("PHONE_AGENT_LINK_KEY_FILE could not be read") from exc
            if not 32 <= len(link_key) <= 4096:
                raise ConfigurationError(
                    "PHONE_AGENT_LINK_KEY_FILE must contain between 32 and 4096 bytes"
                )

        return cls(
            device_id=environment.get("PHONE_AGENT_DEVICE_ID") or None,
            control_host=environment.get("PHONE_AGENT_CONTROL_HOST", "127.0.0.1").strip(),
            use_adb_forward=_env_bool("PHONE_AGENT_USE_ADB_FORWARD", True, environment=environment),
            control_port=_env_int("PHONE_AGENT_CONTROL_PORT", 8765, 1, 65535, environment=environment),
            protocol_control_port=_env_int("PHONE_AGENT_PROTOCOL_CONTROL_PORT", 8768, 1, 65535, environment=environment),
            rx_port=_env_int("PHONE_AGENT_RX_PORT", 8766, 1, 65535, environment=environment),
            tx_port=_env_int("PHONE_AGENT_TX_PORT", 8767, 1, 65535, environment=environment),
            sample_rate=_env_int("PHONE_AGENT_SAMPLE_RATE", 16_000, 8_000, 48_000, environment=environment),
            frame_ms=_env_int("PHONE_AGENT_FRAME_MS", 20, 10, 40, environment=environment),
            input_queue_frames=_env_int("PHONE_AGENT_INPUT_QUEUE_FRAMES", 25, 2, 50, environment=environment),
            auto_answer=_env_bool("PHONE_AGENT_AUTO_ANSWER", False, environment=environment),
            memory_enabled=_env_bool("PHONE_AGENT_MEMORY_ENABLED", True, environment=environment),
            task_id=environment.get("PHONE_AGENT_TASK_ID", "general_conversation").strip(),
            event_stream_enabled=_env_bool("PHONE_AGENT_EVENT_STREAM", False, environment=environment),
            voice_lock_path=Path(
                environment.get(
                    "PHONE_AGENT_VOICE_LOCK_PATH",
                    str(Path.home() / ".local" / "share" / "phone-agent" / "voice-host.lock"),
                )
            ).expanduser(),
            system_prompt=environment.get(
                "PHONE_AGENT_SYSTEM_PROMPT",
                (
                    "You are a helpful and polite AI voice assistant on a telephone call. "
                    "Speak only English or French. Use English by default and switch to French "
                    "when the caller requests it or speaks a complete French sentence. Keep your "
                    "responses concise, warm, and conversational (1 to 2 short spoken sentences). "
                    "Never use markdown, emojis, asterisks, or bullet points."
                ),
            ).strip(),
            link_authentication_key=link_key,
            providers=providers if providers is not None else ProviderConfig.from_env(require_credentials=require_provider_credentials),
        )

    @property
    def pipeline_mode(self) -> str:
        return self.providers.pipeline_mode

    @property
    def call_channel(self) -> str:
        return self.providers.call_channel

    @property
    def whatsapp_country_code(self) -> str:
        return self.providers.whatsapp_country_code

    @property
    def whatsapp_max_duration_secs(self) -> int:
        return self.providers.whatsapp_max_duration_secs
