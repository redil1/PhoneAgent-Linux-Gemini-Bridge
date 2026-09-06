"""Apple GPU Whisper decoding with the shared acoustic confidence gate.

All construction and inference runs on the local STT executor. MLX's internal
model cache and stream are then owned by the same thread throughout a call.
"""

from __future__ import annotations

import importlib
import platform
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from .parakeet_local_stt import STTHypothesis, evaluate_whisper_segments

DEFAULT_MLX_WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo-q4"


class MLXWhisperDecoder:
    backend_kind = "mlx-whisper"

    def __init__(self, model_id: str = DEFAULT_MLX_WHISPER_MODEL) -> None:
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise RuntimeError(
                "whisper_mlx requires Apple Silicon; select whisper_turbo for CPU/CUDA"
            )
        try:
            import mlx.core as mx
            mlx_whisper = importlib.import_module("mlx_whisper")
        except ImportError as exc:
            raise RuntimeError(
                "Install the local-stt-mlx dependency extra to use whisper_mlx"
            ) from exc
        self._mx = mx
        self._transcribe = cast(Callable[..., dict[str, Any]], mlx_whisper.transcribe)
        self.model_id = model_id

    def _run(self, samples: NDArray[np.float32], language: str | None, initial_prompt: str | None = None) -> dict[str, Any]:
        result = self._transcribe(
            samples,
            path_or_hf_repo=self.model_id,
            language=language,
            temperature=0.0,
            condition_on_previous_text=False,
            compression_ratio_threshold=2.2,
            logprob_threshold=-1.0,
            no_speech_threshold=0.6,
            word_timestamps=False,
            verbose=None,
            **({"initial_prompt": initial_prompt} if initial_prompt else {}),
        )
        self._mx.synchronize()
        return result

    def prewarm(self) -> None:
        # The normal public transcribe API loads and caches the model. Merely
        # importing a module must never be reported as ready for the first call.
        self._run(np.zeros(16000, dtype=np.float32), "en")

    def decode(self, samples: NDArray[np.float32], *, language: str, initial_prompt: str | None = None) -> STTHypothesis:
        code = (language or "auto").split("-", 1)[0].lower()
        selected_language = None if code == "auto" else code
        result = self._run(samples, selected_language, initial_prompt) if initial_prompt else self._run(samples, selected_language)
        segments = [SimpleNamespace(**segment) for segment in result.get("segments", [])]
        # MLX does not implement faster-whisper's beam-search retry. A second
        # deterministic greedy pass would repeat the same result, so doubtful
        # audio stays explicitly untrusted instead of buying latency for no gain.
        return evaluate_whisper_segments(
            segments,
            samples,
            language=result.get("language") or (None if code == "auto" else code),
            diagnostics={
                "engine": self.backend_kind,
                "model": self.model_id,
                "decoder": "greedy",
                "quality_retry": False,
                "vad": "external_turn_controller",
            },
        )
