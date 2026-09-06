# Continuous French/English turn findings

2026-09-05. This extends `ANALYSIS_AND_RESULTS.md`; the original objective remains active.

## A meaningful accuracy defect hidden by isolated clips

An eight-utterance continuous replay used the real Gemini ASR bridge, Silero VAD and `smart-turn-v3.2-cpu.onnx`. Input was synthetic telephone-bandlimited PCM paced at 20 ms. One ASR session remained open throughout. Known audio EOF was measured but was not supplied to the endpoint controller. No phone call, external message, CRM write, microphone or speaker was used.

The earlier 100 ms transcript-stability window was shorter than the observed provider revision cadence. Acoustic completion therefore released text before the recognizer had delivered its last words. The next acoustic epoch could then adopt a delayed provider final or cumulative prefix from the previous turn.

Observed examples before repair:

| Source utterance | Committed text |
|---|---|
| Yes, I can. | Yes, please. Yes, I can. |
| Je ne sais pas. | Je ne sais |
| Where are you calling from? | pas Where are you calling |
| I prefer movies and series. | Where are you calling from?I prefer movies and series. |
| Oui, s'il vous plaît. | Oui, s'il vous |
| A second Oui, s'il vous plaît. | plaît Oui, s'il vous |

These are functional errors, not just punctuation differences. “Je ne sais” omits the negation, and a previous question attached to a new preference can steer the model incorrectly. A low text-commit latency is not valuable if that text is wrong.

## Repairs and their boundaries

1. **Separate partial-ASR patience from acoustic turn completion.** The runtime now defaults to 650 ms stability for a changing/nonfinal transcript, while retaining the shorter 100 ms window for provider-final text. Smart Turn analysis and speculative preparation can run during this period. The new field is `antigravity_live_partial_stability_ms`; the child process receives the corresponding environment setting, and Studio reports the effective value.
2. **Remember provider-segment ownership across acoustic turns.** A retired raw transcript watermark is updated when a late revision is suppressed. An outstanding old final is consumed without appending it to the new turn. Cumulative prefixes are removed while that provider segment remains unsettled; a genuinely repeated answer after a settled segment remains valid.
3. **Remove incorrect lexical vetoes.** Complete French negatives such as “Je ne sais pas,” affirmative elliptical answers such as “Yes, I can,” and common stranded-preposition questions no longer automatically force the three-second incomplete-turn delay. This does not bypass VAD or Smart Turn. Clear fragments such as “I prefer” and “Je préfère” retain continuation protection.
4. **Make speculative context match actual commitment.** Preparation now runs the same caller-state transition on an isolated copy of task, language/repair, call context, recent turns and action evidence. The live call receives no speculative transcript/task events or mutations. The complete prompt must still match at consumption. Tests verify that changes to transcript, confidence, tool catalog and playback state reject a cached result.
5. **Keep malformed protocol out of speech.** Truncated tool openers and case variants follow the validated bounded tool/repair path. EOF no longer releases a held `<tool_` prefix as spoken text.

The ASR bridge does not provide an audio-aligned segment identifier in the fields this adapter currently consumes. Watermark ownership is an improvement demonstrated by these replays, not proof against every possible out-of-order revision. Representative recorded and physical-call qualification is still required.

## Measured results

After ownership and partial-stability changes, then after lexical corrections, both eight-turn replays preserved all expected words when case and punctuation are ignored. Neither run committed before the continuation during the 900 ms English or 1200 ms French preference pause. The final replay retained both identical French answers as separate turns.

| Complete utterance | With corrected words but old lexical veto | After lexical correction |
|---|---:|---:|
| Yes, I can. | 2964 ms | 1791 ms |
| Je ne sais pas. | 2992 ms | 1643 ms |
| Where are you calling from? | 2964 ms | 1723 ms |

These measure caller-source EOF to transcript commitment. They are small real-provider samples, not stable population percentiles or perceived audio measurements. Cloud cadence varied. The final eight-turn run ranged from 1417 to 1822 ms to commitment, and was more conservative than the earlier isolated clips.

Smart Turn CPU inference itself had a median of **70.6 ms** in the final run (52–83 ms). Its decisions were not perfect: a synthetically spoken “I prefer” received high completion probabilities during its internal pause, while “Yes, I can” initially received low completion probabilities. The text guard preserved the unfinished preference. This is direct evidence that the ONNX model is useful but cannot definitively solve turn-taking on its own.

A real production-pipeline replay after the repairs reached the simulated phone output **2621 ms after caller audio EOF**, with **185 ms** barge-in-to-flush. It recorded a speculative cache hit. This one run was slower than the shortest earlier 2153 ms run; response text, cloud timings and action paths varied, and the new partial-recognition window also changes the correctness/latency balance. Do not describe this as a universal latency reduction. Further latency work must retain the recovered words.

## Evidence and validation

- `continuous-turn-summary.json`: compact before/after text and timing evidence.
- `turn-sequences-before-short-answer-fix.json`: original continuous failure trace.
- `turn-sequences-after-ownership.json`: recovered words, before lexical corrections.
- `turn-sequences-after-ownership-and-grammar.json`: final eight-turn replay.
- `benchmark_turn_sequences.py`: reproducible continuous input harness.
- `pipeline-benchmark-projected-context.json`: first observed real speculative hit.
- `pipeline-benchmark-complete-transcripts.json`: production replay with the recognition repairs.
- `turn-sequences-discovery-diagnostic.json` and the initial `/tmp` harness logs describe setup mistakes (missing discovery call / treating a void-returning discovery method as boolean). They are excluded from model comparisons.

Focused continuation/context/protocol tests: **130 passed**. Full isolated suite: **942 passed, 13 pre-existing failures, 39 skipped**. Changed-file Ruff, configured Pyright and Python compilation passed. The frozen WhatsApp verifier still detects the previously modified frozen files; neither those files nor their manifest was changed here.

Activation evidence is in `runtime-sequence-refresh-result.json`, and post-refresh effective configuration is recorded in `runtime-sequence-health.json`.

Still incomplete: long/noisy/echoing real conversations; explicit physical phone playback and goodbye qualification; full local STT floor parity and MLX integration; bounded history/prompt work; broader speculative hit-rate and final-audio latency distributions; the pre-existing release/qualification failures. The implementation is improved and tested within the stated scope, not certified as universally human-equivalent.
