# PhoneAgent Cascade Program — Durable Execution State

This file is the restart-safe checkpoint for the program. The canonical scope and acceptance gates
remain in `docs/UNIVERSAL_CASCADE_PLATFORM_BACKLOG.md`.

## Control

| Field | Value |
| --- | --- |
| Updated UTC | 2026-09-05T16:21:39Z |
| Program objective | Cascade-only universal Agent Operating System with preserved GSM/Android media |
| User execution directive | Continue until every backlog milestone and final completion gate passes |
| Goal controller observation | Active universal-runtime hardening after a product-coupling regression |
| Active milestone | Milestone 18 — Reference agents, ecosystem, and migration |
| Active item | M18-11 — Neutral MCP package-owned startup |
| Item state | Implemented, MCP-migrated, regression-tested, and active; physical call qualification remains separate |

## Last verified evidence

- `M18-11` removed all named product/company/persona defaults from executable `ai_bridge` sources,
  added a fail-closed neutral package, allowed package-owned identity ID replacement, and added MCP
  deletion of retired user skills. The active MCP deployment is
  `dep_9cd7bce865a614f90190cd8c`; Studio PID 19438 activated healthy/idle with `PhoneAgent`,
  `general_conversation`, bridge STT/LLM, auto-answer off, and business integrations disabled.
  A later read-only check observed an externally initiated active call using this neutral package;
  the migration did not place or interrupt it.
  The active 10,405-character prompt contains none of the retired markers. Full suite: 1,212
  passed plus the existing protected WhatsApp manifest failure. Evidence:
  `reports/quality/2026-09-05-universal-mcp-package.md`.

- Production core is healthy on immutable local image ID
  `sha256:082c9bfcfd3b87cfdd02ab53c3dd1ffca61d8a07fe1cb433106c93753e1c26fb`.
- Control-plane and warm-worker read-back both select Cascade with Whisper Turbo, the EryriLabs
  PhoneLLM Ollama model, and Kokoro TTS.
- The installed Android update APK is version `1.0.0`, SHA-256
  `9fd917778f44efd46d3ac3fd1ac90d3b4773547c0614989af3d3595e12051d55`, and remains an updated
  privileged system application.
- The latest completed GSM media session recorded zero input/output drops, zero sequence gaps,
  zero phone starvation/concealment/underruns, and completed phone playout acknowledgements.
- Full evidence: `reports/baselines/2026-09-02-gsm-cascade-baseline.json` and
  `reports/baselines/2026-09-02-gsm-cascade-baseline.md`.
- `M0-02` added a synthetic-only protected corpus with nine scenarios, all ten required coverage
  categories, 28 hash-listed artifacts, byte-identical regeneration, and two passing integrity and
  privacy regressions. Manifest: `qualification/corpus/v1/manifest.json`.
- `M0-03` fixed the Studio default-executor shutdown leak. The updated non-device suite exited with
  837 passed, 40 skipped, 4 deselected in 56.68 seconds; configured Ruff passes. Full evidence:
  `reports/quality/2026-09-02-m0-03-quality-gate.md`.
- `M0-04` selected strict Pyright with a bundled Node runtime and ratcheted four authoritative
  platform boundaries to zero diagnostics. Ruff, lock validation, 40 focused tests, and the full
  877-test non-device gate pass. Full evidence:
  `reports/quality/2026-09-02-m0-04-static-typing.md`.
- `M0-05` added ten pinned CI jobs backed by one local stage runner. Quality, unit, integration,
  package, Android protocol, security, licence/SBOM, container contract, and eval commands pass;
  device qualification fails closed unless manually authorized. The combined gate reports 844
  passed, 40 skipped, and 4 device tests deselected. Full evidence:
  `reports/quality/2026-09-02-m0-05-ci-stages.md`.
- `M0-06` established nine indexed ADRs with explicit invariants, current drift, migration,
  rollback, verification, and supersession. Structural governance tests and the 891-selected-test
  non-device gate pass. Full evidence:
  `reports/architecture/2026-09-02-m0-06-architecture-decisions.md`.
- `M0-07` established exact Python/container/system/model graphs, commercial licence decisions,
  CVE/SBOM/licence automation, an owned update cadence, and a reproducible CUDA runtime. The final
  image `sha256:4ff4350b3c252ac182240d9f57a87544ecf139bae9890ba9be4c4bed7a86a386`
  passed GPU Whisper and Kokoro qualification, control-plane startup, strict quality, and 856
  non-device tests. Full evidence:
  `reports/quality/2026-09-02-m0-07-dependency-governance.md`.
- `M0-08` added a packaged, fail-closed feature-control registry. CI validates four temporary
  flags, one bounded S2S transition, sixteen durable controls, emitted telemetry, expiry, rollback,
  and zero alternate-pipeline flags. The final full gate reports 825 passed, 39 skipped, and four
  device tests deselected. Full evidence:
  `reports/quality/2026-09-03-m0-08-feature-flag-governance.md`.
- `M0-09` added schema-v1 sealed release evidence with eight result classes and five qualification
  profiles. The packaged validator rejects missing, weakened, failed, tampered, unredacted, or
  uncommitted-production evidence; its reference development bundle and full 833-test gate pass.
  Full evidence: `reports/quality/2026-09-03-m0-09-release-evidence.md`.
- `M0-10` added packaged deterministic and real Cascade performance replay. The RTX A6000 baseline
  passes STT final (146.183 ms), LLM TTFT (142.703 ms), TTS TTFA (158.187 ms), end-to-end TTFA
  (615.549 ms), and WER targets; 60-turn TTFT drift is an explicit 26.556% M7 failure. The final
  full gate reports 890 passed, 40 skipped, and four device tests deselected. The benchmark also
  exposed and fixed prewarm/live Ollama runner mismatch, and restored warm-host cache ownership.
  Full evidence: `reports/quality/2026-09-03-m0-10-performance-harness.md`.
- The Milestone 0 exit gate is accepted. The final suite terminates with 900 passed, 40 skipped,
  and four device tests deselected; two independent builds are byte-identical (wheel
  `d46f7fae...b01fe`, normalized sdist `7705c27b...f55d`). The six-artifact clean bundle validates
  under manifest `a9335e13...bdfd`, and the machine exit report has a CI-verified detached Ed25519
  signature. The existing authorized GSM trace has zero nominal playout faults. Five simulated
  rollback tests and a live 15-check exact-image no-op drill pass. Full evidence:
  `reports/quality/2026-09-03-m0-exit-gate.md`.
- `M1-01` maps 96 S2S surfaces across 11 owned groups: 52 delete, 21 migrate, 14 rewrite, and nine
  retain as historical-only. The CI validator also locks 18 configuration keys, five persisted
  stores, four dependency bindings, nine `PhoneVoiceAgent` runtime branches, 47 provider/platform
  event names, and all ten shared behaviors M1-02 must re-prove through Cascade. The full gate
  reports 902 passed, 40 skipped, four device tests deselected, passing static/package/security/
  licence checks, and no live mutation. Full evidence:
  `reports/quality/2026-09-03-m1-01-s2s-inventory.md`.
- `M1-02` now maps all ten required Cascade survivor behaviors to 31 executable nodes. The matrix
  hash is `95115a2c...6b9f`, and the repository gate reports 927 passed, 40 skipped, and one real
  device test deselected. Fast-unit, integration, strict typing, lint, package, security,
  licence/SBOM, container, eval, and Android protocol stages pass.
- The silent-phone regression is pinned to remote-link v1 head-of-line blocking: Android completed
  local AudioTrack writes and emitted local ACKs while the shared WAN carrier simultaneously
  stalled playout ACK, control, and PONG traffic. Two candidate calls stopped after 57/60 output
  frames and dropped 442/439 frames after the six-second ACK deadline. An exact M0 rollback then
  delivered 1,046 frames with zero output drops and completed playout status.
- Remote-link v2 now uses one authenticated coordinator and an independently authenticated socket
  per logical stream with fresh 32-byte challenge binding. Linux retains v1 compatibility and the
  APK has explicit v1 fallback. Executable Linux and Android tests prove blocked capture cannot
  block ACK/control traffic.
- The reproducible v2 APK is `7e059cc9...2d3b`, with embedded Android source digest
  `d23bd6d7...e307`. The fresh candidate image is `sha256:aac9aaac...b6861`; production remains on
  verified M0 `sha256:082c9bfc...c26fb`. Full evidence:
  `reports/quality/2026-09-03-m1-02-playout-ack-investigation.md`.
- Android installation is now fail-closed and produces a redacted rollback receipt. Formal device
  qualification requires the exact source digest and remote-link v2 as both supported and actively
  negotiated, preventing the installed v1 APK from accidentally passing the release gate.
- The 2026-09-03 retry call proved v2 phone delivery: 2,943 input frames, 1,498 output frames,
  1,503 playout ACKs, and zero drops, sequence gaps, underruns, starvation, concealment, or flush
  failures. The remote-link timeout counter did not increase.
- That call also reproduced a low-confidence Whisper hallucination and a repeated PhoneLLM repair
  that previously ended in silence. The transcript was marked untrusted for task state. Permanent
  regressions now make exhausted model recovery emit a tracked persona repair, and make Android
  audioserver recovery PID-aware with bounded diagnostics. Focused tests and the Android protocol
  gate pass. Evidence: `reports/quality/2026-09-03-m1-02-retry-recovery-regression.md`.
- Candidate APK rebuilt with source digest
  `01427d6f2ef58fce1e4e0b453ce2fce0c1dd04c77dfeb4a67a17106e651f1831` and SHA-256
  `8c57f02cdb6b4ae9f0368430c94feceb09cf0cc2ceec633fda8beea30f5f7170`; it is not installed yet.
- Matching backend image `phoneagent-cascade:m1-02-retry-0903` is built and immutable as
  `sha256:cfa664f57f91a7c78ebe51e6f5e691d7462456855aad15f8ca213fe02daf3bfa`; it is not deployed.

## Unresolved risks discovered at this checkpoint

- The reliable Whisper wrapper is still named `ParakeetLocalSTTService` and emits
  `source=local_parakeet`; this is misleading observability, not a different active model.
- Legacy logs label Cascade diagnostics as `Voice S2S diagnostic`, which conflicts with the
  Cascade-only target and must disappear during Milestone 1.
- Source defaults now disable speculative and reflex experiments. The qualified live baseline was
  deliberately not redeployed during M0-08, so its effective settings remain baseline evidence
  until a later authorized release qualification.
- The latest call rejected eight stale uplink frames. There were no drops, sequence gaps, playout
  faults, or flush failures, but the counter must remain visible in future comparisons.
- Device verified-boot state is `orange` on a userdebug/test-key GSI; this is a qualification
  environment, not a locked consumer-production build.
- Crawl4AI is unhealthy. It did not affect the GSM voice pipeline, but web-research capabilities
  cannot be considered healthy.
- Pipecat's transitive `nltk==3.10.3` has one unfixed path-security advisory. PhoneAgent does not
  call the affected APIs; the exact exception expires 2026-12-01 and CI fails on drift or expiry.
- Commercial engineering licence review passes, but conditional NVIDIA/LGPL/MPL/model decisions
  still require final product-owner or qualified-counsel approval for the actual distribution.
- The supported A6000 profile exceeds the 15% long-call TTFT drift target at 26.556%. The target is
  unchanged; M7-04 through M7-12 own stable context, budgets, structured summaries, bounded recent
  dialogue, cache metrics, GPU admission, and final 60-minute qualification.
- The bind-mounted Hugging Face cache had drifted to root ownership and made the non-root warm host
  crash. Ownership was restored to UID/GID 1001 and readiness is currently stable, but deployment
  creation must enforce this precondition so a reboot/recreation cannot reintroduce it.
- Mac/ADB is still unreachable on the known routes, so the new APK and corresponding backend image
  are not deployed. The current installed device remains the previously qualified v2d build; no
  claim is made that the new recovery behavior is hardware-proven. A fresh retry of
  `100.73.112.70:22`, `196.119.87.246:22`, `196.119.87.246:2223`, and `105.71.133.53:22`
  found all four unreachable. Production remains on exact M0.

## Exact next action

2026-09-06 current goal supersedes the previous next action: implement all 40 requirements in
`/Users/aziz/.codex/attachments/7944adbf-7d82-4ada-a120-218e6170cec6/goal-objective.md`.
The complete requirement ledger and current evidence are in
`reports/quality/conversation-quality-program.md`. Batch 13 is active in Studio PID 55080,
with 1,419 passing full-suite tests and the existing frozen-media failure. Native MCP verified
resolved runtime/behavior validation and preservation of the user's complete runtime configuration.
Current user configuration is `adk_commercial_sprint` on bridge STT/LLM.
Scoped consent, channel checks, typed receipts, matching-request protection, greeting disclosure
and fact-review records are implemented. WhatsApp credentials/session/task bindings are ready;
no message was sent. Email sender selection and actual material-fact source reviews remain absent.
Package-scoped caller memory, twelve critical tuning roundtrips and replacement/rollback of
package-owned conversation behavior are now implemented and regression-tested. New deployment
snapshots resolve represented defaults before hashing; older unknown defaults cannot claim exact
rollback. The provider-setting inventory's 42 behavior entries are resolved as 41 implemented
roundtrips and one retired no-op energy control. Snapshot version 2 captures the expanded settings.
The eight host paths/addresses now round-trip too, captured by snapshot version 3; their physical
assets/services still require qualification. Seven provider credential fields now have explicit
references and private runtime resolution; Google TTS cannot bypass a disabled reference via the
environment. Snapshot version 5 removes retired controls and preserves original historical package
payloads during status updates. Hashing now uses explicit bookkeeping paths and a separate complete
staging token; task normalization preserves empty allowlists and activation rechecks call state.
Package-level caller memory now reaches the worker; the unused recording flag is retired while
actual per-call recording consent remains intact. Owned memory workers drain/close without revival
after shutdown. Snapshot version 7 also binds thirteen parsed host/session requirements; current
16 kHz/20 ms/25-frame values are preserved. The business bridge is reachable with required apps ready
but disabled. Next: separate caller-invoked business tools from automatic outcome sync, fix caller
binding/read-only semantics, and qualify registration/context tools before enabling them for the task.
Keep campaign/automatic writes off. Then durable receipts, email bridge configuration, remaining
environment ownership, live facts and recorded/physical qualification. See the requirement ledger
and runtime-session-ownership.md for evidence and limits.
The overall goal is active.

The previous neutral-package checkpoint is historical. The operator has since configured an
enterprise package through MCP. Preserve it while correcting the shared runtime and validation;
perform physical call qualification only with an explicitly authorized destination.
