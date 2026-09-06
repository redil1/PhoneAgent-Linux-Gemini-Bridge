# Installed Smart Turn v3.2 model: identity and offline CPU latency

Date: 2026-09-04. This is an inspection of the project's installed Python environment and a synthetic timing benchmark. It is **not an evaluation of French or English turn-taking accuracy**, and it does not establish production end-to-end response latency.

## Exact model identity

The installed `LocalSmartTurnAnalyzerV3` in Pipecat 1.7.0 defaults explicitly to `smart-turn-v3.2-cpu.onnx`. The class and log messages still use the broader v3 name; that does not mean this environment loads the earlier v3 artifact.

| Property | Observed value |
| --- | --- |
| Absolute path | `/Users/aziz/Desktop/phone-agent-linux/.venv/lib/python3.11/site-packages/pipecat/audio/turn/smart_turn/data/smart-turn-v3.2-cpu.onnx` |
| File size | 8,679,182 bytes (8.68 MB decimal; 8.28 MiB) |
| SHA-256 | `2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f` |
| ONNX producer | `onnx.quantize` |
| Input name/type/shape | `input_features`, float32 tensor, `[dynamic batch, 80, 800]` |
| Output name/type/shape | `logits`, float32 tensor, `[dynamic batch, 1]` |
| Active execution provider | `CPUExecutionProvider` |
| Intra-op / inter-op threads | 1 / 1 |
| Execution / optimization | Sequential / all graph optimizations enabled |

The hash matches the publisher hash independently obtained by the main investigation for the named Hugging Face artifact. The metadata has `onnx.quant.pre_process` and `onnx.infer` values of `onnxruntime.quant`. This inspection did not install `onnx` or enumerate graph operations.

## Runtime and preprocessing

The environment is Python 3.11.0 on macOS 26.5.1 arm64, with Pipecat 1.7.0, ONNX Runtime 1.24.4, NumPy 1.26.4, and soxr 1.0.0. Python reports 14 logical CPUs. The sandbox denied `sysctl` inspection of the exact Apple CPU model, so no exact chip identification is claimed.

ONNX Runtime exposes CoreML, Azure, and CPU providers, but this wrapper's actual session uses **CPU only**. Availability is different from activation.

Read-only inspection of the installed wrapper confirms:

- Audio is resampled to 16 kHz when its declared sample rate differs.
- The wrapper retains the final eight seconds; longer preceding audio is discarded.
- Shorter audio is padded with zeros **before** the waveform to reach eight seconds. Eight seconds is a maximum model context window, not a requirement to wait for eight seconds of speech.
- Audio is normalized and converted to Whisper-style 80-by-800 log-mel features.
- The wrapper treats the ONNX output as a completion probability, even though its output tensor is named `logits`, and reports complete only when the value is greater than 0.5.
- The model returns a completion classification and probability. It does not directly cancel assistant generation, revoke playback permission, manage a conversation transcript, or perform acoustic echo cancellation.

These properties are from the installed files, whose hashes are recorded alongside the model hash in `local_model_benchmark.json`.

## Measured CPU latency

The script loaded the exact installed wrapper with `sample_rate=16000` and `cpu_count=1`. Each warm case used three warm-up calls and 30 measured calls. The input was a deterministic amplitude-modulated 180 Hz tone, deliberately not speech. Initial inference used zeros. Audio logging was explicitly disabled. No customer recordings, calls, network requests, settings changes, or application code changes were involved.

| Measured operation | Median | p95 |
| --- | ---: | ---: |
| Wrapper, 1 second synthetic input | 64.2 ms | 80.1 ms |
| Wrapper, 4 seconds synthetic input | 65.8 ms | 78.1 ms |
| Wrapper, 8 seconds synthetic input | 65.3 ms | 83.2 ms |
| Wrapper, 12 seconds synthetic input | 63.6 ms | 77.6 ms |

The similar durations across input lengths are consistent with padding/truncation to a fixed eight-second feature window.

ONNX inference on cached features had a median of **48.7 ms**. Feature extraction alone had a median of **3.0 ms**. These were separate sequential timing phases, so their medians must not be subtracted from, or expected to sum exactly to, the wrapper medians. System load and scheduling were not controlled.

Three new Python subprocesses gave:

| Operation | Observed range |
| --- | ---: |
| Imports inside new process | 174.9–214.1 ms |
| Analyzer/session construction | 43.9–52.9 ms |
| First wrapper inference | 64.0–68.7 ms |

“Cold” here means a new Python process. It does not mean cleared filesystem caches or a reboot. Process creation itself is excluded from the import measurement. These results are from this Mac, not a Linux deployment or a loaded production host.

The wrapper latency includes preprocessing, ONNX execution, and output thresholding. It excludes VAD silence waiting, STT finalization, application queues, the LLM, TTS, transport, and playback. An approximately 65 ms classifier therefore does not imply that the assistant will answer in 65 ms, or that it will choose the correct moment to answer.

## What these measurements establish

1. This checkout already contains the exact v3.2 CPU artifact; obtaining a file with the same name and hash would not upgrade it.
2. It loads successfully on the installed CPU runtime and executes within roughly 64–66 ms at the median under this limited synthetic benchmark.
3. Its compute time is small compared with the hundreds of milliseconds of conversational pause discussed in the turn-management review, making it plausible to use the result in an online turn decision. That is a timing feasibility inference, not proof of conversational quality.
4. No French/English correctness rate, false interruption rate, hesitation robustness, accent quality, noise robustness, or “human quality” claim can be made from synthetic audio.
5. Being present and fast is different from having authority over the application's decision to play an answer. The companion integration investigation must establish how the application uses, overrides, and invalidates this result.

## Reproduce

From `/Users/aziz/Desktop/phone-agent-linux`:

```text
.venv/bin/python reports/quality/turn-taking-review/smart-turn-model/benchmark_local_model.py
```

The script writes only `local_model_benchmark.json` beside itself. It sets offline environment flags and disables Smart Turn audio logs. Raw timing samples, provider configuration, versions, file hashes, model metadata, and method limitations are retained in that JSON.

Repository status could not be obtained because this copied project directory has no Git metadata (`git status` returns “not a git repository”). The only files created by this benchmark subtask are its script, JSON results, and this report under `reports/quality/turn-taking-review/smart-turn-model/`.
