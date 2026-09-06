"""Provider references resolved privately inside the local runtime."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

_DEFAULTS = {
    "deepgram_api_key": ("DEEPGRAM_API_KEY",),
    "openai_api_key": ("OPENAI_API_KEY",),
    "openrouter_api_key": ("OPENROUTER_API_KEY",),
    "vllm_api_key": ("VLLM_API_KEY",),
    "lmstudio_api_key": ("LMSTUDIO_API_KEY",),
    "google_api_key": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "cartesia_api_key": ("CARTESIA_API_KEY",),
}
_STANDARD_ENV = frozenset(name for names in _DEFAULTS.values() for name in names)
_REF_PATTERN = re.compile(r"env:(?:" + "|".join(sorted(_STANDARD_ENV)) + r"|PHONE_AGENT_SECRET_[A-Z0-9_]{1,100})")


def default_credential_refs() -> dict[str, list[str]]:
    return {field: ["env:" + name for name in names] for field, names in _DEFAULTS.items()}


def credential_refs_schema() -> dict[str, Any]:
    return {
        "description": "Ordered provider credential references; an empty list disables that credential. Values resolve privately on this host.",
        "required": list(_DEFAULTS), "additionalProperties": False,
        "properties": {field: {"type": "array", "maxItems": 3, "uniqueItems": True,
                               "items": {"type": "string", "pattern": "^env:(?:" + "|".join(names) + r"|PHONE_AGENT_SECRET_[A-Z0-9_]{1,100})$"}}
                       for field, names in _DEFAULTS.items()},
        "examples": [default_credential_refs()],
    }


def validate_credential_refs(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict) or set(value) != set(_DEFAULTS):
        raise ValueError("Credential references must specify the seven provider credential fields")
    result: dict[str, list[str]] = {}
    for field, refs in value.items():
        if not isinstance(refs, list) or len(refs) > 3:
            raise ValueError("Each provider credential accepts zero to three ordered environment references")
        checked: list[str] = []
        for ref in refs:
            if not isinstance(ref, str) or not _REF_PATTERN.fullmatch(ref):
                raise ValueError("Credentials accept environment references, never raw secret values")
            if ref[4:] in _STANDARD_ENV and ref[4:] not in _DEFAULTS[field]:
                raise ValueError("Standard credential reference belongs to a different provider")
            if ref in checked:
                raise ValueError("Duplicate credential references are not allowed")
            checked.append(ref)
        result[field] = checked
    return result


def credential_refs_from_env(environment: Mapping[str, str] | None = None) -> dict[str, list[str]]:
    env = os.environ if environment is None else environment
    raw = env.get("PHONE_AGENT_PROVIDER_CREDENTIAL_REFS")
    if raw is None:
        return default_credential_refs()
    try:
        if len(raw) > 4096:
            raise ValueError("oversized")
        return validate_credential_refs(json.loads(raw))
    except (ValueError, TypeError):
        raise ValueError("Provider credential references are invalid") from None


def resolve_provider_credentials(
    references: dict[str, list[str]], environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ if environment is None else environment
    refs = validate_credential_refs(references)
    return {field: next((env[ref[4:]].strip() for ref in names if env.get(ref[4:], "").strip()), "")
            for field, names in refs.items()}


def required_provider_credentials(config: Any) -> set[str]:
    required: set[str] = set()
    for provider, mapping in (
        (config.stt_provider, {"deepgram_flux": "deepgram_api_key"}),
        (config.llm_provider, {"openai": "openai_api_key", "openrouter": "openrouter_api_key", "gemini": "google_api_key"}),
        (config.tts_provider, {"cartesia": "cartesia_api_key", "google_genai": "google_api_key"}),
    ):
        if provider in mapping:
            required.add(mapping[provider])
    return required
