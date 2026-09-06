"""Continuous caller speech must not lose VAD state to a wall-clock reset."""
from __future__ import annotations

import numpy as np
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

from phone_agent_gateway.ai_bridge.streaming_silero_vad import StreamingSileroVADAnalyzer


class Model:
    def __init__(self):
        self.resets = 0
        self.calls = 0
    def __call__(self, samples, rate):
        assert samples.shape==(512,) and rate==16000
        self.calls += 1
        return np.array([[0.9]], dtype=np.float32)
    def reset_states(self):
        self.resets += 1


def analyzer():
    vad = StreamingSileroVADAnalyzer.__new__(StreamingSileroVADAnalyzer)
    vad._model = Model()
    vad._sample_rate = 16000
    vad._num_channels = 1
    vad._params = VADParams(confidence=0.7,start_secs=0.12,stop_secs=0.2,min_volume=0)
    vad._last_reset_time = 0  # Always expired under the old wall-clock policy.
    vad._prev_volume = 0.7
    vad._vad_buffer = b'old partial frame'
    return vad


def test_elapsed_clock_does_not_reset_active_silero_state():
    vad = analyzer()
    for _ in range(500):
        assert vad.voice_confidence(bytes(1024)) > 0.7
    assert vad._model.calls==500
    assert vad._model.resets==0


def test_session_reset_clears_model_buffer_and_detector_state():
    vad = analyzer()
    vad._vad_state = VADState.SPEAKING
    vad.reset_stream()
    assert vad._model.resets==1
    assert vad._vad_buffer==b''
    assert vad._prev_volume==0
    assert vad._vad_state is VADState.QUIET
    assert vad._vad_starting_count==vad._vad_stopping_count==0


def test_invalid_model_probability_cannot_create_speech():
    vad = analyzer()
    vad._model = lambda *_: np.array([[float('nan')]])
    assert vad.voice_confidence(bytes(1024))==0
