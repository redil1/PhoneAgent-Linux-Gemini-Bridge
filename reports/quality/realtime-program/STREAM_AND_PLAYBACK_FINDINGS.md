# Stream framing, acoustic input ownership and playback identity

2026-09-05. The overall goal remains active.

## Reproduced transport defect

The custom Connect/HTTP stream reader removed the five-byte envelope header before buffering its complete payload. If the socket timed out while reading that payload, the next call parsed payload bytes as a new header. A controlled test with 54 split positions reproduced failures at 46 positions before the repair.

The reader now retains the header until the full payload is available. All 54 split-position cases pass. Additional tests cover truncated payloads, oversized lengths and distinguishing timeout from EOF. The reader reports an unexpected EOF once instead of polling the closed connection indefinitely; end/error envelopes are surfaced as errors. Automatic reconnection and recovery from provider failure remain separate outstanding work.

The reference implementation uses buffered HTTP response reads with a long socket timeout; it does not have this custom resumable-parser path. This is an architectural difference worth correcting, but there is no packet capture proving it caused the originally supplied call symptoms.

## Noise and level observations

Actual silence and the protected noise-only fixture produced no caller transcript in the live cloud probes. However, injecting a provider transcription over received silence previously created “Yes, please” as a caller turn with zero acoustic speech epochs. Provider text is now accepted at the reader boundary only for an open, acoustically detected turn. A resolved empty turn cannot be revived by late text without a new speech epoch.

The final fault probe records the injected provider text but produces zero caller turns and zero interruptions. A natural English control attenuated by 20 dB retained its full meaning. Normal-level natural English and French controls also retained their expected meaning, with harmless numeric-format differences.

The protected eSpeak fixtures were much less successful as recognition inputs: English yielded no transcript and French only a short prefix during these probes. They remain valuable deterministic contract/integrity fixtures; their labels alone do not make them a representative recognizer-quality corpus. The natural-voice controls distinguish this from total recognizer failure.

The first probes used the constructor's 150 ms chunk setting; later probes explicitly used the production 200 ms setting. At 200 ms, measured send requests were approximately 7–8 ms median, the queue peaked at one chunk and drained fully. No sender backlog was observed in those samples. The weak eSpeak results persisted, so they are not attributed to the repaired parser or to sender backlog.

## Playback correlation failure exposed by the full replay

A full production replay exposed a cancelled queued follow-up remaining in the playback-ID FIFO:

1. Response 1 began playing.
2. Response 2 was prepared and queued.
3. The caller interrupted response 1.
4. Response 3 answered the correction.
5. Response 3's audio was credited to response 2, leaving response 3 unresolved.

Interruption now retires all queued response identities and emits their interrupted outcomes. A genuinely started response retains its identity until its stop/acknowledgement callback reports rendered frames. A queued goodbye is also disarmed, allowing a later legitimate close request. Explicit IDs are used when reporting cancelled queued items, without temporarily changing the active playback identity.

Targeted state-machine tests cover the observed queued-follow-up condition and interruption before any response starts. A later full-provider replay completed both caller turns and resolved playback accounting. It measured **2122.6 ms from source audio EOF to first simulated phone playout**, and **168.4 ms from correction onset to flush**. That replay had no tool call, so the exact extra-follow-up condition is established by the targeted regression, not by claiming the later run reproduced the identical model output. Phone timing remains simulated.

## False action wording

The failing replay also showed “I've just sent…” passing the action-claim guard despite a deliberately blocked diagnostic tool result. Common adverbs were missing from that guard. “Just,” “already,” “now,” and tested French completed-action variants now require the same verified receipt as the simpler “I sent.” Negative statements remain allowed, and a verified `sent` receipt permits the corresponding wording. This fixes the observed variants; it is not a claim that regex rules prove every possible natural-language assertion.

## Validation and evidence

- Framing/acoustic focused set: 110 passed, including the EOF liveness regression.
- Playback queue/policy/memory set: 67 passed.
- Action-claim/orchestration/personality set: 40 passed.
- Full isolated suite: **1071 passed, one frozen-WhatsApp integrity failure, 39 skipped, one device test deselected**.
- Changed-file Ruff, configured Pyright and compilation passed.
- The frozen files and their manifest remain unchanged.

Artifacts:

- `noise-level-benchmark-before.json` and `noise-level-after-framing.json`: initial constructor-setting probes.
- `noise-level-benchmark.json`: production-sized sender measurements.
- `natural-tts-positive-controls.json` and `final-acoustic-boundary-controls.json`: natural/quiet voice and injected-fault controls.
- `pipeline-benchmark-after-framing.json`: preserved failing playback-correlation replay.
- `pipeline-benchmark-after-queue-fix.json`: later complete two-turn replay.
- `runtime-asr-framing-refresh-result.json` and `runtime-asr-framing-health.json`: refreshed healthy Studio with provider choices unchanged.

No calls or external messages were placed. Remaining work includes broader recorded/noisy/echoing and continuous conversations, explicit physical validation, staged Android deployment, frozen-media qualification, richer playback/text correlation, approximate repetition false positives, failure recovery and remaining local-provider parity. No universal human-quality guarantee is established.
