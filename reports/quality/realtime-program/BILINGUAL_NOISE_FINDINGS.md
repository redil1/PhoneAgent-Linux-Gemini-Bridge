# Continuous bilingual/noise qualification and partial-transcript evidence

## Findings

The expanded matrix exposed errors missed by the earlier eight-turn corpus. One continuous Gemini ASR session processed 36 synthetic turns over approximately three minutes: 12 English/French utterances each in clean conditions, with 20 dB stationary noise, and with 10 dB stationary noise. Speech and noise were band-limited to 300-3400 Hz; input was paced in 20 ms PCM frames. These are synthetic stationary-noise conditions, not real café, echo or GSM-codec recordings. Known source EOF was used only for measurement, never supplied to the endpoint controller. No LLM, phone call, microphone, speaker or external action was involved in this matrix.

Before the changes, four turns differed from their expected words:

- “Actually, I want the six month plan” committed as “Actually, I want the six month”. The complete update arrived about 58 ms later.
- The second “Yes, please” committed as “Yes”. The next update arrived about 13 ms later.
- Under 10 dB noise, “I prefer movies and series” committed as “I prefer movies”. The next update arrived about 33 ms later, with the final words arriving afterward.
- Under 10 dB noise, French “Non, pas l'abonnement annuel” became “à l'abonnement annuel”, losing the refusal. In the first replay an early hypothesis contained “Pas”; in the subsequent replay no hypothesis contained the negation. These are recognition errors with different detectability, not an endpoint-only problem.

A separate four-case longer-pause replay reproduced premature commitment: “Est-ce que vous pouvez?” was committed about **364 ms before** the continuation resumed. The old harness stopped observing that row at source EOF after the first commit, so its final-row text is not proof that the later words would never arrive. The timestamp does prove the premature turn. The harness now keeps observing while the caller owns the floor.

## Changes

Nonfinal cloud hypotheses require at least two identical accepted observations belonging to the same acoustic speech-end state before early commitment. Existing partial-stability and Smart Turn/received-silence checks still apply. Resumed speech invalidates the confirmations. Provider-final text does not need an extra duplicate. If a provider never confirms its partial, the existing bounded incomplete-turn interval allows an explicitly untrusted fallback rather than an indefinite wait.

Bare modal request prefixes such as “Could you?”, “Could you please?”, “Est-ce que vous pouvez?” and “Pourriez-vous?” now receive continuation patience despite ASR-inserted question marks. Complete requests and concise answers such as “Oui, vous pouvez” and “Yes, I can” retain ordinary endpointing. These are bounded patience hints; some contextually complete elliptical questions can consequently wait longer.

If ASR revisions remove a previously observed English/French negation, the resulting transcript is explicitly untrusted for task updates and action authorization. The original recognized text is retained; the software does not invent the missing refusal. This gate **cannot detect a refusal the recognizer never includes in any hypothesis**, as demonstrated in the second noisy French replay. Normalization, polarity revision and per-turn reset are tested. Idle silence no longer triggers unnecessary Smart Turn inference after a completed turn.

## Before/after measurements

| Condition | Expected words retained before | After | Median EOF to commit before | After |
|---|---:|---:|---:|---:|
| Clean | 10/12 | 11/12 | 1,588 ms | 1,582 ms |
| 20 dB noise | 12/12 | 12/12 | 1,598 ms | 1,605 ms |
| 10 dB noise | 10/12 | 11/12 | 1,583 ms | 1,574 ms |

The post-change mismatches were removal of the filler “Actually” while preserving the six-month request, and the unresolved noisy French refusal error. All word-tail omissions from the first replay were absent in the second. No row in either 36-turn matrix committed before the specified 650-1200 ms continuation pause. These small, sequential, single-provider replays are diagnostic evidence, not population accuracy estimates or proof of a universal speed improvement. Matching ignores punctuation/case and equates “6” with “six”.

All four post-change long-pause cases preserved the full expected words with one commit and no premature continuation. These included 1.8-second English preference patience, 2.4-second French preference patience, and the previously failing 1.8-second French request pause. The French question still took about 2.87 seconds after EOF to commit: Smart Turn can remain incorrectly pessimistic about a complete synthetic question. Its CPU computation itself was tens of milliseconds; recognition cadence and completion decisions dominated this delay. Lowering the global threshold on these few samples is not justified.

The normal full-pipeline replay after the changes reached first simulated phone audio **2,230 ms after EOF**, flushed on interruption in **177 ms**, transcribed and answered the correction, and resolved playback accounting. It used real providers with external tool effects disabled. It is not a physical first-heard measurement.

## Local cross-check of the unresolved refusal

The already-cached Apple GPU Whisper Turbo q4 model correctly decoded both negative-answer clips across clean/20 dB/10 dB conditions in all 12 tested combinations of automatic and explicit language. The current confidence gate marked all 12 trusted. Median decoder time was **1,182 ms with automatic language** and **604 ms with explicit language**; initial loading/prewarming took about 1,733 ms. The noisy French refusal was preserved at 10 dB in both modes.

These inputs use the same deterministic noise realization as the cloud matrix, including 120 ms of trailing audio. Decoder timing excludes endpoint detection and does not establish hybrid end-to-end latency. This is MLX on Apple Silicon, not Faster-Whisper CPU/CUDA. No hybrid verifier has been installed or enabled by this phase. Broader independent clips and a concurrent, deadline-bounded integration would be needed before choosing a corroboration strategy or changing the selected cloud provider.

## Evidence and scope

Artifacts: [summary](bilingual-noise-summary.json), [before matrix](bilingual-noise-sequences.json), [after matrix](bilingual-noise-sequences-after.json), [long pauses before](bilingual-long-pauses-before.json), [long pauses after](bilingual-long-pauses-after.json), [local cross-check](noisy-local-crosscheck.json), [full-pipeline replay](pipeline-partial-confirmation.json), [matrix harness](benchmark_bilingual_noise_sequences.py).

The initial 16-row diagnostic ended with a harness reshape error because a 650 ms pause was not aligned to a 20 ms PCM frame. Combined fixtures are now padded to full frames. That incomplete run is preserved in `bilingual-noise-sequences-harness-diagnostic.json` and is excluded from the 36-turn comparison.

Focused cloud, endpoint, reconnect and local-controller tests: **88 passed**. Final full-suite and idle-runtime refresh results are appended after completion. Physical EN/FR continuous/noisy/echo qualification, the unresolved refusal omission, broader provider validation, staged Android deployment, verified external actions and frozen-media requalification remain outstanding. The original end-to-end goal remains active.

## Studio refresh lifecycle finding

During activation, the previous Studio process exceeded the refresh helper's 45-second shutdown grace and then exited. The helper had already stopped, leaving no replacement listener. The service was restored after verifying that both the old process and port owner were absent; its saved provider/task/turn configuration matched the previous read-back. The old process environment was no longer available, so restoration used the current desktop shell plus existing saved Studio settings. The interrupted activation record is preserved in `runtime-partial-confirmation-restoration-result.json`.

Host logs showed an open browser WebSocket waiting for heartbeat termination during shutdown. Studio tracked these sockets but did not close them in `on_shutdown`. It now closes them with a bounded, concurrent going-away handshake, rejects connections once shutdown begins, handles a handshake/shutdown race and removes registrations when initial status delivery fails. This follows [aiohttp's documented WebSocket shutdown pattern](https://github.com/aio-libs/aiohttp/blob/master/docs/web_advanced.rst), verified against installed source. The refresh helper also allows75 seconds for the old implementation's remaining shutdown period; it does not force-kill Studio.

The WebSocket and existing Studio tests passed **62 tests**, including a real local WebSocket that does not reply to the closing handshake. They exercise only local HTTP/WebSocket lifecycle, with voice startup disabled in the test fixture.

Final validation: **1,136 passed, 1 failed, 39 skipped, 1 deselected**. The sole failure remains the pre-existing frozen-WhatsApp integrity check; its protected files and manifest were not changed. Changed-file Ruff, configured Pyright using the checkout interpreter and Python compilation passed. [Final activation read-back](runtime-partial-confirmation-refresh-result.json) confirms Studio PID **2847** is healthy and idle, with the expected Gemini Live/Gemini/Edge TTS selection, task and turn settings retained.

Subsequent audit clarified the authority boundary: the uncertainty metadata initially prevented task/memory updates, while generic tool execution still had a gap beyond literal-argument mismatches. The later [corroboration/uncertainty work](CORROBORATION_FINDINGS.md) closes that gap and enforces clarification before model speech. It also shows why the optional verifier has not been promoted.
