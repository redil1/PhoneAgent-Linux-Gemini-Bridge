# Gemini CLI streaming alternative: findings and repairs

2026-09-05. This investigation did not switch the live STT or LLM provider.

## What was found

The system Gemini CLI is version **0.1.9**. Inspection of its installed source confirms incremental model-text writes, but stdout also carries terminal-title control sequences and can carry authentication diagnostics. The old PhoneAgent adapter forwarded stdout chunks directly as LLM text. A text-only probe emitted stdout before exiting with an authentication error requiring `GOOGLE_CLOUD_PROJECT`. Its recorded 1217.8 ms `first_text_ms` is therefore **first stdout arrival, not valid assistant latency**.

The adapter also ended every prompt with “Do not use tools.” That contradicted the PhoneAgent WhatsApp/business-tool instructions. Native CLI tools and PhoneAgent's emitted action protocol need distinct treatment.

An isolated CLI **0.39.1** was installed under `/private/tmp/phoneagent-gemini-cli-0.39.1` for inspection and testing. The system installation was not replaced. Its explicit headless, structured-output probe reached authentication but was rejected with `UNSUPPORTED_CLIENT`: this account's Code Assist tier no longer accepts that client and directs it to the Antigravity suite. No assistant response was produced. These are authentication/eligibility findings, not a performance comparison and not proof that CLI streaming is faster or slower.

## Adapter repairs

- Version-aware profiles recognize the inspected 0.1.9 legacy and 0.39.1 structured protocols; unverified versions fail before inference.
- The modern profile uses explicit headless `--prompt`, `--output-format stream-json` and `--extensions none`. Only assistant delta events become model text. User echoes, initialization, warnings, results and diagnostics do not become speech. Unexpected native tool events, malformed output, incomplete streams and unsuccessful results are errors.
- Core tools, discovery/call commands, MCP servers, extensions, skills and hooks are disabled in the modern profile. Existing system constraints are retained in a private temporary settings overlay; secure mode remains enabled. The original system-defaults location is preserved. User authentication settings are not replaced, and legacy/modern auth selections were checked after the probes.
- Legacy stdout is buffered until successful process completion, with window titles disabled and startup/auth/control output rejected. It is explicitly a buffered compatibility path, not advertised as trustworthy token streaming. Legacy home extensions are rejected because that version loads them outside workspace settings.
- The final phone prompt permits the exact connected PhoneAgent action protocol while prohibiting native CLI commands/tools. The provider explicitly declares that it does not implement Pipecat native function calling.
- The turn deadline covers process-lock waiting, launch, stdin backpressure and output. Cancellation, parse failure and early generator close terminate and reap the owned process. Stdin closure is awaited so a terminated writer does not leave an unobserved broken-pipe exception.
- Content-free first-text/completion telemetry includes the output mode and outcome, avoiding confusion between buffered legacy output and structured streaming.

The first modern diagnostic exposed a deprecated auto-update setting and a missing explicit headless argument; both were corrected. The final profile uses the inspected `general.enableAutoUpdate` and `model.maxSessionTurns` fields. Account eligibility was not worked around, and no further authenticated CLI comparison is justified without an eligible configuration.

## Evidence and validation

Artifacts:

- `gemini-cli-baseline-probe.json`: legacy failure and first-stdout timing, not assistant latency.
- `gemini-cli-modern-probe.json`: initial structured-probe diagnostic.
- `gemini-cli-modern-headless-probe.json`: explicit headless probe and account rejection; response empty.
- `tests/test_gemini_cli_streaming.py`: actual owned fake subprocesses exercise incremental UTF-8 text, filtered events, errors, cancellation/early-close cleanup, stdin deadlines and system-policy preservation. They do not invoke customer tools.

Focused CLI tests: **12 passed**. Full isolated software suite: **983 passed, 13 pre-existing failures, 39 skipped, one device test deselected**. Changed-file Ruff, configured Pyright and compilation passed. A broader optional strict check of this legacy adapter also exposes third-party/base typing limitations; it is not claimed as a fully strict-typed module. Frozen WhatsApp verification still fails on the previously changed two frozen files, untouched here.

The refreshed Studio is healthy and idle, still using `antigravity_live` STT, `antigravity_gemini` LLM and Edge TTS. Activation/read-back evidence: `runtime-gemini-stream-refresh-result.json` and `runtime-gemini-stream-health.json`.

Primary references: [CLI headless event implementation](https://github.com/google-gemini/gemini-cli/blob/v0.39.1/packages/cli/src/nonInteractiveCli.ts), [configuration](https://github.com/google-gemini/gemini-cli/blob/v0.39.1/docs/reference/configuration.md), [settings schema](https://github.com/google-gemini/gemini-cli/blob/v0.39.1/packages/cli/src/config/settingsSchema.ts). Context7 supplied current documentation; installed package source was inspected because the system CLI is much older.

## Remaining goal

The CLI alternative is currently blocked by account eligibility, not by the PhoneAgent streaming parser. The optional direct-Gemini API-key question remains pending. The overall goal remains active because other pipeline/release work and broader audio/physical-call qualification remain incomplete. No human-quality or latency win is claimed from a probe that produced no assistant response.
