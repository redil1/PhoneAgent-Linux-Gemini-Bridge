# Playback evidence, durable memory and history-size findings

2026-09-05. The full voice-quality objective remains active.

## Verified memory defect and repair

Previously, `AgentPolicyRuntime` scheduled durable memory extraction at response generation. `ValidatedMemoryWriter` submitted the entire generated response as an agent episode before the phone's playback outcome was known. Interrupted, dropped or failed speech could therefore be represented as delivered conversation in later memory retrieval.

The runtime now captures the caller/response pair at generation but defers its memory write until playback is resolved. A completed response with a positive acknowledged frame count can enter remembered agent speech. Interrupted, failed, undelivered and unconfirmed output retains the caller's trusted words and preferences, but does not submit the generated assistant response to semantic agent memory. Generated text is retained separately as `generated_ai` in the diagnostic episode, alongside `delivery_status`; the remembered `ai` field is empty when delivery was not established.

This is acknowledgement evidence, not a measurement of what a person physically heard. Partial word-level delivery cannot be reconstructed reliably from a count of audio frames, so the implementation does not invent a spoken prefix. A legacy completion callback without frame evidence is treated as unverified for durable memory.

Other details covered by regressions:

- A reply finishing after the caller starts a new turn still refers to its original caller-text snapshot.
- A playback outcome arriving before streamed-response finalization is retained and applied when that response is recorded.
- Disconnection/close resolves queued drafts conservatively and retains associated caller facts.
- Out-of-order playback results drain in conversational order. Storage operations are serialized, so an older preference cannot finish writing after a newer correction and overwrite it.
- Progress utterances are excluded from duplicate turn-memory writes. Low-confidence caller transcripts remain excluded from durable facts.

This changes future writes. Historical records were not retroactively verified or deleted. It also does not yet redesign the separate in-call repetition bookkeeping, which currently records sentences when released toward TTS; that path still needs playback-aware review.

## History-size experiment

A separate probe sent the real Gemini bridge nine synthetic assembled histories. It used the current task/system prompt and tool protocol, with a seeded preference for French subtitles and exactly one television. It then added 0, 20 or 60 short exchanges and asked the model to recall those two facts. The varying live advisory block was intentionally not included; this isolates history growth with a stable task/tool prefix.

| Additional exchanges | Prompt characters | Samples | Median complete-response time |
|---|---:|---:|---:|
| 0 | 34,585 | 3 | 2015.5 ms |
| 20 | 37,329 | 3 | 2397.9 ms |
| 60 | 42,845 | 3 | 2099.8 ms |

Both seeded facts were recalled correctly in all nine responses. There was no monotonic latency increase in this small probe, so these results do not justify deleting older conversation history to claim a speed improvement. They also do not establish stability during a real sixty-turn call: the history was assembled, the recall task was simple, and no STT/TTS/audio transport or external actions ran.

The measured static system block was 27,773 characters and the emitted tool protocol 6,499 characters. The separately measured live advisory block was 6,531 characters. These sizes identify potential review targets, but prompt reduction needs evidence that it preserves identity, current facts, caller meaning, tool constraints and action truth.

Artifacts: `history-growth-benchmark.json`, `history-growth-summary.json`, and `benchmark_history_growth.py`.

## Validation and activation

- Initial memory/policy/repair/identity regression set: 121 passed.
- Ordered-memory regression set: 54 passed, including the additional out-of-order storage test.
- Full isolated software suite: **964 passed, 13 pre-existing failures, 39 skipped, one device test deselected**.
- Changed runtime-file Ruff, configured Pyright and Python compilation passed.
- An additional strict check of the memory files reports a pre-existing `MemoryBlock.kind` literal/enum typing mismatch; it is not claimed as fully strict-type-clean.
- A full repository Ruff audit reports 104 findings: 24 in the reference implementation, 37 in diagnostic reports/scripts and 43 in extracted stock-ROM material. The changed runtime files pass; repository-wide lint is not clean.
- Frozen WhatsApp verification still reports the previously modified two frozen files. They and their qualification manifest were unchanged by this work.

Activation and effective-provider read-back are recorded in `runtime-memory-refresh-result.json` and `runtime-memory-health.json`. Gemini remains selected. No customer call, message or CRM write was performed by these tests.

Remaining work includes in-call repetition/playback correlation, broader continuous and noisy conversation evaluation, other local-provider turn-controller migration, provider/first-audio latency work and physical-call qualification. The active goal has not been marked complete.
