"""Measure the existing cached MLX alternative in its installed Python runtime.

No model downloads, microphone, speaker, network, calls, or configuration changes.
This uses different decoder settings from the confidence-gated CTranslate2 path.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"

import mlx.core as mx
import mlx_whisper
import numpy as np

ROOT = Path(__file__).resolve().parent
cache = Path.home() / ".cache/huggingface/hub/models--mlx-community--whisper-large-v3-turbo-q4/snapshots"
snapshots = sorted(cache.iterdir())
if len(snapshots) != 1:
    raise RuntimeError("Expected one already-cached model snapshot")
model = str(snapshots[0])
files = sorted((ROOT / "fixtures").glob("*.pcm"))
if not files:
    raise RuntimeError("Run the paired fixture generator first")

def transcribe(path):
    samples = np.frombuffer(path.read_bytes(), dtype=np.int16).astype(np.float32) / 32768
    result = mlx_whisper.transcribe(
        samples, path_or_hf_repo=model,
        language="fr" if path.name.startswith("fr_") else "en",
        temperature=0.0, condition_on_previous_text=False,
        word_timestamps=False, verbose=None,
    )
    mx.synchronize()
    return result

start = time.perf_counter()
transcribe(files[0])
data = {"platform": platform.platform(), "mlx_whisper_version": importlib.metadata.version("mlx-whisper"), "device": str(mx.default_device()), "model": model, "cold_load_and_first_inference_ms": (time.perf_counter()-start)*1000, "rows": []}
for trial in range(3):
    for path in files:
        started = time.perf_counter()
        result = transcribe(path)
        data["rows"].append({"trial": trial, "case": path.stem, "audio_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "elapsed_ms": (time.perf_counter()-started)*1000, "text": result["text"], "segments": [{k:s.get(k) for k in ("avg_logprob", "no_speech_prob", "compression_ratio")} for s in result.get("segments", [])]})
        (ROOT / "mlx-alternative-benchmark.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({"device": data["device"], "rows": len(data["rows"]), "output": str(ROOT / "mlx-alternative-benchmark.json")}))
