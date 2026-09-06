#!/usr/bin/env python3
"""Offline synthetic timing only; this does not evaluate turn-detection accuracy.

Run with the project's .venv/bin/python. No network, customer audio, or application
configuration is used. The bundled Pipecat model and exact installed wrapper are
loaded read-only; output is restricted to this script's directory.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["PIPECAT_SMART_TURN_LOG_DATA"] = "false"

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--cold-worker", action="store_true")
args = parser.parse_args()

import_start = time.perf_counter()
import numpy as np
import onnxruntime as ort
from pipecat.audio.turn.smart_turn._whisper_features import compute_whisper_log_mel_features
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

import_ms = (time.perf_counter() - import_start) * 1000


def elapsed_ms(fn):
    start = time.perf_counter()
    value = fn()
    return (time.perf_counter() - start) * 1000, value


def summarize(samples):
    return {
        "n": len(samples),
        "mean_ms": statistics.mean(samples),
        "median_ms": statistics.median(samples),
        "p95_ms": float(np.percentile(samples, 95)),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "samples_ms": samples,
    }


def times(fn, n=30, warmup=3):
    for _ in range(warmup):
        fn()
    return summarize([elapsed_ms(fn)[0] for _ in range(n)])


def signal(seconds):
    # An amplitude-modulated tone is deliberately not speech. Eight seconds is
    # the model's full window; shorter inputs are left-padded by the wrapper.
    t = np.arange(int(seconds * 16000), dtype=np.float32) / 16000
    return (
        0.1 * np.sin(2 * np.pi * 180 * t) * (0.55 + 0.45 * np.sin(2 * np.pi * 3 * t))
    ).astype(np.float32)


load_ms, analyzer = elapsed_ms(lambda: LocalSmartTurnAnalyzerV3(sample_rate=16000, cpu_count=1))
zeros = np.zeros(8 * 16000, dtype=np.float32)
first_inference_ms, first_result = elapsed_ms(lambda: analyzer._predict_endpoint(zeros))

if args.cold_worker:
    print(json.dumps({
        "import_ms": import_ms,
        "analyzer_constructor_ms": load_ms,
        "first_wrapper_inference_ms": first_inference_ms,
        "session_providers": analyzer._session.get_providers(),
        "prediction_returned": set(first_result) == {"prediction", "probability"},
    }))
    analyzer._executor.shutdown(wait=True)
    sys.exit(0)

package = Path(importlib.metadata.distribution("pipecat-ai").locate_file("pipecat"))
model_path = package / "audio/turn/smart_turn/data/smart-turn-v3.2-cpu.onnx"
wrapper_path = package / "audio/turn/smart_turn/local_smart_turn_v3.py"
features_path = package / "audio/turn/smart_turn/_whisper_features.py"
session = analyzer._session
meta = session.get_modelmeta()
options = session.get_session_options()

result = {
    "scope": "Offline synthetic CPU timing and read-only introspection; NOT an accuracy or conversation-quality evaluation.",
    "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "environment": {
        "executable": sys.executable,
        "python": sys.version,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "logical_cpu_count": os.cpu_count(),
        "exact_cpu_model": "Not inspected: macOS sysctl query denied by sandbox.",
        "versions": {name: importlib.metadata.version(name) for name in ("pipecat-ai", "onnxruntime", "numpy", "soxr")},
        "available_onnx_providers": ort.get_available_providers(),
        "active_session_providers": session.get_providers(),
        "session_provider_options": session.get_provider_options(),
        "session_settings": {
            "intra_op_num_threads": options.intra_op_num_threads,
            "inter_op_num_threads": options.inter_op_num_threads,
            "execution_mode": str(options.execution_mode),
            "graph_optimization_level": str(options.graph_optimization_level),
        },
    },
    "model": {
        "path": str(model_path),
        "bytes": model_path.stat().st_size,
        "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "inputs": [{"name": i.name, "type": i.type, "shape": i.shape} for i in session.get_inputs()],
        "outputs": [{"name": i.name, "type": i.type, "shape": i.shape} for i in session.get_outputs()],
        "metadata": {
            "producer_name": meta.producer_name,
            "graph_name": meta.graph_name,
            "domain": meta.domain,
            "description": meta.description,
            "graph_description": meta.graph_description,
            "version": meta.version,
            "custom_metadata_map": meta.custom_metadata_map,
        },
    },
    "implementation": {
        "wrapper_path": str(wrapper_path),
        "wrapper_sha256": hashlib.sha256(wrapper_path.read_bytes()).hexdigest(),
        "feature_extractor_path": str(features_path),
        "feature_extractor_sha256": hashlib.sha256(features_path.read_bytes()).hexdigest(),
        "model_sample_rate_hz": 16000,
        "retained_audio_window_seconds": 8,
        "short_input_padding": "zeros before audio",
        "feature_shape": [1, 80, 800],
        "wrapper_complete_threshold": "probability > 0.5",
        "audio_logs_enabled": analyzer._log_data,
    },
    "initial_current_process": {
        "import_ms": import_ms,
        "analyzer_constructor_ms": load_ms,
        "first_wrapper_inference_ms": first_inference_ms,
    },
    "methodology": {
        "input": "Deterministic non-speech amplitude-modulated 180 Hz tone; initial call uses 8 seconds of zeros.",
        "warmup_iterations_per_case": 3,
        "measured_iterations_per_case": 30,
        "cpu_threads": 1,
        "fresh_process_trials": 3,
        "cold_definition": "New Python subprocess; import, constructor, and first wrapper call are separate. Filesystem cache is not flushed.",
        "warm_wrapper_includes": "resampling check, padding/truncation, normalization, log-mel feature extraction, ONNX inference, output thresholding",
        "warm_session_includes": "session.run on cached [1,80,800] float32 features only",
        "excluded": "live media, VAD waiting, STT, application queues, LLM, TTS, network, playback, multilingual accuracy",
        "system_load": "Not isolated; other processes and scheduling can affect observed latency.",
    },
    "warm_wrapper": {},
}

for seconds in (1, 4, 8, 12):
    audio = signal(seconds)
    result["warm_wrapper"][f"synthetic_{seconds}s_at_16000hz"] = times(
        lambda audio=audio: analyzer._predict_endpoint(audio)
    )

audio = signal(8)
features = np.expand_dims(compute_whisper_log_mel_features(audio, do_normalize=True), 0)
result["warm_feature_extraction_8s"] = times(
    lambda: compute_whisper_log_mel_features(audio, do_normalize=True)
)
result["warm_session_compute"] = times(
    lambda: session.run(None, {"input_features": features})
)

result["fresh_process_trials"] = []
for _ in range(3):
    child = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--cold-worker"],
        check=True, capture_output=True, text=True, timeout=45,
        env=os.environ.copy(),
    )
    result["fresh_process_trials"].append(json.loads(child.stdout.strip().splitlines()[-1]))

for key in ("import_ms", "analyzer_constructor_ms", "first_wrapper_inference_ms"):
    result[f"fresh_process_{key}"] = summarize([v[key] for v in result["fresh_process_trials"]])

analyzer._executor.shutdown(wait=True)
gc.collect()
output = Path(__file__).resolve().with_name("local_model_benchmark.json")
output.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({
    "output": str(output),
    "model": result["model"],
    "active_session_providers": result["environment"]["active_session_providers"],
    "warm_wrapper_median_ms": {k: v["median_ms"] for k, v in result["warm_wrapper"].items()},
    "warm_wrapper_p95_ms": {k: v["p95_ms"] for k, v in result["warm_wrapper"].items()},
    "warm_feature_extraction_median_ms": result["warm_feature_extraction_8s"]["median_ms"],
    "warm_session_median_ms": result["warm_session_compute"]["median_ms"],
    "fresh_process_trials": result["fresh_process_trials"],
}, indent=2))
