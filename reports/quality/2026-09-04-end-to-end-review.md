# PhoneAgent end-to-end evaluation — 4 September 2026

**Verdict:** This is a substantial, integrated AI telephone appliance with useful operational safeguards, accompanied by an unfinished universal/enterprise platform. This workspace does not currently satisfy its own release gates. Its milestone completion reports materially overstate what the implementation and tests demonstrate.

The review covers the available source snapshot, runtime wiring, Android protocol, conversation policy, tools, memory, Studio, deployment definitions, qualification infrastructure, and automated checks. This directory has no `.git` metadata, so change history and the relationship to a qualified commit cannot be established. Findings apply to this copy; missing files may be a transfer/export problem. No calls were placed, messages sent, devices flashed, or services deployed. Source and configuration were not changed by the review.

**What the system actually does**

The principal execution path is Studio → voice host → Android or WhatsApp channel → Pipecat Cascade → provider-independent policy and tools → synthesized speech → phone playback feedback.

1. `ai_bridge/web_server.py` owns Studio, persisted settings, subprocess orchestration, inbound/outbound coordination, events, and configuration/control APIs.
2. `ai_bridge/phone_voice_agent.py` owns the voice-host lock and per-call lifecycle. It selects `ProductionCallPipeline` directly, prepares providers, attaches media, validates the audio injection route, and coordinates greeting and teardown.
3. `mac_client/`, `ai_bridge/remote_link.py`, and `ai_bridge/pipecat_transport.py` connect Python to authenticated phone control and framed media. Android owns privileged Telecom and audio routing. Generation changes and playback acknowledgements distinguish queued output from current speech.
4. `ai_bridge/production_pipeline.py` assembles STT, LLM, TTS, transcription policy, conversation repair, speculation/reflex behavior, and playback observers.
5. `ai_bridge/agent_policy.py` combines persona, task, caller context, memory, permissions, response evaluation, and completion decisions. `ai_bridge/cascade_tools.py` exposes tools through native function calling or an emitted protocol, grounds arguments, dispatches execution, and reports results.
6. The live memory path uses `LayeredMemoryManager` and identity memory, including JSON persistence and SQLite episodes. It is distinct from the newer standalone `customer_memory.py` demonstration.
7. `ai_bridge/control_plane.py` supplies the active versioned configuration package. The newer `AgentPackageV1` and compiler form a separate implementation that is not imported into the live Studio/voice path.
8. OpenWA supplies messaging; the separate WhatsApp voice channel supplies audio. Frappe integration and web research are additional tool backends, with their own setup and availability requirements.

**Evidence collected in this review**

| Check | Observed result | Meaning |
|---|---|---|
| Default Python suite | 835 passed, 17 failed, 39 skipped, 1 deselected; 61 seconds | Broad coverage exists, but current release gates are red |
| Focused conversation/migration rerun | 66 passed, 3 failed | Those three failures reproduce outside the full suite |
| Repository Ruff check | 82 errors | Includes research/ROM scripts and maintained application code |
| Pyright with explicit project interpreter | 0 errors | Passes only the configured subset of files; this is not whole-project type coverage |
| Frozen WhatsApp verifier | Failed for two files | Current bytes do not match the protected manifest |
| Java/Python protocol codec | Passed golden vector | Supports framing interoperability |
| Java remote-link checks | Interoperability and v2 isolation passed | Supports protocol behavior, not physical audio quality |
| Local Studio status endpoint | HTTP 200 | Confirms local service availability only; runtime provenance was not matched to this source |
| Physical calling, providers, full business stack | Not exercised end to end | No new call-quality, delivery, backend-write, or production-availability claim is justified |

Initial Pyright invocation used the wrong interpreter resolution and reported missing Pipecat imports. Repeating it with `.venv/bin/python` resolved those errors. The passing result above is the correctly configured run.

**Priority 1 — Reopen unsupported milestone completion claims**

`docs/UNIVERSAL_CASCADE_PLATFORM_BACKLOG.md` checks off enterprise identity, persistence, scale, commercial readiness, pilots, and launch evidence. `reports/quality/2026-09-03-m19-exit-gate.md` claims the transformation is complete and has no unverified milestones. The source does not substantiate that breadth:

| Claimed capability | Implementation observed | Missing production evidence/functionality |
|---|---|---|
| Universal structured runtime | `universal_runtime.py` returns a fixed acknowledgement of the input | Live pipeline integration and actual structured model planning |
| Durable workflows | `durable_workflows.py` keeps records in a dictionary | Persistent execution, restart recovery, activities, retries, and external handoff completion |
| Enterprise security | `enterprise_security.py` provides a small role switch and in-memory hash chain | Live request enforcement, identity integration, persisted tenant isolation and independently verified audit controls |
| Worker orchestration | `release_orchestration.py` allocates from an in-memory dictionary | Actual workers, lease expiry, dispatch, crash recovery and distributed ownership |
| Commercial platform | `commercial_readiness.py` increments in-memory counters | Runtime metering integration, durable billing records, onboarding and commercial lifecycle |
| Capability routing | `model_router.py` defines profiles and context helpers | Integration of these helpers into live model selection and bounded conversation state |

Searches found these manager classes defined in their modules and exercised by tests, without integration into the running application. For example, M12 evidence explicitly claims asynchronous execution and context persistence, while the engine consists of synchronous dictionary updates. A unit test of those updates does not prove a durable workflow.

Reclassify each milestone as specified, implemented, integrated, or qualified. Require a concrete runtime consumer and scenario-level evidence before marking it complete. Preserve genuine existing functionality: the older control plane, memory and policy implementations are substantial and should not be confused with these demonstrations.

**Priority 1 — Repair the current release baseline**

The 17 suite failures divide into:

- Two corpus integrity tests and four performance tests fail because `qualification/corpus/v1/audio/` is absent.
- Three CI-contract tests fail because `.github/workflows/ci.yml` is absent.
- Frozen WhatsApp verification detects changes in `ai_bridge/whatsapp_client.py` and `ai_bridge/whatsapp_phone_client.py`.
- The Android lifecycle test expects a single physical track-start attempt, but `DigitalAudioBridge.java` now sets `TRACK_START_ATTEMPTS = 3`.
- Two conversation-repair tests fail: exhausted recovery emits two fallback lines where the contract expects one; a retry instruction remains in history where the test expects it removed. The latter reflects an explicit cache-preservation choice in source, so the intended behavior needs adjudication rather than blindly changing the test.
- A saved-settings migration test fails. Its warning identifies a provider/aggregation incompatibility; validation rejects the settings before restoring the custom prompt. This demonstrates a coupled-settings migration problem in the tested configuration, not universal loss of all saved settings.
- The feature-flag registry does not declare `PHONE_AGENT_SMART_TURN_ENABLED`.
- The S2S inventory does not classify new research/provider surfaces.
- A persona prompt assertion fails. The generated prompt includes an installed identity version, so test isolation and identity precedence need investigation before calling this a production regression.

Restore missing inputs from a known source, reconcile behavioral changes against their contracts, and rerun qualification. Do not regenerate protected hashes merely to make checks green. The Android source itself documents limited native audio-track capacity; increasing retry count warrants physical-device evidence.

**Priority 1 — Align deployment with its trust boundary**

The default application is designed around local operation. However, `compose.production.yaml` switches Studio to all interfaces with external access enabled. The Studio middleware implements Host/Origin checks; this is not a general identity/session boundary for every UI route. Use loopback deployment or authenticated ingress plus explicit application authorization before network exposure. This is a source/configuration finding; external reachability was not probed.

The same Compose file contains fallback service credentials. Require supplied secrets and fail configuration when they are absent. Some infrastructure images use mutable tags while other components are digest pinned. Apply one reproducibility policy across the supported deployment.

The top-level production Compose file provisions database/cache/queue services but no Frappe application frontend, backend, workers or scheduler, although the core points at a Frappe URL. The separate `integrations/business_suite/compose.yaml` contains the fuller business deployment. The top-level installer must clearly provision or require that stack; launching its Compose file alone does not establish a complete CRM/ERP product.

**Priority 2 — Reduce architectural duplication and domain coupling**

Studio has approximately 4,215 Python lines and 4,750 lines of HTML/JavaScript. The policy module has 1,576 lines and the pipeline 1,386. These are central change bottlenecks. Extract application services and UI modules along stable ownership boundaries while preserving existing behavior tests.

There are competing package, memory, routing, and runtime abstractions. Choose the authoritative live interfaces, migrate consumers, then remove or explicitly label experimental replacements. The live policy also includes product-specific plan names and price vocabulary in response-quality heuristics. Move those into task-specific configuration before claiming broad domain neutrality.

Several current lint findings are meaningful: blocking subprocess work inside asynchronous provider code and an unretained asynchronous task deserve review for audio latency and lifecycle behavior. Other findings are import placement or formatting. Prioritize runtime effects rather than treating all 82 findings equally.

**Priority 2 — Make deployment and evaluation reproducible**

The workspace includes roughly 7.4 GB of stock ROM material, research scripts, compiled artifacts, and firmware utilities. `.dockerignore` does not exclude the ROM directory, while the Dockerfile copies the project tree. Separate application sources from firmware/research assets to bound build context and resulting image contents.

The project has strong foundations here: pinned Python dependencies, a lockfile, shared CI stages, release evidence schemas, signing support, device qualification, and rollback tooling. They need to operate on a complete, attributable source tree. This review did not rebuild the wheel/container, reinstall the application, or perform a new rollback drill.

The performance CI profile is explicitly synthetic. Its timings establish harness contracts, not STT/LLM/TTS or phone latency. The real local-provider profile is separate, and the absent corpus prevents even the current default harness tests from completing. Measure actual endpoint latency, first audible response, interruption flush, long-call drift, disconnect reliability and verified external actions on the declared hardware/provider combination.

README, ADR transition notes and the bundled master skill still describe removed Realtime paths as available. Update these from the live schemas and entry points, and identify the supported Linux versus macOS deployment profiles explicitly.

**Recommended completion sequence**

1. Recover a complete version-controlled baseline and qualify the changed Android/WhatsApp surfaces.
2. Resolve the 17 failing tests and maintained-code lint findings; record intentional contract changes with evidence.
3. Correct the milestone status and publish an honest supported-capability matrix.
4. Fix deployment authentication/secrets assumptions and choose one complete business-stack installation path.
5. Integrate one end-to-end vertical slice: activate an agent package, conduct a call, execute a caller-bound tool, verify its backend result, persist approved memory, then recover after restart.
6. Prove quality on real providers and hardware before expanding to tenancy, distributed workers, billing, or marketplace features.

**Readiness assessment:** credible engineering foundation for a supervised single-phone appliance; current release candidate blocked by failed gates and unqualified media changes; business integrations require live verification; universal enterprise/SaaS readiness is not established. The highest-value next work is integration and evidence repair, not additional feature scaffolding.
