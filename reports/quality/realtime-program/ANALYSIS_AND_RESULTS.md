# Voice latency investigation and measured repairs

Date: 2026-09-05. Checkout: `/Users/aziz/Desktop/phone-agent-linux`.

**The 11.9046-second WhatsApp reply was primarily an action-path delay, not a slow recognizer.** Normal replies also had a reproducible extra 500 ms in turn orchestration. Repairs are implemented and tested; universal human-level quality and physical telephone performance are not established.

## The supplied 00:47 call

The call used Gemini Live STT (`antigravity_live`). The later selection of local Whisper cannot explain its delay.

| Interval | Measured or reconstructed duration |
|---|---:|
| Committed caller transcript → cached model/tool decision | 2.217 s |
| Decision → WhatsApp tool return | 8.748 s |
| Synchronous delivery check within that tool interval | 3.001 s |
| Remaining tool interval | 5.747 s |
| Tool return → finalized confirmation text | approximately 0.940 s |

The installed OpenWA container had simulated typing enabled. The 195-character message reached the five-second cap; configured random variation allows **4.25–5.75 seconds** of typing delay. Its exact random draw was not logged. This accounts for most of the remaining tool interval; do not attribute all of it to WhatsApp network latency. Link previews were not the cause: the deployed sender disables them unless requested.

The progress utterance was queued at the pipeline entrance while the tool processor awaited execution. It therefore waited behind the same tool it was meant to acknowledge. A real Pipecat queue regression reproduces this and verifies the repaired ordering.

Speculation also synthesized tool XML/JSON unnecessarily. The logged 2.652-second speculative synthesis overlaps the tool interval and must not be added again to the total.

The database-registration turn's 2.446-second metric includes model decision and confirmation. The original logs do not isolate database request duration. Narration alone is not proof of persistence.

The goodbye followed an accepted end-call request, then peer reset, then a missing audio-end marker and aborted completion. The logs do not establish who initiated the reset. The old teardown could drain queued audio onto a closed link; this path now cancels and resolves interrupted playback instead.

Detailed original evidence: [call timing](../call-latency-0047/call-timing-evidence.json) and [initial analysis](../call-latency-0047/ANALYSIS.md). The initial analysis describes the pre-repair state; this report supersedes its pending-benchmark statements.

## What changed

- Disabled simulated typing in both repository Compose configurations and the installed OpenWA service. Sending pace, rate controls, session authentication and recipient restrictions remain enforced.
- Set synchronous delivery-confirmation wait to zero. Acceptance remains distinct from device delivery/read. Self-chat confirmation runs as an owned background operation; cancellation closes its tasks.
- Slow tools can emit brief English/French progress after 350 ms through the downstream speech-policy queue. Fast tools skip this extra speech. Tool-only model responses close before waiting, preventing playback-ID drift.
- Split tool protocol is held out of spoken text. Speculation retains useful tool decisions without synthesizing their protocol. Tool-loop limits now stop requeueing indefinitely.
- Added `CommittedTranscriptStopStrategy` for adapters that already publish an authoritative finalized transcript. Pipecat's priority system-stop frame can overtake its transcription data frame; the default external strategy then waits 500 ms. The new strategy requires both explicit stop and finalized transcript, handles either order, and disarms before callbacks so the timer cannot duplicate inference. It does not shorten acoustic endpoint patience.
- Restored spaces between policy-released sentences. Previously TTS saw text such as `choice.Since...now.May...`, which merged sentences and delayed the first useful audio chunk.
- Added content-free model, TTS and OpenWA timings, immutable call attribution and event timestamps. The UI labels its existing metric “Reply prepared,” because it measures transcript commitment to text finalization, not first audible sound.
- Added one total model-response deadline across retry attempts. Moved desktop activation off the event loop.
- Local Whisper prewarm now fails explicitly when the decoder cannot load. Removed its unnecessary Torch dependency; added a locked local-STT dependency extra and installed Faster-Whisper/CTranslate2 for actual testing.
- Removed unsupported blanket “Recommended,” “75 ms” and “6x multilingual” provider labels from the UI. The saved fast profile uses Gemini Live STT.
- Previous turn fixes retain Smart Turn v3.2, acoustic interruption, transcript stability and output speech-floor enforcement. Repeated short replies after a new acoustic epoch are accepted.

## Production pipeline replay

Real configured cloud STT, Gemini LLM and Edge TTS processed synthetic telephone-bandlimited English audio through the production pipeline. Phone output used a **simulated** 100 ms initial reservoir and 20 ms acknowledgement clock. All external action handlers were replaced before input; no WhatsApp message, CRM write, call, microphone or speaker was used.

| Measurement | Before handshake/spacing fixes | After, replay 1 | After, replay 2 |
|---|---:|---:|---:|
| Caller audio EOF → first simulated playout | 3617.5 ms | 2152.6 ms | 2465.7 ms |
| Transcript commit → first LLM request, inferred from stage clocks | 503.4 ms | 2.3 ms | 2.7 ms |
| Correction speech onset → output flush | 150.2 ms | 175.0 ms | 180.9 ms |

The scheduling defect is directly isolated. The whole 1.15–1.46-second improvement is **not** a controlled estimate of code impact: response text and cloud timings varied. The second utterance tests interruption, but the harness ends after its assistant text; it does not qualify complete second-response audio or long conversations. One replay attempted a WhatsApp tool, which the diagnostic mock blocked.

Artifacts: [before](pipeline-benchmark-before-handshake.json), [after 1](pipeline-benchmark-after-handshake-1.json), [after 2](pipeline-benchmark-after-handshake-2.json), [replay harness](benchmark_pipeline.py).

## Reference implementation comparison

The reference at `Speechto Speech Google/speech_to_speech.py` is a useful low-overhead baseline, but implements a different interaction contract:

- Microphone input is push-to-talk with 250 ms chunks. Explicit end-of-input supplies the endpoint, followed by 200 ms zero flush. The function returns the latest transcript snapshot without requiring finality. Automatic conversational endpoint detection cannot simply inherit that behavior without risking cutoff or missing speech.
- Its model call is also a non-token-streaming `GetModelResponse` request. The tested reference prompt was 513 characters with a short-answer instruction and four history messages; the production task/tool prompt was 34,364 characters, and the full pipeline with live state was about 41,000.
- Its Edge MP3 stream goes directly to ffplay. It does not perform the production phone framing, generation fencing, acknowledgement accounting, action verification and full-duplex interruption workflow.
- Reference file transcription has different chunk/flush settings from microphone mode. Those paths must not be mixed in a comparison.

Matched synthetic English/French component results on this Mac:

| Component and measurement | Samples | Median | Range |
|---|---:|---:|---:|
| Reference original STT: explicit EOF → return | 8 | 483.5 ms | 258–622 ms |
| Runtime cloud STT: EOF → automatic transcript commit | 8 | 783.7 ms | 566–1129 ms |
| Reference short-prompt LLM, complete response | 3 | 568.5 ms | 546–571 ms |
| Runtime full task/tool LLM, complete response | 3 | 930.4 ms | 757–931 ms |
| Reference wire client with the same full prompt | 3 | 759.5 ms | 696–794 ms |
| Reference TTS first MP3 bytes | 3 | 606.5 ms | 411–622 ms |
| Runtime TTS first 16 kHz PCM | 3 | 411.7 ms | 402–596 ms |
| Confidence-gated Faster-Whisper Turbo CPU decode | 12 | 6630.4 ms | 6479–13133 ms |
| Exploratory MLX Turbo q4 GPU decode | 12 | 608.6 ms | 368–749 ms |

The reference returned **empty text in 2/8 STT samples**, versus zero empty automatic-runtime transcripts. Other word differences remain. These short synthetic samples do not establish broad recognition quality. Raw WER also counts formatting differences such as “thirty nine” versus “39.” TTS arrival formats differ and its sample order can introduce warming bias; these numbers do not establish an inherent TTS speed winner.

**Faster-Whisper Turbo on this Mac's CPU is unsuitable as the low-latency recommendation.** Its confidence retry can approximately double decoding time, including on “Yes, please.” These timings exclude local endpoint wait. Gemini has already processed incoming audio before EOF, whereas the measured local decoder receives the complete buffer. This distinction is intentional for understanding the installed pipeline, not a universal model benchmark. CUDA hardware can produce a different result.

MLX uses Apple's GPU and is promising, but the exploratory q4 checkpoint/decoder does not use the same confidence gate and has not been integrated or qualified as a production replacement. Existing `whisper_mlx` routing requires correction before it can truthfully represent this benchmark.

The existing `compile_realtime` prompt was larger (40,058 characters), not a compact alternative. A three-sample experiment had median 1048 ms and an outlier at 4457 ms after primary errors and a slower fallback. Prompt compaction must preserve tools, consent and verified-action semantics; changing this compiler by name alone does not help.

Artifacts: [ASR](asr-benchmark.json), [LLM](llm-benchmark.json), [TTS](tts-benchmark.json), [Whisper CPU](whisper-benchmark.json), [MLX exploration](mlx-alternative-benchmark.json), [component harness](benchmark_components.py). `asr-wrong-bridge-diagnostic.json` preserves an invalid preliminary harness run using the wrong ASR bridge; it is excluded from these comparisons. `asr-common-request-benchmark.json` is a separate controlled-request experiment, not the exact original reference request.

## Pipecat architecture and Smart Turn

Installed Pipecat is 1.7.0. Its SystemFrames use a separate priority queue, while ordered data/control processing can be blocked by awaited work. This explains both the external-stop/transcript race and the delayed tool preamble. Repairs respect these queues and the speech permission/generation boundary rather than making ordinary speech uncancellable.

The production model adapter still waits for a complete provider response. Pipecat can forward streamed text, but cannot create token streaming from a nonstreaming bridge API. Sentence-preserving TTS now starts with the first released sentence; true model streaming requires a supported provider path.

Smart Turn predicts whether acoustic speech sounds complete; it does not fix STT omissions, tool waits, response hallucinations, transport disconnects or competing endpoint controllers. It therefore helps with phrases such as “I prefer…” but cannot guarantee human-level turn decisions in every French/English conversation. It must remain paired with VAD, transcript stability, continuation patience and immediate output interruption.

Primary references used alongside installed source: [Pipecat pipeline documentation](https://docs.pipecat.ai/guides/learn/pipeline), [Pipecat turn management](https://docs.pipecat.ai/guides/learn/turn-management), [Smart Turn repository](https://github.com/pipecat-ai/smart-turn), [Faster-Whisper repository](https://github.com/SYSTRAN/faster-whisper), [MLX Whisper implementation](https://github.com/ml-explore/mlx-examples/tree/main/whisper). Current documentation was also queried through Context7; installed version behavior was verified in source and real queues.

## Validation and remaining work

- Focused policy, interruption, queue and committed-turn tests: **121 passed**.
- Full isolated suite: **917 passed, 13 failed, 39 skipped**. Failures match the pre-existing baseline: frozen WhatsApp bytes, Android track retry contract, absent qualification audio, absent CI workflow, runtime migration rollback and S2S inventory. The suite is not green.
- Changed-file lint, configured Pyright and Python compilation passed. Dependency lock check passed (214 packages).
- Frozen verifier still reports pre-existing changes to `whatsapp_client.py` and `whatsapp_phone_client.py`. This work did not change those files or update the qualification manifest.
- Runtime refresh evidence is recorded separately in `runtime-refresh-result.json`; that file, not this report, establishes whether activation succeeded.

Remaining qualification includes French/English hesitation and noise matrices; longer continuous call/echo tests; safe speculative-context matching and cache-hit measurement; local STT floor-control parity and truthful MLX integration; bounded history/prompt improvements; physical phone first-audio and disconnect attribution; verified external-action and final-goodbye trials. The synthetic benchmark does not grant authority to call or message a customer. A physical call matrix needs an explicitly authorized test destination.

No “100% human,” “100x,” or permanent-fix claim is supported. The demonstrated improvements are removal of avoidable tool waits, a reproduced and eliminated 500 ms orchestration delay, restored sentence streaming, and measured interruption behavior within the stated test scope.

Activation verified: checkout Studio PID 84208 is healthy and idle, with Gemini Live STT, Gemini LLM, Edge TTS and Smart Turn enabled. Installed OpenWA is running with typing disabled, delivery wait zero and session ready. See [refresh evidence](runtime-refresh-result.json) and [health read-back](runtime-health.json). Studio embedded JavaScript syntax check also passed.

Further continuous-session testing found transcript truncation and prior-turn leakage not visible in the isolated-clip benchmark. See [continuous-turn findings and updated measurements](CONTINUOUS_TURN_FINDINGS.md). The newer partial-ASR stability rule changes the latency/accuracy balance; older STT timing numbers are historical, not the current profile.

The Apple GPU option is now implemented and benchmarked through the shared acoustic controller. See [MLX integration and full-pipeline evidence](MLX_INTEGRATION.md). Its decoder timings do not establish a conversational speed win; Gemini remains selected.

Durable-memory writes now distinguish generated text from acknowledged completed playback, and a synthetic history-size experiment has been completed. See [memory and history findings](MEMORY_AND_HISTORY_FINDINGS.md) for evidence and the broader qualification limits.

In-call repetition and evaluation history now distinguish pending drafts from acknowledged complete speech. See [repetition/playback ownership findings](REPETITION_PLAYBACK_FINDINGS.md) for the reproduced zero-playback failure and regression results.

The installed Gemini CLI and an isolated modern CLI were investigated as streaming alternatives. The adapter was repaired, but this account rejects the modern client, so it produced no usable comparison. See [Gemini CLI findings](GEMINI_CLI_FINDINGS.md). The live desktop bridge remains selected.

The verification foundation has been repaired: all protected corpus audio was reproduced byte-for-byte, packaged, and tested; provider migration defaults and missing CI/inventory entries were corrected. The suite now has one remaining frozen-media integrity failure. See [qualification recovery](QUALIFICATION_RECOVERY.md) for scope, staged Android build and remaining physical validation.

Further stream and full-pipeline testing reproduced a timeout-framing defect, unsupported ASR turn creation and queued playback-ID drift. These are repaired within the tested scope; see [stream and playback findings](STREAM_AND_PLAYBACK_FINDINGS.md), including the preserved failed replay and recognition-corpus limitations.

Meaning-changing corrections are no longer blocked by approximate repetition checks. Buffered local recognizers now share the neural controller, and a CPU replay exposed and resolved a slow-decoder watchdog conflict while confirming unacceptable CPU latency. See [meaning and local-controller findings](MEANING_AND_LOCAL_PARITY.md).

Terminal model failures now recover through the current guarded speech turn, without replaying partial tool requests or leaking late generation output. Normal and injected-failure replays completed with resolved playback accounting. See [generation recovery findings](GENERATION_RECOVERY_FINDINGS.md) for measurements, activation and remaining scope. The latest isolated suite is 1,088 passed with the existing frozen-WhatsApp integrity failure.

A recognition disconnect previously allowed a pending fragment to be committed as a complete caller request. Bounded reconnection now invalidates the affected utterance and stale audio, preserves acoustic floor ownership and waits for recognition availability before requesting repetition. See [ASR recovery findings](ASR_RECOVERY_FINDINGS.md) for the before/after probe, real-provider disconnect replay and remaining qualification scope.

TTS failure handling now distinguishes partial synthesis from successful completion, closes failed contexts promptly, preserves response identity through playback callbacks and offers one guarded repair when no usable reply remains. See [TTS failure and playback identity findings](TTS_FAILURE_FINDINGS.md) for controlled before/after evidence, real-provider replays, final1113-pass suite with the existing frozen failure, and remaining physical qualification.

A broader36-turn bilingual/noise matrix exposed remaining partial-tail commits and a noisy French refusal omission. Partial-confirmation and request-prefix repairs preserve the demonstrated continuations; explicit uncertainty covers negation lost between hypotheses, but cannot recover negation never recognized. See [bilingual/noise findings](BILINGUAL_NOISE_FINDINGS.md), including the matched local MLX cross-check, remaining recognition gap and repaired Studio WebSocket shutdown. Final suite1136 passed with the existing frozen failure; Studio PID2847 is healthy and idle.

Local corroboration experiments revealed an onset-capture defect and gaps between recognition uncertainty, model speech and tool authority. Local pre-roll, direct clarification, uncertainty/epoch tool barriers and startup readiness handling are repaired. The optional verifier remains disabled because proper-name disagreements exceed its rollout target. See [corroboration and uncertainty findings](CORROBORATION_FINDINGS.md) for the full matrix, French before/after conversation,1184-pass regression result with the existing frozen failure, and active runtime read-back.

French barge-in variance was traced to Pipecat 1.7 resetting Silero's recurrent state on a five-second wall timer. Phone and STT PCM matched exactly. Moving reset ownership to safe call/assistant-floor boundaries reduced five repeated simulated-phone interruptions to **218-229 ms** while retaining the 0.7/120 ms production sensitivity. See [continuous VAD and interruption findings](VAD_INTERRUPTION_FINDINGS.md). Final suite: 1,188 passed with the existing frozen-WhatsApp failure; Studio PID 15213 is healthy and idle.

The final prompt audit found five saved persona instructions that conflicted with the immutable AI identity. The compiler now omits those lines from the effective runtime prompt without modifying the saved persona. Future imported prompt sections with deceptive identity instructions are rejected, while contrast examples may still document unsafe wording. Direct English and French identity questions are also guarded at speech time: the approved disclosure is spoken and the response ends there instead of continuing into a sales pitch.

Mutable live state duplicated stable product facts and conversation rules. Removing that duplication reduced live state from **6,531 to 1,943 characters** and the effective prompt from roughly **40.3k to 35.7k characters**. A six-pair evaluation through the selected Antigravity bridge measured a **698.35 ms** median with the preserved state and **656.33 ms** with the compact state; all 12 pricing answers were correct. This small paired diagnostic supports the compaction but does not prove a fixed latency improvement. The bridge remains a unary `GetModelResponse` path, so model text cannot begin before the complete bridge response arrives.

Bridge-only identity validation passed all six English/French direct, robot and adversarial pressure cases. Raw responses disclosed AI identity in 6/6 cases, and the deterministic guard produced a truthful first sentence and stopped further speech in 6/6. Focused policy tests pass **131/131**. The full isolated suite now reports **1,204 passed, 39 skipped, one device test deselected**, with the sole failure still limited to the two known frozen WhatsApp files. Ruff, configured Pyright, compilation, feature-flag governance and migration-inventory validation pass. The frozen manifest remains unchanged. The production selection remains `antigravity_live` STT plus `antigravity_gemini` LLM; official provider diagnostics are no longer part of this program.

The prompt build was first activated in Studio PID **17293**. Its post-refresh read-back reported healthy and idle, with `antigravity_live` STT, `antigravity_gemini` LLM, Edge TTS, Smart Turn enabled, the 600/900/3000 ms turn profile intact and local corroboration disabled. Source hashes for the prompt compiler, speech guard and streaming VAD were verified from the activated checkout.

A final simulated-phone replay exposed a separate WhatsApp authority gap: “I would like the six month plan” caused the model to request a send before the caller authorized messaging. Tool execution now requires either a direct current-turn request to send/share the details or a short affirmative after a WhatsApp send proposal that reached playback. Plan selection alone returns `explicit_caller_authorization_required`, with guidance to ask once and never describe the block as a technical failure. Focused tool regressions pass **40/40**. After the repair, the same real-bridge replay asked for permission and did not invoke the WhatsApp handler.

Three final bridge-only simulated-phone samples also show the remaining latency variance clearly: first playout after caller audio EOF was **9,254.5 ms**, **4,896.0 ms**, and **2,452.2 ms**. The instrumented middle run attributed **2,785.2 ms** to the unary Antigravity model response and **1,241.8 ms** to first Edge PCM; the final run measured **745.2 ms** for the model and **759.8 ms** for first Edge PCM, with speculative preparation reducing the correction response to **218.1 ms** and interruption flushing in **159.6 ms**. This tail cannot be represented as a stable two-second experience. The bridge has no token-streaming model method, so provider response variance remains on the critical path.

After the tool-intent repair, the full isolated suite reports **1,206 passed, 39 skipped, one device test deselected**, with the same single protected-manifest failure for the two known WhatsApp files. Ruff, configured Pyright and compilation pass. Studio PID **17925** is active, healthy and idle with both Antigravity bridges, Edge TTS, Smart Turn and the existing turn profile; local corroboration remains disabled.
