# Can smart-turn-v3.2-cpu.onnx solve PhoneAgent turn-taking?

Investigation date: 4 September 2026. Scope: exact bundled model, primary-source evaluations, local CPU execution, and the active Antigravity integration design.

**Decision:** Retain this model and make its integration reliable. It is an appropriate acoustic turn-completion component for English and French. It cannot independently or definitively eliminate premature replies. The application already constructs this exact model, and several observed failures occur outside its inference function.

**Exact artifact verified**

The installed Pipecat 1.7.0 wrapper defaults to `smart-turn-v3.2-cpu.onnx`. Its local file has 8,679,182 bytes and SHA-256 `2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f`, exactly matching the [publisher's artifact](https://huggingface.co/pipecat-ai/smart-turn-v3/blob/main/smart-turn-v3.2-cpu.onnx). Substituting another download of that file would not change the model.

The model card describes an approximately eight-million-parameter Whisper Tiny encoder with a classifier, available in quantized CPU and unquantized variants. It classifies audio rather than reading the application's dialogue or task state. [Publisher model card](https://huggingface.co/pipecat-ai/smart-turn-v3).

The supported input is mono 16 kHz audio. The wrapper keeps the last eight seconds, left-pads shorter input, generates log-mel features, and predicts completion. Eight seconds is a maximum context window, not a required waiting time. The publisher recommends VAD-triggered inference over the current utterance, including resumed material; tiny isolated fragments are a weaker input. English and French are among the supported languages. [Model repository](https://github.com/pipecat-ai/smart-turn).

Version 3.2 specifically improved short-utterance handling, fixed a training padding issue, and added realistic background-noise augmentation. The release's “40%” claim means fewer short-utterance mistakes relative to the prior result, not 40 percentage points or perfect detection. [Release announcement](https://www.daily.co/blog/smart-turn-v3-2-handling-noisy-environments-and-short-responses/).

**Published accuracy is useful but imperfect**

The publisher's 7 January 2026 CPU benchmark reports:

| Evaluation group | Clips | Accuracy |
|---|---:|---:|
| Overall | 31,527 | 92.63% |
| English | 7,820 | 94.26% |
| French | 1,252 | 94.09% |
| `human_convcollector_1` subset | 90 | 86.67% |

These are labelled clip-classification results, not end-to-end call success or interruption rates. The small conversation subset illustrates variation without establishing general live-call performance. [CPU benchmark](https://huggingface.co/pipecat-ai/smart-turn-v3/blob/main/benchmarks/smart-turn-v3.2-cpu.md).

The test dataset includes both synthetic and human audio. Its labels do not establish accuracy for this handset, caller accents, French/English code-switching, background conditions, or repeated inference within a live turn. Those require a deployment-specific evaluation. [Dataset](https://huggingface.co/datasets/pipecat-ai/smart-turn-data-v3.2-test).

A metric interpretation detail matters: the benchmark code calculates its columns called FPR/FNR as false-positive/false-negative counts divided by **all samples**, rather than the standard class-conditional denominators. They must not be presented as the percentage of unfinished utterances that get cut off. The code classifies scores greater than 0.5 as complete. [Metric implementation](https://github.com/pipecat-ai/smart-turn/blob/main/benchmark.py#L155-L173).

**Actual CPU cost on this Mac**

The offline benchmark loaded the installed model using ONNX Runtime 1.24.4 on macOS arm64, CPUExecutionProvider, one inference thread. Each warm input case had three warmups and 30 measured runs.

| Measurement | Observed value |
|---|---|
| Full wrapper, across 1/4/8/12-second synthetic input cases | Median 63.6–65.8 ms |
| Same cases, p95 | 77.6–83.2 ms |
| ONNX execution on cached features | Median 48.7 ms |
| Construction in three fresh Python processes | 44–53 ms |
| First prediction in those processes | 64–69 ms |

This is small enough to make local inference a practical component of the turn decision. It excludes VAD waiting, ASR, task queues, LLM, TTS, and actual phone playout. Generated tones and zeros were used only to measure execution cost; no accuracy claim follows from them. The machine was not isolated from other workloads. Separate phase measurements should not be added together as though taken from the same run.

See [benchmark findings](/Users/aziz/Desktop/phone-agent-linux/reports/quality/turn-taking-review/smart-turn-model/local-model-findings.md), [raw measurements](/Users/aziz/Desktop/phone-agent-linux/reports/quality/turn-taking-review/smart-turn-model/local_model_benchmark.json), and [reproducible script](/Users/aziz/Desktop/phone-agent-linux/reports/quality/turn-taking-review/smart-turn-model/benchmark_local_model.py).

**What the current application does with this model**

The active provider is configured as externally owning turn start, turn end and interruption. Pipecat's aggregator VAD is disabled on that path. Antigravity loads the model but calls its private prediction function from a custom watchdog. That private call is not inherently invalid inference: it still applies the installed model's feature conversion and window preparation. It bypasses the surrounding Pipecat turn lifecycle, which the application must then implement correctly.

| Concern | Current integration consequence |
|---|---|
| Short responses | “Oui” and “yes” bypass Smart Turn analysis, so this model cannot protect them from premature commitment |
| Incomplete decision | Normally extends a timer to 1.4 seconds; the timer can still commit without a new complete verdict |
| Inference pending | Can stop blocking after one second |
| Inference exception | Returns a synthetic complete score of 1.0 |
| Turn identity | An epoch check exists, but it does not comprehensively protect transcript commitment and audio release |
| Speech resumes | Earlier offline probes reproduced stale commitment and missed cancellation of an answer that starts afterward |
| Input ordering | Earlier probe reproduced synthetic silence queued ahead of captured audio |
| Audio availability | Model invocation requires a transcript, so it is not an independent speech-onset mechanism |

Evidence: [model setup](/Users/aziz/Desktop/phone-agent-linux/ai_bridge/antigravity_live_stt.py:328), [inference and deadline selection](/Users/aziz/Desktop/phone-agent-linux/ai_bridge/antigravity_live_stt.py:749), [watchdog](/Users/aziz/Desktop/phone-agent-linux/ai_bridge/antigravity_live_stt.py:887), and [external ownership](/Users/aziz/Desktop/phone-agent-linux/ai_bridge/production_pipeline.py:965). Earlier runnable reproductions are linked in the [conversation review](/Users/aziz/Desktop/phone-agent-linux/reports/quality/turn-taking-review/conversation-quality-review.md).

**A qualification to the previous review:** bounded silence fallback is not unique to this project. Installed Pipecat's standard Smart Turn lifecycle also eventually completes after a default three seconds of received non-speech. It normally predicts at VAD stop, preserves incomplete utterance context, and can predict again after resumed speech stops. It does not continuously rerun the model throughout the same silent pause. A local mocked-predictor probe confirmed that lifecycle. Using the standard implementation therefore does not create a perfection guarantee either; the value is coherent ownership, buffering, and transcript-readiness coordination.

There is a latent sample-rate initialization omission in the custom path: Pipecat's constructor stores the requested rate, while `set_sample_rate` activates it. The standard strategy calls this on StartFrame; the custom path does not. The current 16 kHz phone route matches the predictor's fallback and is compatible. This is not evidence that current calls have a sample-rate mismatch.

**What the model can and cannot fix**

It can improve the judgement that a natural pause is still part of an utterance, using acoustic/linguistic cues that silence timing alone lacks. It can do that without waiting for a network round trip.

It cannot know that a caller will unexpectedly continue a complete-sounding idea; infer all omitted context beyond its audio window; decide every task-specific acknowledgement; detect speech onset by itself; repair missing/reordered media; cancel LLM/TTS work; or prevent stale queued audio from playing. A high score is a model estimate, not calibrated certainty about the caller's intentions.

**Recommended implementation and evidence gate**

1. Keep this verified model. Prewarm it and report actual model load/inference failures.
2. Choose one turn owner. Either migrate transcription and audio routing into one standard VAD + Smart Turn strategy, or implement equivalent lifecycle guarantees in a dedicated external controller. Do not add a second strategy alongside existing authoritative STT boundaries. Current STT suppresses audio passthrough, so a downstream analyzer also needs deliberate access to input audio without routing it into phone output.
3. Feed ordered, continuous caller audio with current-utterance context. Run analysis at a speech pause; invalidate the result when new speech arrives. Preserve the continuation before reanalysis.
4. Separate speculative computation from permission to speak. Recheck current input revision and caller activity before both final transcript commitment and first output audio. Cancel obsolete work even when the bot has not yet become audible.
5. Treat classifier failure as unknown. Tune completion threshold and bounded patience together on labelled recordings. A higher threshold can trade fewer early replies for more waiting; do not choose an arbitrary high number and assume it solves the problem.
6. Compare a fixed-silence baseline, the current implementation, and the corrected Smart Turn integration using held-out French and English telephone audio. Annotate true yielded turns, hesitations, short replies, continuations, interruptions, names/numbers and language switches. Measure early audible starts, delayed responses, missed short answers and interruption-to-playback-stop—not only classifier accuracy.

The recommendation is to use v3.2 as the acoustic completion judge inside a reliable turn controller. Model selection is already satisfied. Integration correctness and deployment-specific qualification are the remaining work. No application settings, model files, or call behavior were changed in this investigation.
