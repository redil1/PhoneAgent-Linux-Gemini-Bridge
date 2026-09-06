# Repetition checks and playback ownership

2026-09-05. The voice-quality goal remains active.

## Reproduced failure

A regression using the real `ResponsePolicyProcessor` demonstrated that a price answer cancelled before any phone playback was treated as already spoken. When the caller asked the price again, the same answer was rejected by the repetition guard and a generic repair was substituted. In a pipeline with a bound retry handler, this path can also request an unnecessary additional model generation.

This was a distinct problem from durable memory. The in-call `_spoken_sentences` and `_completed_ai_turns` collections were populated at sentence release/response generation, before delivery evidence arrived.

## Change

The policy now keeps pending sentence and turn drafts under their response identities. Repetition checks compare a new sentence against:

1. Acknowledged complete speech from earlier responses.
2. Earlier sentences in the same current draft, so model loops inside a response remain blocked.

An unconfirmed older draft does not block a new response, including the interval before a delayed stop callback arrives. Completed playback with positive frame evidence promotes that response into confirmed repetition/evaluation history. Interrupted, failed, undelivered and unverified drafts are discarded from those histories. No attempt is made to infer exactly which words were heard during partial playback.

Both streamed and non-streamed responses now follow this accounting. A completion callback arriving before streamed text finalization is handled without duplicating history entries. Discarding a reservation cannot classify an already-active partial playback as undelivered; its actual playback outcome remains authoritative.

Direct legacy calls that explicitly populate spoken history without a response identity retain their existing behavior. The production response processor supplies response identities, including its bounded recovery fallback.

## Validation

- Before repair, the real-processor regression failed: the second price answer was blocked as repeated even though the first had zero delivered frames.
- After repair, that same test releases the requested answer on both attempts, with the first still correctly classified as interrupted.
- Negative tests confirm that duplicate sentences inside one draft and previously acknowledged complete answers remain detected.
- Partial failures, unverified completion, callback ordering and non-streamed evaluation history are covered.
- Focused repetition/policy/repair/memory/queue tests: **144 passed**.
- Full isolated software suite: **974 passed, 13 pre-existing failures, 39 skipped, one device test deselected**.
- Changed-file Ruff, configured Pyright and Python compilation passed.
- Frozen WhatsApp verification still reports the two pre-existing changed files; this work changed neither those files nor their manifest.

No new physical latency number is claimed. The controlled regression proves removal of an incorrect repetition rejection; provider timing varies, and the size of a real-call latency saving requires a matched call replay.

Activation and selected-provider evidence are recorded in `runtime-repetition-refresh-result.json` and `runtime-repetition-health.json`. Gemini remains selected.

## Remaining scope

The live advisory block still distinguishes generated/playing/interrupted text, and partial assistant context is managed separately by Pipecat's aggregator. This change does not provide word-aligned proof of what a person heard. Broader in-call context correlation, semantic false positives in approximate repetition checks, long/noisy/echo tests, other local-provider migration, streaming-provider comparisons and physical call qualification remain outstanding.

The current desktop LLM bridge returns a complete response, not streamed tokens. No Google/Gemini API key was found in the active Studio process environment. An optional question about configuring the existing direct streaming provider has been left with the user; no provider switch or credentials change was made. A Gemini CLI executable is also available, but its existing adapter's streaming format and tool-disable configuration need validation before an inference comparison.
