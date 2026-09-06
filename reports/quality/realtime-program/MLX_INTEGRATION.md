# Apple GPU STT integration and qualification evidence

2026-09-05. The voice-quality objective remains active. Gemini remains the selected live recognizer.

## Implementation

The previous `whisper_mlx` selection resolved to `large-v3-turbo` and then loaded Faster-Whisper/CTranslate2, which ran on this Mac's CPU. It did not represent the exploratory Apple GPU benchmark.

`whisper_mlx` now resolves to `mlx-community/whisper-large-v3-turbo-q4` and an actual `mlx-whisper` decoder. Model loading and inference share the owned local STT executor. Prewarm invokes the public transcription API, loading and compiling the model rather than reporting a successful import as readiness. Unsupported platforms or missing dependencies produce an explicit error; they do not silently fall back to CPU.

The decoder retains average log probability, no-speech probability, compression ratio and detected language through the same confidence gate used for Faster-Whisper. Empty segments remain untrusted. MLX does not implement the existing Faster-Whisper beam-search retry, so it is not labelled as doing one; repeating greedy decoding deterministically would add time without new evidence.

The MLX provider now uses `LocalNeuralSTTService`, which reuses the cloud adapter's Silero, Smart Turn, continuation rules, acoustic interruption and speech-floor control. It does not discover a cloud ASR bridge. Recognition runs off the event loop. A decoded snapshot is discarded if newer speech arrived, and only a result covering the current acoustic signature can commit. Local results replace the complete buffered hypothesis rather than being appended as continuous-provider segments. Decoder errors clear obsolete partial text. Audio buffers and owned tasks are bounded and cleaned up.

A 300 ms decode-start silence threshold avoids some wasted work during tiny pauses between words. LLM speculation remains independently controlled. The initial 120 ms local threshold caused four discarded decodes in an instrumented eight-turn replay; the 300 ms run had two. This reduced wasted work in that sample but did not establish a lower median end-to-end latency.

The UI now exposes an explicitly labelled Apple Silicon GPU option. It does not mark it as universally recommended or switch the saved provider.

## Packaging

The `local-stt-mlx` extra installs MLX Whisper 0.4.3 and Mac Torch 2.14.0. Linux retains the existing Torch 2.13.0+cu129 pin and explicit CUDA index. Platform sources and disjoint resolution environments prevent the optional Mac extra from inheriting a Linux-only wheel; all platforms remain covered by the marker partition.

A dry-run installation initially failed because the old CUDA Torch wheel had no macOS build. The corrected lock resolves separate platform variants and the Mac dry run passes. No `uv sync` removal operation was applied to the running environment; additive installation supplied the actual test dependencies. The tested MLX/MLX Metal version is 0.32.2. `uv lock --check` passes.

Primary documentation: [MLX streams](https://github.com/ml-explore/mlx/blob/main/docs/src/python/devices_and_streams.rst), [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), and [uv platform-specific PyTorch installation](https://github.com/astral-sh/uv/blob/main/docs/guides/integration/pytorch.md). Context7 documentation and the installed public transcription signature were checked before implementation.

## Measurements

All input was synthetic English/French telephone-bandlimited PCM. These tests used no microphone, speaker, phone call, real WhatsApp send or CRM write.

| Test | Result |
|---|---|
| Integrated decoder, six clips with specified language | median 608.7 ms; range 582.9–680.1 ms |
| Continuous MLX session, automatic language detection, initial run | median EOF-to-commit 1279.9 ms; range 871.5–1905.5 ms |
| Instrumented eight-turn run at 120 ms decode-start threshold | median EOF-to-commit 1360.3 ms; four discarded decodes |
| Eight-turn run at 300 ms decode-start threshold | median EOF-to-commit 1404.0 ms; range 1041.9–2979.2 ms; two discarded decodes |
| Full production pipeline, initial MLX replay | first simulated playout 4984.4 ms |
| Full replay with correction and playback completion verified | first simulated playout 3411.3 ms; barge-in-to-flush 154.3 ms |

The continuous MLX runs retained all expected words after ignoring case/punctuation, including French negation, repeated French confirmations, and continuations after 900 ms English / 1200 ms French pauses. The final continuous run still held “Yes, I can” for approximately three seconds following an incomplete Smart Turn verdict. That is a remaining quality/latency issue, not a successful fast-turn result.

The completed full-pipeline replay recorded the correction transcript, generated its answer and resolved playback accounting. The harness now requires those conditions; it no longer mistakes a second assistant message from a tool follow-up for completion of a second caller turn. Phone playout and acknowledgements are simulated.

**The integrated GPU decoder is much faster than this Mac's 6.63-second median confidence-gated CPU decoder, but these results do not establish a faster overall conversation than Gemini.** Automatic language detection, discarded early decodes, acoustic decisions, model output length, tools and cloud LLM latency all matter. The earlier approximately 609 ms exploratory decoder result cannot be presented as full conversational latency. Gemini remains selected pending a broader matched comparison.

## Validation and remaining work

- Focused local lifecycle/decoder/config tests: 46 passed.
- Full isolated software suite: 952 passed, 13 pre-existing failures, 39 skipped, one device test deselected.
- Changed-file Ruff, configured Pyright, explicit strict checking of the two new modules, Python compilation and Studio JavaScript syntax checks passed.
- Dependency lock check and Mac installation dry run passed.
- Frozen WhatsApp verification still fails on the two previously modified frozen files. This work did not change those files or the manifest.
- The first broader strict check also examined the legacy local recognizer and reported existing typing/stub problems there. The configured check and new modules pass; the entire legacy module is not being claimed as strictly typed.

Evidence: `mlx-integrated-decode.json`, `turn-sequences-mlx-first.json`, `turn-sequences-mlx-eager-decode.json`, `turn-sequences-mlx.json`, `pipeline-benchmark-mlx-initial.json`, and `pipeline-benchmark-mlx.json`. Activation and selected-provider read-back are recorded separately in `runtime-mlx-refresh-result.json` and `runtime-mlx-health.json`.

Other legacy local providers still use their older buffered/RMS controller. Their migration, broader noisy/echoing and continuous-call qualification, prompt/history optimization, physical output timing, final-goodbye trials and the pre-existing release failures remain outstanding. This is an integrated and tested alternative within the stated scope, not a universal human-quality guarantee.
