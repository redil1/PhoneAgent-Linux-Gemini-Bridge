"""Apple decoder selection and evidence handling without network or GPU work."""

from __future__ import annotations

import numpy as np
import pytest

from phone_agent_gateway.ai_bridge.mlx_whisper_decoder import (
    DEFAULT_MLX_WHISPER_MODEL,
    MLXWhisperDecoder,
)
from phone_agent_gateway.ai_bridge.parakeet_local_stt import evaluate_whisper_segments
from phone_agent_gateway.ai_bridge.production_pipeline import _local_whisper_model
from phone_agent_gateway.ai_bridge.runtime_config import ProviderConfig


def test_mlx_selection_cannot_silently_route_to_ctranslate_cpu():
    assert (
        _local_whisper_model(ProviderConfig(stt_provider="whisper_mlx", stt_model="large-v3-turbo"))
        == DEFAULT_MLX_WHISPER_MODEL
    )
    with pytest.raises(ValueError, match="MLX Whisper checkpoint"):
        _local_whisper_model(
            ProviderConfig(stt_provider="whisper_mlx", stt_model="Systran/whisper-large-v3")
        )
    assert _local_whisper_model(ProviderConfig(stt_provider="whisper_turbo")) == "large-v3-turbo"


@pytest.mark.parametrize("logprob,trusted", [(-0.25, True), (-1.4, False)])
def test_mlx_preserves_acoustic_evidence_without_unsupported_beam_retry(logprob, trusted):
    decoder = MLXWhisperDecoder.__new__(MLXWhisperDecoder)
    decoder.model_id = DEFAULT_MLX_WHISPER_MODEL
    calls = []

    def run(samples, language):
        calls.append(language)
        return {
            "language": "fr",
            "segments": [
                {
                    "text": "Je ne sais pas.",
                    "start": 0,
                    "end": 1,
                    "avg_logprob": logprob,
                    "no_speech_prob": 0.01,
                    "compression_ratio": 1.1,
                }
            ],
        }

    decoder._run = run
    result = decoder.decode(np.zeros(16000, dtype=np.float32), language="fr-FR")
    assert calls == ["fr"]
    assert result.text == "Je ne sais pas."
    assert result.trusted_for_task is trusted
    assert result.language == "fr"
    assert result.diagnostics["engine"] == "mlx-whisper"
    assert result.diagnostics["quality_retry"] is False


def test_empty_decoder_segments_are_untrusted_not_a_success_or_exception():
    result = evaluate_whisper_segments(
        [], np.zeros(16000, dtype=np.float32), language="en", diagnostics={"engine": "test"}
    )
    assert not result.text and not result.trusted_for_task


def test_mlx_refuses_unsupported_platform_before_importing_the_gpu_runtime(monkeypatch):
    from phone_agent_gateway.ai_bridge import mlx_whisper_decoder

    monkeypatch.setattr(mlx_whisper_decoder.platform, "system", lambda: "Linux")
    with pytest.raises(RuntimeError, match="Apple Silicon"):
        MLXWhisperDecoder()
