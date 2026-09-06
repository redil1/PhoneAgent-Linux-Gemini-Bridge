# Conversation quality implementation program

Authoritative objective: `/Users/aziz/.codex/attachments/7944adbf-7d82-4ada-a120-218e6170cec6/goal-objective.md`.
All 40 requirements remain in scope. No human-equivalence claim is established.

## Current audit — 2026-09-06

Previous goal turn: no progress (requested issue list only). Revalidated against live state:
Studio is idle, bridge STT/LLM and Edge TTS are selected, task is `adk_commercial_sprint`.
Latest call `94bb88e5-2629-431c-b092-4030d7e35006` contradicts earlier completion claims.
Only `end_call` was connected; zero action tools ran. Partial “it seems very” was committed.
Dispatch claims bypassed the speech guard. Task regexes interpreted conversational permission
as budget agreement and closing authorization. The 100% score did not detect these failures.

## Requirement ledger

Each entry requires implementation plus evidence at the stated scope. Historical tests alone
do not prove completion. Initial status for all entries: open / current qualification pending.

| ID | Requirement | Completion evidence required |
| --- | --- | --- |
| 1 | Premature turn completion | EN/FR paused-thought replay, no premature substantive answer |
| 2 | Smart Turn errors | Acoustic/text/confidence disagreement and continuation tests |
| 3 | Adaptive pause tolerance | Hesitation, slow speech, self-correction recordings |
| 4 | Recognition omissions | Meaning-changing negation/word omission corpus |
| 5 | Unclear-speech recovery | Uncertain recognition requests repetition without sales advance |
| 6 | Reliable interruptions | Timestamped flush, stale generation suppression, correction answer |
| 7 | Contextual consent | Separate continuation, interest, budget, messaging, purchase evidence |
| 8 | Task-state accuracy | Contextual slot tests and real-call replay |
| 9 | Natural dialogue | Human-reviewed long conversations and concise task behavior |
| 10 | Accurate identity | Package-specific greeting/disclosure and interruption tests |
| 11 | Grounded product claims | Provenance for material claims and missing-evidence rejection |
| 12 | Speech normalization | EN/FR amounts, dates, names, email/URL integrity and audio review |
| 13 | Language handling | Mixed EN/FR recorded dialogue, accents and meaning preservation |
| 14 | Voice quality | Pronunciation/prosody/pacing listening evaluation |
| 15 | Latency consistency | Stage distributions and slow-path/failure performance |
| 16 | Bridge streaming limitation | Supported bridge source/contract investigation and measurements |
| 17 | Safe speculative responses | Meaning mismatch invalidation and zero speculative actions |
| 18 | MCP package completeness | Every effective setting round-trips, activates and rolls back |
| 19 | Configuration contradictions | Promised actions without tools cannot validate/activate |
| 20 | Tool readiness | Auth, health, task assignment and permission probes |
| 21 | WhatsApp capability | Bound recipient/session readiness and authorized receipt trial |
| 22 | Email capability | Configured real sender and authorized receipt trial |
| 23 | Channel-specific availability | Independent WhatsApp/email/SMS/CRM speech and tool permissions |
| 24 | Correct tool selection | EN/FR intent-to-channel/task execution scenarios |
| 25 | Argument grounding | Recipient/content/identifier/date validation and malformed input tests |
| 26 | Verified action claims | Unsupported dispatch/sending/registration claims rejected |
| 27 | Action-status tracking | Requested/executing/accepted/delivered/read/failed/unknown lifecycle |
| 28 | Duplicate prevention | Retry/repeated-request idempotency under ambiguous failures |
| 29 | Tool cancellation | Pre-dispatch interruption fence and in-flight reconciliation |
| 30 | Tool responsiveness | Bounded latency and truthful progress speech |
| 31 | Failure recovery | Available alternatives only; bounded useful retry behavior |
| 32 | Tool-result interpretation | Exact evidence-based claims and no protocol leakage |
| 33 | Package isolation | Switch unrelated packages without old knowledge/persona/memory leakage |
| 34 | Playback-aware memory | Delivered speech and verified actions only; corrections preserved |
| 35 | Graceful call completion | Goodbye acknowledged before hangup, complete end-marker lifecycle |
| 36 | Meaningful quality scoring | Proven task/action evidence; no misleading success score |
| 37 | End-to-end observability | Audio/turn/model/tool/TTS/playout event correlation |
| 38 | Representative testing | Recorded bilingual/noise/echo/accent/long-dialogue suite |
| 39 | Real integration qualification | Authorized test destinations and independently checked receipts |
| 40 | Release qualification | Frozen-media provenance, upgrade/restart/package/rollback evidence |

## Execution order

First reproduce and repair latest-call failures in turn authority and unsupported action claims;
then contextual consent and speech normalization; then capability contracts, MCP readiness,
action lifecycle and package isolation. Complete latency, perception, recorded and integration
qualification against all ledger entries. Keep bridge providers selected. Never rewrite the
frozen-media manifest merely to make a check pass.

Real external sends/calls require a specifically authorized test destination. This does not block
local implementation, deterministic queue tests, simulated effects, or read-only backend health.

## Batch 1 — implemented and activated, not overall completion

Evidence: `realtime-program/call-0009-batch1.json`; reproducible synthetic bridge STT harness:
`realtime-program/replay_unfinished_evaluations.py`.

- Requirements 1/2/3: unfinished EN/FR degree modifiers retain incomplete-turn patience even
  with final ASR punctuation or complete acoustic verdicts. An exhausted incomplete evaluation
  produces a short continuation request instead of budget acceptance. Four bridge STT replays
  spanning 900–1800 ms pauses produced no premature commit. This is synthetic evidence, not
  broad recorded/human qualification. French ASR changed “très” to “vrai” in one sample;
  this recognition error remains open under requirements 4/13/38.
- Requirements 12/25: grouped currency amounts and French tens/hundreds now normalize correctly.
  Literal email and URL identifiers survive generic punctuation/number rewriting. Dates, account
  references, locale ambiguities, signed values and perceptual pronunciation still need broader
  qualification; this does not establish the whole speech normalization requirement.
- Requirements 26/36: present-progressive and paraphrased dispatch/registration assertions now
  require evidence; questions, negation and non-action speech have negative controls. Standalone
  text evaluation catches these paraphrases. UI replaces Fidelity percentages with limited text
  checks and separate verified-action evidence. General semantic claim recognition, independent
  fact scoring and channel-specific receipts remain incomplete.
- Full suite: 1,254 passed; 39 skipped; one device test deselected; existing frozen-client
  integrity failure remains. Ruff, configured Pyright, compile, JS syntax and governance pass.
- Studio PID 36766 activated healthy and idle with current user package `adk_commercial_sprint`
  and bridge STT/LLM. No package/business configuration was overwritten and no external action
  was sent. Physical qualification and source rollback of this batch are not claimed.

Next concrete work: requirements 7/8/18/19/20/23. Replace unscoped slot keyword acceptance
with explicit contextual evidence; reject task promises whose channel capabilities cannot be
executed. Prove “go ahead” after a listening question cannot accept a price or authorize a send,
while a direct send request or a yes to the actually delivered send proposal does authorize
exactly that action. Keep original 40-item scope active and preserve live user package edits.

## Batch 2 — scoped consent and channel readiness

Previous goal turn classification: progress (batch 1 changed source, activated a tested runtime
and produced new real-provider replay evidence). All 40 original requirements remain in scope.

Implemented and checked on 2026-09-06:

- Requirements 7/8: task slots support explicit `consent_scope`. Legacy named consent fields
  receive scoped semantics; unscoped regexes cannot capture a bare affirmative as business data.
  The active task declares continuation, budget and messaging scopes. A short affirmative is
  bound to one acknowledged, fully delivered proposal, consumed by one caller turn. Playing,
  interrupted, unverified and stale proposals cannot authorize a send. Direct requests remain
  supported. A question asking for information, such as “What is your budget?”, cannot turn a yes
  into budget acceptance. Consent is visible in live context and emitted as scoped evidence.
- Requirements 18/19/20/23: package validation now respects task assignment, treats missing
  declared tools as failures, and checks typed `required_capabilities` plus positive messaging
  promises. Custom managed tools declare `capabilities` independently of their names; sending
  requires a writable tool. Metadata reaches the runtime catalog and consent boundary. An email
  promise cannot use WhatsApp availability as proof that email is possible.
- The actual user package failed validation for missing `email.send` and `whatsapp.send`, matching
  the call failure. Its task had no authorized action tools. This was inspected without replacing
  the user's product or persona.
- Requirements 20/21: OpenWA health was good, but the saved client key was empty and session
  requests received HTTP 401. The existing local administrator credential verified the paired
  session was ready. The existing private provisioning endpoint created a session-scoped operator
  key; only presence/status were printed. Native MCP enabled the integration and assigned the
  text sender and delivery-status tool to the active task. Native MCP health then confirmed ready.
  Automatic replies to unsolicited incoming chat messages remain disabled. No message was sent.
- A native MCP call exposed a separate dispatcher defect: integration configuration omitted the
  `config` envelope required by Studio. The dispatcher is repaired. Installed MCP clients use an
  older wheel, so Studio accepts both validated complete configs and envelopes. A real native MCP
  call with its standard public schema now succeeds. Empty and unknown fields remain rejected.
- A read-only runtime catalog probe connected exactly `end_call`,
  `whatsapp_send_text_current_customer` and `whatsapp_last_delivery_status`; channel availability
  was WhatsApp and initial send consent was false.

Validation: full isolated suite 1,297 passed, 39 skipped, one device test deselected; the same
frozen WhatsApp integrity failure remains. After that run, one additional unverified-playback
negative control was added; the full contextual-consent file passes 34 tests. Focused MCP,
package, HTTP compatibility and consent checks pass. Ruff, configured Pyright, compile and
governance pass. Source active in Studio PID 38391, healthy/idle on bridge STT/LLM and Edge TTS.

Limits and next work:

- These are bounded EN/FR intent rules and deterministic tests, not proof of universally correct
  semantic understanding or human conversation quality. More direct requests, negation, channels,
  confirmation phrasing and interruption timing need recorded and full-pipeline evaluation.
- Consent states do not prove task completion. Typed action receipts, per-channel result evidence,
  deduplication, cancellation reconciliation and verified CRM/email completion remain open.
- No email sender exists in the active package. A question requesting the existing email bridge
  or mailbox is pending; do not fabricate credentials or a sender. Continue independent work.
- The active package's positive email promises still fail complete-package validation. Incremental
  MCP changes repaired WhatsApp and slot scopes; no complete package reactivation is claimed.
- No actual message/call/CRM write or physical qualification was initiated. Real receipt trials
  require an authorized test destination; runtime/channel readiness is not delivery proof.
- Installed MCP source still differs from the checkout. Server compatibility was verified with
  the old client, but full installed-wheel migration and rollback remain part of requirement 40.

Next concrete action: implement typed action receipts and channel-specific result claims
(requirements 26–32), remove stale declarative-state authority, then complete email provisioning
when its bridge is known. Also audit all MCP mutation paths for complete-package validation and
behavior-setting roundtrip coverage. Preserve all remaining requirements and current user edits.

## Batch 3 — typed receipts and duplicate protection

Previous goal turn classification: progress (scoped consent and channel checks were activated;
real WhatsApp authentication and native MCP configuration were repaired). The 40-item objective
remains active and is not complete.

Implemented for requirements 26–32/34/37:

- Added typed receipt states: requested, executing, accepted, completed, delivered, read, failed,
  unknown and cancelled. Messaging needs an acceptance flag and a bounded, exact backend message
  ID; generic success text is not proof. Structured MCP receipts are supported and error flags
  take precedence. Known CRM operations require their documented verified/mutation/id fields.
  Quotation and order drafts prove only drafting, never booking, payment or completed purchase.
- The production pipeline now uses a private persistent SQLite journal (30-day retention),
  storing hashes and bounded receipts rather than argument/result bodies. Reservations are atomic.
  Matching call/caller/tool/argument requests do not dispatch twice, including concurrent requests
  or after reopening the journal. Unknown/executing outcomes are not automatically retried.
  Explicit direct resend intent creates one new logical operation. New user tools default to
  writable; their decorator can explicitly declare read-only lookup behavior.
- Direct async handlers are now awaited. Direct synchronous handlers run outside the event loop
  with bounded waiting. A timeout/cancellation after submission is unknown because the backend
  or worker thread may already have acted. Pre-dispatch authority and catalog binding are checked
  after journal notifications; cancelled-before-submission requests can be tried again.
- Typed receipts feed speech authorization per channel. Failure in one channel cannot borrow
  success from another, and partial success within one channel cannot support an aggregate send
  claim. Known-call WhatsApp status lookups can confirm acceptance, delivery or reading. Stale
  responses do not authorize current-turn claims. An older acceptance replay cannot downgrade
  an already verified read/delivery state.
- Action-state events provide operation IDs and receipt states. Journal failures return a
  bounded unknown-outcome result instead of dispatching untracked actions or crashing the call.

Evidence: `tests/test_action_receipts.py` (18 passing receipt/concurrency/cancellation/restart/
thread-timeout/channel tests), plus 126 passing focused policy/tool/consent tests. The latest full
isolated run passed 1,315 tests with 39 skipped and one device test deselected; the sole failure
remains the existing frozen WhatsApp source mismatch. Additional structured-receipt and status
handling changes were checked in the focused suites. Ruff, configured Pyright, compile and
governance pass. A production-graph startup probe found both WhatsApp tools and a private journal;
no external action was executed. Studio PID 39739 is active, healthy and idle on the same bridge
STT/LLM, Edge TTS and user task.

Remaining limits (not completion claims): backend-side idempotency is not established; matching
canonical requests do not cover semantically equivalent reworded message bodies. Delivery
updates after a process restart still need backend reconciliation and durable event association.
In-flight worker threads cannot be forcibly undone. Generic/new integrations need explicit receipt
adapters and caller/argument binding qualification. Real email provisioning remains pending the
mailbox/bridge answer; no real send trial or physical call qualification has been performed.

Next work: fix identity disclosure in configured greetings and require evidence for material
product claims (requirements 10/11/19), then continue package isolation, backend reconciliation,
email integration and recorded dialogue/latency qualification. The original 40 requirements remain
the completion audit; no subset has replaced them.

## Batch 4 — truthful openings and material-fact review records

Previous goal turn classification: progress (typed receipts, journalled matching-request protection
and action execution fixes were implemented, tested and activated). All 40 requirements remain.

- Requirement 10: configured greetings now disclose the configured AI identity before TTS.
  A short appositive is inserted into a recognized introduction while preserving the caller
  address, company and purpose. Contradictory/different self-identities use the approved disclosure.
  AI product mentions do not count as self-disclosure. The greeting finalization path enforces this
  even though it does not pass through streamed-sentence policy. Source configuration is preserved.
- Requirements 11/18/19: `task.knowledge_evidence` binds material facts to an exact value hash,
  source kind/reference, reviewer, review timestamp and optional expiry. Missing, malformed,
  changed, future-dated and expired records fail package validation. Certification claims require
  document/backend attribution rather than operator pricing authority alone. The MCP control
  schema exposes the new evidence and consent/capability extension shapes.
- The compiler marks unreviewed configuration explicitly. The runtime withholds positive material
  assertions in categories containing unreviewed configured facts; direct greeting/finalization
  cannot bypass that guard. Identity and unclear-audio repair retain their earlier priority.
- Actual-package audit found five unreviewed material entries: investment, timeline, scope_summary,
  guarantee and approved_claims. Price and SOC2 assertions were withheld in the policy probe;
  the actual configured opening gained AI disclosure and retained its purpose. No evidence records
  were fabricated and no product/persona configuration was overwritten.

Validation: full isolated suite 1,327 passed, 39 skipped, one device test deselected; the sole
failure remains the pre-existing frozen WhatsApp client mismatch. The later MCP schema exposure
passed 25 focused tests. Ruff, configured Pyright, compilation, feature controls and migration
inventory pass. Studio PID 41067 activated healthy and idle on the unchanged bridge STT/LLM,
Edge TTS and active user task. Native MCP confirms evidence-schema availability and correctly
reports that the active package still fails material-review and missing-email capability checks.

Limits: these records are review attestations. Validation does not fetch source documents or
independently verify that a certification exists. The legacy-runtime claim guard is category-based;
it is not a general semantic entailment checker, and it does not yet bind arbitrary live catalog
lookup results to spoken assertions. New claims outside configured categories and reworded values
still require broader qualification. The current package needs real source reviews for its material
claims and a configured email sender. No call, message or CRM action was executed in this batch.

Next concrete work: package-scoped caller memory and prompt isolation (33/34), complete runtime
setting roundtrips (18), then live backend fact binding, reconciliation, email and recorded/perceptual
conversation tests. All remaining requirements and the original completion audit stay active.

## Batch 5 — isolated caller memory and package-owned conversation behavior

Previous visible goal turn classification: no progress (the requested issue list only). This
continuation read the full objective, revalidated the code and the running test handle, and made
the following authoritative changes. The original 40 requirements remain the completion scope.

- Requirements 33/34: caller JSON and semantic memory now use a namespace derived from package ID,
  identity core and task objective/knowledge. Legacy data remains on disk but is not silently
  imported. Product/persona/package changes isolate data; voice-only changes retain it. The semantic
  database group key also includes the namespace, including when a shared database is configured.
  Studio memory/evaluation reads and call-worker writes resolve the same namespace. Child processes
  receive the configured control-plane root and task directory rather than falling back to another
  installation's defaults. Tests cover legacy preservation, sibling scopes, shared database search
  isolation and Studio/worker agreement.
- Requirement 18: twelve critical tuning controls now validate and round-trip through public
  settings, persistence, AgentPackage, and the child environment: Smart Turn enablement/threshold,
  conversation repair, local corroboration, five bridge endpoint/stability values, and Edge voice
  rate/volume/pitch. Worker readiness reports their parsed values, and Studio rejects mismatches.
  Tests include malformed booleans, nondefault values, persisted reload and stale worker handshakes.
- Requirements 9/13/18/33: AgentPackage includes typed `behavior` for traits, communication and
  human-conversation instructions/repair phrases. Export captures effective defaults. Activation
  replaces prior behavior rather than merging it; failed activation restores both disk and memory.
  Legacy packages without behavior use neutral shipped defaults when activated. Editing behavior
  after staging invalidates activation. Tests switch unrelated wordings, instantiate a fresh
  compiler, restore an older recorded package and inject a failure after the behavior write.
  Shared defaults no longer contain the TV-specific conversational examples found in this audit,
  and the language instruction explicitly accommodates caller switching between English and French.

Validation: 1,341 tests passed, 39 skipped, one device test deselected. The only full-suite failure
is the unchanged frozen WhatsApp mismatch in `whatsapp_client.py` and `whatsapp_phone_client.py`.
Ruff, configured Pyright, compilation, feature controls and migration inventory passed. The final
frozen verifier still names those same two files; its manifest was not modified.

Activated the tested checkout in idle Studio PID 42530 (previous PID 41067). Native MCP confirms
the new behavior schema and twelve tuning controls. Exact comparison preserved package ID,
identity, task, tools, OpenWA, business/research config, skills and memory blocks. Bridge STT/LLM,
Edge TTS, user-selected voice and speculation settings are unchanged. The OpenWA read-only probe
reports the configured session ready. No call, message or CRM mutation was performed. No new
business package was activated merely to demonstrate the schema.

Activation source hashes: `ai_bridge/control_plane.py`
`7fcb244675818333f127188114ba57cb87ce6c692c80dde3d77cadfaca398416`;
`ai_bridge/web_server.py`
`8d6cab1d1122519ba488228afceeac834e12bfde82b711bcf1e269d8563a95a2`;
`ai_bridge/personality/persona_compiler.py`
`2f2f5e34206ae30f4eb951ac238496fc75959c939ee5f6520883d2a775484639`.

Limits: these are focused configuration and isolation improvements, not completion of 18/33/34.
Every remaining ProviderConfig/environment setting still needs an inventory and package treatment.
Older packages can omit optional tuning and inherit current values; partial behavior uses shipped
defaults. Fully resolved immutable snapshots, old-package migration and cross-version rollback
remain unqualified. Existing operator persona data is preserved on upgrade until explicit package
activation. Voice-only memory retention does not mean every semantic identity change is classified.
Perceptual audio, backend-reconciled actions and real destination qualification remain outstanding.
The current business package still needs material-fact reviews and a configured email sender.

Next: finish immutable package default resolution and the remaining effective-setting inventory;
then backend action reconciliation, live fact binding, email and recorded/physical qualification.
Preserve current operator state and bridge-only providers throughout. Keep all 40 requirements active.

## Batch 6 — recorded defaults and honest legacy rollback boundaries

Previous goal turn classification: progress (memory/behavior isolation and tuning controls were
implemented, tested and activated). This continuation reread the complete objective and inspected
the authoritative source, deployment storage and running Studio before making changes.

- Requirements 18/33/40: validate/stage now resolves omitted optional conversation tuning and
  inherited behavior before validation and package hashing. Validation returns the resolved runtime
  and behavior for review. Stage persists them with `snapshot_version: 1`; storage itself refuses
  unresolved packages or validation referring to a different package hash.
- `behavior.defaults_resolved` makes persisted conversation wording independent of future shipped
  defaults. Resolved records must contain every currently compiled trait and speaking style.
  Activating and reloading that behavior does not silently merge another version of shipped wording.
- Legacy records remain readable. A record without the snapshot marker cannot be directly activated
  or described as an exact rollback: the API explains that historic defaults were not recorded and
  requires review/restaging. Existing active records are not automatically rewritten or activated.
- The new HTTP regression validates and stages an older payload with omitted tuning/behavior,
  changes the shipped defaults, activates it, constructs a fresh compiler, modifies current timing
  and behavior, then calls rollback. Recorded timing and wording remain exact. Additional tests
  reject mismatched validation and legacy rollback while retaining legacy read access.

`tools/audit_package_setting_coverage.py` is a read-only AST inventory, not a completion gate.
It loads field names without reading configuration values or credentials. Of 99 ProviderConfig
fields, 40 occur in the package runtime schema. The other 59 comprise 42 behavior settings,
8 host paths/endpoints, 7 credential fields requiring reference policy, and 2 obsolete fields.
Presence in the schema does not prove a worker roundtrip. RuntimeConfig and environment-only
controls remain outside this inventory and must also be audited. The migration inventory explicitly
classifies the new scanner as read-only audit work: 111 surfaces/15 groups, with detection unchanged.

Validation: final full isolated suite 1,344 passed, 39 skipped, one device test deselected. The sole
failure remains the two pre-existing frozen WhatsApp source mismatches. Ruff, configured Pyright,
compilation, feature controls and migration inventory passed. The WhatsApp freeze manifest and
protected client sources were not changed in this batch.

Activated tested source in idle Studio PID 43270, replacing PID 42530. Native MCP confirms resolved
runtime/behavior in validation. Exact read-back comparison preserved package ID, identity, task,
tools, integrations, skills, approved memory blocks and runtime settings. Current bridge STT/LLM,
Edge TTS voice and user speculation preferences are unchanged. The active business package still
correctly fails missing material-fact review and email capability checks. No business package was
activated for this probe, and no call, message, booking or CRM mutation was executed.

Activation hashes: `ai_bridge/control_plane.py`
`a96f3a66375f9f2f567ed6528f36e860bc15ec3678d319b91797924dfee2ba44`;
`ai_bridge/web_server.py`
`dfdc4d90f4d9a04319c4bdbb6db46530ecaff1b293fea4fa6d97604e6d80cfcc`;
`ai_bridge/personality/persona_compiler.py`
`78055ad86a35a0d5d74bbec8c813b487387a871779f7ceca5c6d696c612bd5c2`.

Limits: snapshot version 1 covers the existing RuntimeControl/ConversationBehavior schema, not the
whole provider configuration, external credential state, source code or model assets. This is not
full release rollback qualification. Older historic defaults cannot be recovered without evidence;
review/restaging is a compatibility boundary, not a reconstruction claim. Environment-only controls,
secret references, tenant/host settings, installed-wheel parity and interrupted deployment recovery
still require work. All original conversation and integration qualification requirements remain.

Next concrete action: use the field inventory to implement the 42 missing behavior-setting
roundtrips, with strict bounds and child-worker agreement; then host/secret profiles and the
RuntimeConfig/environment inventory. Continue backend receipt reconciliation, live fact binding,
email and recorded/physical qualification afterward. The complete 40-item goal remains active.

## Batch 7 — remaining provider behavior controls reach the worker

Previous goal turn classification: progress (recorded defaults and the field-coverage inventory).
This continuation reread the complete objective, inspected parser/consumer paths and verified that
Studio was idle on the current bridge providers before changing source.

- Requirement 18: 41 additional meaningful settings now have bounded typed package fields. They
  cover bridge chunking/context bias, local/Flux endpoints, speculation timing, reflex cooldown,
  model sampling/context/response limits, supported bridge CLI timeouts, TTS buffering/phrase
  limits/timeouts, synthesis parameters and maximum WhatsApp call duration. Settings persistence,
  export, candidate validation, activation and rollback use those same values.
- A central environment mapping transfers all 53 ConversationTuning fields to the worker, including
  nonstandard variable names. Resident-host environment comparison now includes those names too.
  Readiness compares parsed values; context-bias text is hashed rather than included in event logs.
- The initial list contained one no-op: `parakeet_energy_threshold_dbfs` was no longer consumed by
  the local neural speech path. It was removed from ProviderConfig and not retained as a misleading
  package control. Settings requests using it fail clearly. The obsolete environment variable has
  no consumer; neural VAD and the separate SenseVoice gate are unchanged.
- Snapshot version 2 records the expanded tuning set. Earlier records remain readable and can be
  reviewed/restaged, but cannot claim exact current-schema rollback for values they never stored.

Evidence: the new all-settings test passes nondefault values through the public settings API,
persisted reload, package export, actual RuntimeConfig environment parser and PhoneVoiceAgent
readiness event, without contacting providers. Another test activates all values and rolls every
one back to its recorded baseline. Invalid booleans/numbers/enums, infinity, reversed endpoint or
phrase limits, and the retired control reject without mutating settings. Nonstandard environment
changes invalidate the resident signature. Consumer source inspection found a runtime consumer
for every retained new field, including the call-duration accessor; it is not a performance test.

Final isolated suite: 1,355 passed, 39 skipped, one device test deselected. The sole failure remains
the pre-existing frozen WhatsApp mismatch. Ruff, configured Pyright, compilation, feature controls
and migration inventory passed. Protected WhatsApp client sources and freeze manifest are unchanged.

Activated tested source in idle Studio PID 43761 (previous 43270). Native MCP confirms new controls
and their resolved validation output. Exact read-back preserved all previously represented runtime
values plus package ID, identity, task, tools, integrations, skills, approved memory and behavior.
Bridge STT/LLM, Edge TTS and the operator's speculation preference are unchanged. Active package
validation still correctly reports missing material-fact reviews and an email sender. No call,
message, booking or CRM mutation was executed; no business package was activated for the probe.

Activation hashes: `ai_bridge/control_plane.py`
`1741d8d01cdef94e2df7844174e64211e4226c235ebad01e912f928bdecb3c77`;
`ai_bridge/web_server.py`
`e9dc48b41785152af06de7255f1a158edbde205b15a77887d71072f971bb6196`;
`ai_bridge/runtime_config.py`
`e58eb178e3e111053c8158917f9350a1f7fb5fd52aacd515c068339745e1ef59`;
`ai_bridge/phone_voice_agent.py`
`15fbd7a87b411944f07e6f73f8894d2be537d587d984800c5896560307a7a056`.

Remaining scope: the ProviderConfig inventory now has 98 fields, 81 represented in the package,
and 17 still needing treatment (8 host paths/endpoints, 7 credential references, 2 obsolete fields).
This count includes legacy represented fields and is not a completion percentage. RuntimeConfig's
19 fields and other modules' direct environment controls still require a separate ownership audit.
Physical acoustic effect, latency/quality improvement and release rollback remain unqualified.
All 40 original requirements, including real action receipts and conversational evaluation, remain.

Next: host/secret profiles and obsolete-field migration, then RuntimeConfig/environment ownership;
continue backend receipt reconciliation, live facts, email and recorded/physical qualification.

## Batch 8 — host settings and secret-safe validation errors

Previous goal turn classification: progress (41 behavior controls and retirement of one no-op).
This continuation reread the full objective, traced host setting consumers, inspected credential
loading without reading secret values, and verified the running Studio was idle on bridge providers.

- Requirement 18: the eight remaining host fields now validate, persist, export, resolve into a
  version-3 deployment snapshot and reach the worker environment: Codex/Gemini CLI binaries,
  FFmpeg binary, Smart Turn model path and four provider service addresses. Their nonstandard
  environment names participate in resident-host staleness checks. All eight have runtime consumers.
- Service addresses reject credentials, query strings, fragments, unsupported schemes and invalid
  ports. Host paths reject control characters. Pydantic control-plane errors hide input values;
  malformed port parsing also emits a generic error rather than reflecting a possible token.
- Tests activate eight nondefault fixture paths/addresses, parse the actual worker configuration,
  verify environment invalidation and restore every recorded baseline value. Invalid credentials,
  malformed addresses and paths reject without mutation or secret reflection. Fixture programs,
  models and addresses are never executed, loaded or contacted.

Final isolated suite: 1,364 passed, 39 skipped, one device test deselected. The only failure remains
the pre-existing frozen WhatsApp source mismatch. Ruff, configured Pyright, compilation, feature
controls and migration inventory passed. Protected WhatsApp files/manifest are unchanged.

Refreshed the verified idle checkout from Studio PID 43761 to PID 44183, after validating the new
tuning schema against the preserved process environment in memory. Native MCP confirms all eight
fields and exact preservation of previously represented runtime values, identity, task, tools,
integrations, approved memory, skills and conversation behavior. Providers remain bridge STT/LLM
and Edge TTS. The active business package still correctly fails material-fact review and missing
email capability checks. No call, message, business mutation or business package activation occurred.

Activation hashes: `ai_bridge/control_plane.py`
`c460202be63e60bb6269b15046efe2878bc79755d3e2a88db8973eed6b4fbd03`;
`ai_bridge/web_server.py`
`c8e40318b41f4e74fcaa0df9e0bc9642ae34be1a10db2c70c82c2766216f370b`.

Limits: these are validated host-setting values, not a signed host/asset qualification. Executable
existence/version, model digest/format, service reachability and DNS/backend identity are not proven
by shape validation. Changing an endpoint/path does not select or exercise that provider. The
field inventory now represents 89/98 ProviderConfig fields; nine remain (seven credential fields,
two obsolete fields). This is not a completion percentage, and legacy represented fields still
need migration. RuntimeConfig/environment-only controls remain a separate unfinished inventory.

Credential inspection found seven values loaded from process/private-file environment; Google TTS
also has a direct GEMINI_API_KEY fallback in the provider consumer. A correct reference policy must
cover both worker resolution and that consumer fallback, not merely add metadata to package export.
No credential value was read into reports, changed, activated or used for an official API request.

Next: implement explicit provider secret references with private worker resolution, availability
checks and no raw values in package/settings/events; preserve bridge-only selected providers.
Then obsolete configuration migration, RuntimeConfig/environment ownership, backend receipts,
live facts, email and recorded/physical qualification. Every original requirement remains active.

## Batch 9 — explicit provider credential references

Previous goal turn classification: progress (host-setting roundtrips and secret-safe errors).
This continuation reread the full objective, traced private credential loading and the provider
consumer fallback, and verified the active Studio/task/providers before changing source.

- Requirements 18/20/33: `runtime.credential_refs` describes all seven provider credential fields
  using ordered environment-reference lists. Empty lists explicitly disable a credential. Standard
  variables are restricted to the matching provider; custom aliases use `PHONE_AGENT_SECRET_`.
  Raw values, unrelated environment names, duplicate references and malformed shapes reject.
  The MCP JSON Schema exposes the exact seven fields, syntax and default reference order.
- The local runtime resolves private values; packages, settings, deployment records and readiness
  events contain references only. Reference updates propagate to Studio's private ProviderConfig
  and the child worker. Rollback restores the recorded reference list. Snapshot version 4 records
  the references explicitly; older records need review/restaging for current-schema rollback.
- Package validation reports required credential presence for the selected provider without making
  an authentication request. Optional local-provider authentication remains optional. Bridge-only
  selected providers do not require these conventional API credentials.
- Removed Google TTS's direct environment fallback so an explicitly disabled/replaced Google
  credential cannot be bypassed. Default reference order preserves legacy Google/Gemini alias
  behavior when the operator has not overridden it.

Tests cover all seven private resolutions, ordered fallback and disablement, unrelated/cross-provider
reference rejection, JSON Schema parity, missing required credentials, settings persistence,
activation, actual worker parsing/readiness and rollback. Synthetic secrets never appear in exported
packages, persisted settings/deployments, config repr or readiness events. The disabled-Google test
constructs only the existing Edge fallback; it does not call an official API or synthesize speech.

Final isolated suite: 1,378 passed, 39 skipped, one device test deselected. The sole failure remains
the pre-existing frozen WhatsApp source mismatch. Ruff, configured Pyright, compilation, feature
controls and migration inventory passed. Protected WhatsApp sources/manifest were not changed.

Refreshed verified idle Studio from PID 44183 to PID 44932. Native MCP confirms seven reference
fields with reference-only values, exact preservation of all previously represented runtime values
and unchanged identity/task/tools/integrations/skills/memory/behavior. The selected bridge STT/LLM
and Edge TTS remain unchanged. Package validation passes the credential-reference check (no
conventional API credentials required by those selected bridges) and still correctly fails missing
material-fact reviews and email capability. No credential was provisioned and no call, message,
business mutation, official API request or business package activation was performed.

Activation hashes: `ai_bridge/control_plane.py`
`a05eb9eedbe0be8e2580595fef715c5936e94b1fd2f99af12a6ab26b76078c6d`;
`ai_bridge/web_server.py`
`f8a81e9bb4bcb44976f6c0cd4a04fa059e3121db11ce83f1af429cfcd6856189`;
`ai_bridge/runtime_config.py`
`5e951920ba4491a7b9e89e57ba31ea24e99f410c514b7bc4e2adbd060c118608`;
`ai_bridge/provider_credentials.py`
`e196ae29c4f00ecbd5dad9c43e78c19cb7900df878c4da5631cc7e00266896f9`;
`ai_bridge/production_pipeline.py`
`8b485e2bf18876d34bd4563cd48512d379e21a9e459865f9cb3cbfa73f8dc7a1`;
`ai_bridge/phone_voice_agent.py`
`1db1de7818cb9f162efe3eb9dd2aed603257e6e3f084f247168d556b9724aabb`.

Limits: presence is not authentication/authorization proof. References do not freeze historical
secret values or provide a vault/rotation service; process environment takes precedence over the
private file, and same-name rotation may require reload. OpenWA, MCP and business-integration
credential stores remain separate. Their delivery/action qualification is still outstanding.
The structural field audit now reports 90 package fields plus seven private fields with declared
reference bindings, leaving two obsolete fields out of 99 ProviderConfig fields. This is not a
completion percentage or proof that every runtime/environment control is covered.

Next: obsolete configuration migration and RuntimeConfig/environment ownership, then durable
backend receipt reconciliation, live fact binding, email and recorded/physical qualification.
All 40 original requirements remain the completion audit; the overall goal is active.

## Batch 10 — retired configuration and preserved deployment history

Previous goal turn classification: progress (private credential references). This continuation
reread the objective and traced the remaining obsolete fields. They had no current call-pipeline
consumers; their remaining active surfaces were schema, settings, environment and import handling.

- Requirements 18/33/40: removed fourteen retired provider fields, including the twelve previously
  exposed package controls. The published runtime schema advertises only `cascade`. Legacy package
  imports normalize the retired mode and discard the recognized obsolete fields; current settings
  requests attempting to use them reject clearly. Saved settings retain current values during
  migration and persist only current controls. Worker environments drop the retired prefix.
- Snapshot version 5 captures the current schema. Older records remain readable under the existing
  review/restaging boundary. New tests cover every retired control, the published schema, saved
  settings migration, package import and exclusion from worker environment.
- Fixed deployment-history preservation: changing lifecycle metadata now retains the original
  stored package payload and associated hash instead of serializing the migrated read view over it.
  Attempts to replace a stored package reject. Regression coverage includes older identity payloads
  with omitted timestamps; generated read defaults are excluded from the immutability comparison.

Final isolated suite: 1,382 passed, 39 skipped, one device test deselected. The only failure remains
the pre-existing frozen WhatsApp source mismatch. Ruff, configured Pyright, compilation, feature
controls and migration inventory passed. The protected WhatsApp files and manifest are unchanged.

Loaded the cleanup in verified idle Studio and then refreshed the final timestamp-compatibility
fix; current PID is 45889. Native MCP confirms cascade-only schema, retired-field absence, exact
preservation of current runtime values and unchanged identity/task/tools/integrations/skills/memory/
behavior. The active package still correctly fails material-fact review and missing email capability.
Bridge STT/LLM and Edge TTS are unchanged. No call, message, business mutation, official API request
or business package activation was performed.

Final activation hashes: `ai_bridge/control_plane.py`
`1fdd98b9d4b35ab1be5f61add43066876ece55767f43fc03cd04d47d4006f052`;
`ai_bridge/web_server.py`
`54f730eda6db8f36843d982f800140a1fed3aeb9af78a5e870c2a49c517f3094`;
`ai_bridge/runtime_config.py`
`88c7f00e66e5fc4d0001b5962acab9532730d398bdbe5ca341c18c9bcfd624a5`.

The structural provider inventory now reports 85 fields: 78 represented in the package and seven
private fields with reference bindings. It has no unmapped ProviderConfig fields. This does not
complete requirement 18: RuntimeConfig/session/environment controls, host/asset qualification and
cross-version release verification remain. Historical migration inventory entries remain evidence
of the broader program; this batch does not claim that every historical file or adapter is removed.

Next audit finding: `state_hash` currently ignores `package_id` and recursively ignores keys named
`version`, `created_at`, etc., even inside arbitrary business knowledge. Package ID now controls
caller-memory scope, so it is behavioral; nested business fields can also be behavioral. Correct
this stale-stage detection boundary using explicit metadata paths rather than blanket key removal,
with regression coverage, before finishing RuntimeConfig/session ownership. Then continue durable
backend receipts, live facts, email and recorded/physical qualification. All 40 requirements remain.

## Batch 11 — semantic hashes, staging conflict detection and empty allowlists

Previous goal turn classification: progress (retired controls and preserved deployment history).
This continuation reread the full objective and confirmed the blanket hash filtering defect against
the current source and package-scoped memory boundary.

- Requirements 18/33: effective behavior hashing now ignores generated bookkeeping only at explicit
  identity, integration-root and memory-block paths. Package ID, skill version and arbitrary business
  fields named version/revision/fingerprint/created_at/updated_at/display_name remain significant.
- Stage/activate conflict checks use a separate complete configuration token, including integration
  revisions and private-store fingerprints. A regression rotates a synthetic masked OpenWA key
  without incrementing its revision; semantic public behavior stays the same, but activation of the
  older stage rejects. Business-version/date changes also reject rather than overwriting newer facts.
  The active-package API returns both hashes from the same returned package snapshot.
- Read-back testing exposed task normalization occurring only at activation. Resolution now
  normalizes the task before staged hashing. Task saving preserves explicit empty lists, including
  `allowed_tools`: an explicit empty allowlist must not become an absent legacy allowlist.
- Activation rechecks call state after acquiring its async lock. A queued activation is rejected if
  a call starts while it waits. The regression uses simulated call state and performs no real call.

Evidence: tests cover metadata-key collisions at business paths, package identity, skill versions,
nested tool headers, genuine bookkeeping normalization, masked-key rotation, stale business edits,
effective-hash read-back, empty allowlist preservation and the call/activation lock boundary.
Final isolated suite: 1,396 passed, 39 skipped, one device test deselected. The sole failure remains
the pre-existing frozen WhatsApp mismatch. Ruff, configured Pyright, compilation, feature controls
and migration inventory passed. Protected WhatsApp sources/manifest are unchanged.

Refreshed verified idle Studio from PID 45889 to PID 47116. Native MCP confirms exact preservation
of the entire effective package and availability of both hash fields. The selected bridge STT/LLM,
Edge TTS, tools and business/persona settings are unchanged. Current validation still correctly
fails material-fact reviews and email capability. No call, message, business mutation, official API
request or business package activation was performed.

Activation hashes: `ai_bridge/control_plane.py`
`10a751a95671ad3eb5e7326da7731a750de46a6a6d33a376917853b7e36cd207`;
`ai_bridge/web_server.py`
`dbd662a26d95a5b06ba07190c27d79f64fe9e4a207d5c2452a9c3af68592a498`;
`ai_bridge/tasks/task_engine.py`
`9321b5881ab4de66fbd268e5a870348a65cccdad8364289718c14039240be711`.

Limits: staged records using the previous base-hash algorithm need restaging. These checks do not
prove external backend authentication, same-reference provider-secret rotation, complete physical
call immutability or every possible integration-provisioning transformation. The effective hash
is distinct from the full staging token; neither replaces authoritative backend action receipts.
Physical/audio quality and real integration qualification are still outstanding.

Session audit: RuntimeConfig has 19 fields. `record_calls` has no live consumer (a similarly named
field also exists in runtime_schema.py); actual recording is separately governed by RecordingConfig
and per-call consent. `memory_enabled` is consumed but not in AgentPackage. Task ID/system prompt/
auto-answer/providers already have declared owners. Event output, hardware locks, authentication,
transport routing/audio geometry and queue sizing need explicit session/host ownership, not blind
exposure as business controls. Direct environment readers outside RuntimeConfig remain to be audited.

Next: complete the session memory policy roundtrip, retire/clarify the unused recording flag while
preserving actual recording consent, and record the immutable host/session requirements. Continue
durable backend receipts, live facts, email and recorded/physical qualification. All 40 requirements
remain active; no conversation-quality or full-package-completeness claim has been substituted.

## Batch 12 — caller-memory policy, recording ownership and worker shutdown

Previous goal turn classification: progress (staging conflict detection, task normalization and
call-boundary checks). This continuation reread the full objective and traced session consumers.

- Requirements 18/33/34: `runtime.memory_enabled` now round-trips through settings, package
  resolution/activation/rollback, child environment and worker readiness. Snapshot version 6 records
  it explicitly. It governs automatic caller history for subsequent calls, not audit evidence,
  approved package knowledge or separately authorized business tools.
- Disabled calls avoid loading caller stores, recalling prior preferences or writing new caller
  summaries/episodes. The manager and writer both enforce disabled persistence. Tests seed real
  temporary history, prove enabled recall, then prove disabled absence and unchanged stored bytes.
  Unknown/anonymous callers do not share a persistent bucket. Model context states the effective
  caller-history capability and does not invite cross-call memory promises when it is disabled.
- Removed the unused `record_calls` field/environment flag and its obsolete registry entry. The
  prototype deployment schema imports the old false value but rejects an attempt to enable recording
  through that retired control. Actual RecordingConfig/per-call consent behavior is unchanged and
  tested against inherited conflicting flags. No recording was enabled by this batch.
- Found and fixed a per-call memory-worker lifecycle leak. Owned workers now reject new submissions
  at close, drain accepted work and stop; borrowed/shared workers remain under their owner's control.
  Close waits are bounded, with diagnostic closing/alive state for remaining work. Tests cover normal
  draining, full queues, a slow mirror, ownership, observer failure and shutdown racing initialization.
  Database initialization stays outside the lifecycle lock and cannot revive a worker after close.

Final isolated suite: 1,410 passed, 39 skipped, one device test deselected. The only failure remains
the pre-existing frozen WhatsApp source mismatch. Ruff, configured Pyright, compilation and migration
inventory passed. Feature governance passes with 13 discovered boolean controls and 16 durable
controls after removal of the unused recording flag. Protected WhatsApp files/manifest are unchanged.

Refreshed verified idle Studio from PID 47116 to PID 49251. Native MCP confirms all previous runtime
values and business/persona/tool configuration are preserved, with caller memory still enabled.
Resolved package validation exposes that policy and still correctly fails material-fact reviews
and missing email capability. Bridge STT/LLM and Edge TTS are unchanged. No call, message, business
mutation, official API request or business package activation occurred.

Activation hashes: `ai_bridge/control_plane.py`
`38f1789ca6afa363e657b759f011496611f9ad04bffc7737804be2ea91e1fe27`;
`ai_bridge/web_server.py`
`6a5db06798554325c0331e5ab73952414d309ac9900ddc1bb61da524a09cb6fd`;
`ai_bridge/agent_policy.py`
`82607587ddab5636340ff5e08aee3dfefcbc06e9df8fb024c67a2a194dc9cd89`;
`ai_bridge/memory/memory_manager.py`
`a9b8ae1f87e8adaf223ec8a7aaeb23c84ddaa442192a336df65bed0f96488390`;
`ai_bridge/memory/memory_writer.py`
`f80a10144ff475da012e813dd3d4902d79879593afe3aae809970ee188cf8e30`;
`ai_bridge/identity/memory.py`
`87a50140973d32fdec27010c20b97ef5da86ce268392ff0aa684d3ea05c37277`.

Limits: memory disablement is not erasure or a prohibition on all logs/business records, and applies
to newly started call sessions. Optional mirror I/O may continue draining after the bounded close
wait; threads are not forcibly terminated and this is not durable remote-mirror retry/reconciliation.
Prompt capability guidance is not proof of perfect model obedience. Long-call resource/perceptual
qualification remains outstanding. No real call or real recording was performed.

`reports/quality/runtime-session-ownership.md` maps the 18 remaining RuntimeConfig fields to package,
session or protected host owners. Host/transport parameters still need authoritative requirement
binding and worker read-back, and direct environment controls outside RuntimeConfig remain an
unfinished inventory. Recording retention/root/queue settings need explicit privacy/host treatment.

Next: bind and verify the protected host/session requirements without exposing pairing or media
controls as arbitrary persona settings; then complete remaining environment ownership and move to
durable backend receipts, live facts, email and recorded/physical qualification. All 40 requirements
remain the completion audit and the overall goal stays active.

## Batch 13 — host/session requirements and transport read-back

Previous goal turn classification: progress (caller-memory policy and worker shutdown). This
continuation reread the objective, inspected the live host environment and traced transport
construction. The live service uses the default 16 kHz/20 ms/25-frame profile.

- Requirements 18/37/40: version-7 packages bind thirteen host/session requirement fields to the
  parsed worker report. These constrain topology, audio geometry, queue size, event output, pairing,
  lock path and ADB forwarding; they are not business-package setters for protected host values.
  Device selection, lock path and pairing material are reported as hashes. Current values are
  captured for legacy imports before staging, while explicit mismatches invalidate the package.
- A private pairing-file rotation now changes the configuration token and rejects an older staged
  package even when environment strings stay unchanged. Invalid host settings make a resident
  worker unavailable rather than allowing readiness to remain trusted. Rollback reports failed
  validation instead of silently rebinding a saved package to another host.
- Explicit session-environment parsing avoids mutation of the parent environment and avoids mixing
  in unrelated global values. The worker reports what it parsed. Base64 pairing material is bounded
  consistently with the existing key-file bound, without returning its contents.
- Found two parsed settings not forwarded to transport construction. Queue size now reaches the
  transport; the supported phone geometry is explicitly 16 kHz/20 ms, matching the existing baseline.
  Other requested geometry rejects before startup instead of yielding a misleading report. Both
  voice channel constructors use the same parameter builder. No live parameter value was changed.

Validation: final isolated suite 1,419 passed, 39 skipped, one device test deselected. The sole
failure remains the pre-existing frozen WhatsApp mismatch. Ruff, configured Pyright, compilation,
feature controls and migration inventory pass. Protected WhatsApp client files/manifest are unchanged.
Tests cover mapping/read-back, queue parameters, unsupported geometry, bounded pairing material,
host constraint mismatch, key rotation, environment isolation and resident readiness rejection.

Refreshed verified idle Studio from PID 49251 to PID 55080. Native MCP confirms all previous package
components/runtime values are preserved, thirteen host requirements are present, and their check
passes. Current values remain 16000 Hz, 20 ms and 25 input frames. The package still correctly fails
material-fact review and missing email capability checks. Bridge STT/LLM and Edge TTS are unchanged.
No call, message, business-record mutation, official API request or business package activation occurred.

Activation hashes: `ai_bridge/control_plane.py`
`4928471c5c8e9a3d484f983d974acbbd0b9dd5d96f6f2681c9e0efaa17a95a6d`;
`ai_bridge/web_server.py`
`3ad807444269a4b4f531a5bf2be786acd3d33cf3da4734c931d794a5f8f9b33d`;
`ai_bridge/runtime_config.py`
`c48870ac201e774de847deade801220dab5a8a4184dfd36ef543c18c407294ed`;
`ai_bridge/phone_voice_agent.py`
`70d220c4b32fdca17089900759e8b32d71ad17f3cee562fa602c5eaabf856082`.

Limits: this verifies configured/parsed requirements, not physical device identity, actual audio
delivery, installed assets or performance under a nondefault queue. Remaining direct environment
controls and recording/privacy configuration still need ownership and qualification. All original
conversation, action, real integration and release requirements remain.

Independent integration evidence: native read-only Frappe health returns status ok and
required_ready=true, with six installed apps. The active business integration is disabled. Source
inspection shows enabling it also enables automatic call-outcome writes; there is no separate
outcome-sync switch. Its tool registration does not distinguish read-only getters, and execution
contains an argument fallback when a bound phone is absent. No customer record was queried or written.

Next priority is the caller's actual database-registration problem: separate caller-invoked business
tools from automatic outcome sync, tighten bound-caller and read-only semantics, and qualify the
existing registration/context tools before enabling them for the active task. Preserve campaign and
automatic writes as disabled unless specifically authorized. Then reconcile durable receipts and
investigate available email-bridge configuration. Remaining environment/recording ownership and
recorded/physical quality tests stay open. Approved offer evidence and email sender selection are
still pending user input. All 40 requirements remain active.
