"""Silero inference whose recurrent state belongs to one continuous call.

Installed Pipecat1.7 resets its Silero model on a five-second wall-clock timer,
including during a word. Keep the same model and VAD thresholds, but let the
call/session lifecycle reset state. The model wrapper stores fixed-size state
and context arrays; it does not need to forget active speech periodically.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

import numpy as np
from numpy.typing import NDArray
from pipecat.audio.vad.silero import SileroVADAnalyzer

logger = logging.getLogger('PhoneAgentStreamingVAD')


class StreamingSileroVADAnalyzer(SileroVADAnalyzer):
    """Use Silero's normal PCM inference without a mid-stream timer reset."""

    def voice_confidence(self, buffer: bytes) -> float:
        try:
            samples = np.frombuffer(buffer, dtype=np.int16).astype(np.float32) / 32768.0
            model = cast(
                Callable[[NDArray[np.float32], int], NDArray[np.float32]],
                self._model,
            )
            probability = float(model(samples, self.sample_rate).reshape(-1)[0])
            if not np.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError('Invalid Silero probability')
            return probability
        except Exception as exc:
            logger.error('Silero inference failed: %s', type(exc).__name__)
            return 0.0

    def reset_stream(self) -> None:
        """Reset between sessions, never as a consequence of elapsed call time."""
        self._model.reset_states()
        self._vad_buffer = b''
        self._prev_volume = 0.0
        self.set_params(self.params)
