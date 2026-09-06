# External-Agent Control Plane

## Purpose

Codex, Hermes or another compatible agent should act as PhoneAgent's administrator/orchestrator,
while PhoneAgent remains the protected call executor. The external agent may design, validate,
deploy, operate, observe and roll back declarative behavior. It may not alter framework/media code.

## AgentPackage

`AgentPackage` schema version 1 is the atomic desired-state unit. It contains:

- package ID, display name, objective and labels;
- complete `IdentityProfile`;
- complete task contract;
- `RuntimeControl`;
- `behavior`: trait intensities, communication settings and English/French human-conversation
  instructions/repair wordings, replaced atomically during activation;
- user `SkillDraft` objects needed by the identity;
- full replacement set of mutable approved memory blocks;
- managed tool config;
- OpenWA config;
- web-research config;
- Frappe business config.

Nested credentials returned by the active-package API are masked. Sending the mask back preserves the
stored secret. The package transport is bounded to 400,000 characters.

Clone the effective package to preserve authored behavior. Omitting `behavior` on a newly activated
package uses the shipped neutral conversation defaults, not the previous business's persona.
Export includes effective conversation defaults; activation failure restores the persona file and
in-memory behavior. Existing active deployments are not rewritten just by upgrading the server.

Runtime controls include Smart Turn enablement/threshold, repair enablement, bridge corroboration,
complete/incomplete/fallback endpoint and transcript stability timings, and Edge voice rate, volume
and pitch. These values round-trip through settings, package export and worker environment; worker
readiness reports the parsed values. Validate/stage resolves omitted optional tuning from the
current effective values and captures shipped conversation defaults before hashing. Validation
returns `resolved_runtime` and `resolved_behavior`; the stage response contains the stored package.
New deployment records have `snapshot_version: 7`. Resolved conversation behavior does not import
new shipped wording when loaded again. Exact validation is required by the storage layer too.

Legacy records remain readable, but activation/rollback of a record without the resolved snapshot
marker is rejected with a review-and-restage explanation. Missing historic defaults cannot be
reconstructed accurately. These snapshots cover the fields currently represented by RuntimeControl
and ConversationBehavior; they do not yet capture every provider or environment setting. Run
`tools/audit_package_setting_coverage.py` to inspect the remaining ProviderConfig coverage.

Version 2 additionally records bridge chunking/context bias, local/Flux endpoint controls,
speculation timing, reflex cooldown, model sampling/context/response limits, supported bridge CLI
timeouts, TTS buffering/phrase limits/timeouts, synthesis parameters and maximum WhatsApp call
duration. These 41 newly exposed settings use the same bounds as the worker parser. Their values
are carried explicitly into the worker environment, including nonstandard environment names;
worker readiness reports them and hashes context-bias text instead of logging it. Snapshot version
1 remains readable but cannot claim an exact current-schema rollback for values it never captured.

The old `parakeet_energy_threshold_dbfs` control was unused by the current local neural turn
detector and is retired. Settings requests containing it are rejected rather than pretending to
adjust the detector. The former environment variable has no consumer. This does not change the
neural VAD or the separate SenseVoice energy gate.

Version 3 also captures the configured CLI/FFmpeg/model paths and Ollama, OpenRouter, vLLM and
LM Studio service addresses. Environment mapping includes their nonstandard variable names, and
worker readiness compares the parsed values. Service addresses must use HTTP(S) without embedded
credentials, query strings or fragments; paths reject control characters. Validation errors omit
input values so rejected credentials are not echoed. These checks validate configuration shape;
they do not prove executable/model contents, DNS identity or service availability. Those assets and
services still need installation/readiness qualification. No provider is selected by these settings.

Version 4 captures `runtime.credential_refs`: seven provider credential fields mapped to ordered
lists of `env:NAME` references. Empty lists explicitly disable a credential. Standard provider
variables are restricted to their matching provider field; custom aliases use `PHONE_AGENT_SECRET_`.
Raw values and unrelated environment references are rejected. Read the field schema for the exact
keys and reference syntax. Existing default references preserve the established Google/Gemini alias
order; explicitly replacing or clearing that list removes the fallback.

References, never secret values, persist in settings/deployments and appear in readiness events.
The local runtime resolves them from its process/private-file environment. Package validation checks
required credential presence for the selected provider; this is not an authentication probe. Changing
references updates Studio's private provider config and worker resolution; rollback restores the
reference list, not a historical secret value. Rotation of an existing environment variable may
require process reload, and private-file/environment precedence remains in effect. These provider
references do not replace OpenWA, MCP or business-integration credential stores.

Version 5 removes the retired direct-audio-model controls from active runtime settings and the
published package schema. `pipeline_mode` advertises only `cascade`. Importing an older package
normalizes its retired mode and discards the recognized obsolete control fields; current settings
requests that attempt to set those controls are rejected. Saved settings migrate on read, and the
next persistence writes only current controls. Retired environment variables are not passed to the
call worker. This is configuration migration, not revival of an alternate execution pipeline.

Deployment history retains its original package payload even when the current model presents a
migrated read view. Status updates preserve that payload alongside its original package hash, and
attempts to replace a stored deployment's package are rejected. Historical rollback still requires
the version-specific review/restaging boundary described above.

The active-package response exposes two different hashes. `effective_state_hash` compares behavior,
excluding generated bookkeeping only at declared identity/integration/memory paths. Package identity,
skill versions and arbitrary business fields named `version`, `updated_at`, etc. still count.
`configuration_state_hash` includes revisions and private-store fingerprints and is the server's
stage/activate concurrency token. Thus a masked integration-key change can invalidate an older
stage even when public masks remain the same. Both response hashes use the returned package snapshot.
Staged records from before this hash change need restaging. Provider reference values remain
externally managed as described above; the token is not an authentication or secret-rotation proof.

Task normalization happens before staged hashing. Explicit empty lists, especially `allowed_tools`,
remain explicit during saving. Activation rechecks call state after waiting for the activation lock.

Version 6 adds `runtime.memory_enabled` for automatic caller-history recall and persistence in
subsequent calls. Older imports inherit the effective setting before staging. Disabled calls avoid
loading caller stores, do not write caller summaries/episodes or create semantic-memory workers,
and receive matching capability guidance. Unknown/anonymous callers never share a persistent history
bucket. Existing history remains available for explicit operator review; this flag does not erase
data or disable approved package knowledge, audit records or explicitly authorized business tools.

The unused `record_calls` field/environment flag is retired. Actual recording remains controlled by
per-call recording consent and RecordingConfig. Caller-memory workers have explicit ownership and
bounded shutdown: accepted episodes drain, new submissions reject after closing, and a borrowed
shared worker is not shut down by one call. Slow I/O can continue after the bounded close wait;
diagnostics expose closing/worker-alive status. The change is not a durable remote-mirror retry system.

Version 7 binds `host_requirements` to parsed host/session values. These are constraints, not setters
for protected transport or pairing settings. Legacy imports bind the current host before staging;
provided requirements must match it. Pairing-key rotation, routing, lock-path or queue-setting changes
invalidate an older stage/rollback. Worker readiness reports the same values, with hashes for device
selection, lock path and pairing material; private keys and device serials are not reported.

The supported phone geometry is explicitly 16 kHz mono with 20 ms frames. Other requested geometry
is rejected before host startup. Queue size reaches the transport constructor instead of being parsed
and then ignored; the default remains 25 frames. Explicit session-environment parsing does not modify
the parent process environment. These checks establish configuration agreement, not physical-device
identity, installed model/APK qualification, service reachability or measured audio performance.

## Safe deployment lifecycle

1. Read `phoneagent://schema/agent-package`.
2. Read `phoneagent://state/active-package` or call `phone_agent_get_active_package`.
3. Clone the full package and modify only desired fields.
4. Call `phone_agent_validate_package`.
5. Resolve critical failures; inspect warnings such as task tools not active in the package.
6. Call `phone_agent_stage_package` with a meaningful reason and external-agent identity.
7. Record the deployment ID and exact package hash.
8. Call `phone_agent_activate_deployment`.
9. Read back the active package/deployment/effective state.
10. Operate calls and follow `phone_agent_recent_events` with a sequence cursor.
11. Use `phone_agent_rollback_deployment` if qualification regresses.

Staging never changes active behavior. Activation revalidates the exact hash and compares the staged
base-state hash with the current effective state. A manual/other-agent change after staging forces a
new stage rather than silently overwriting it.

## Activation semantics

- Refused during a call.
- Serialized by an activation lock.
- Snapshots effective private config files before writes.
- Validates task, runtime, tools, OpenWA, research, business, memory, skills and identity.
- Saves/trusts package user skills under the authenticated actor.
- Replaces the mutable memory set while retaining immutable self memory.
- Executes identity revision → evaluation → approval → activation.
- Marks previous deployment superseded and writes a private active pointer.
- Restores snapshots and marks the deployment failed if effective activation raises.

Identity revision/audit artifacts may remain as evidence after a failed activation; the effective
active identity/config is restored.

## MCP resources

- `phoneagent://schema/agent-package`
- `phoneagent://state/active-package`
- `phoneagent://state/capabilities`

## MCP tools

Hermes exposes these as `mcp__phoneagent__<original_name>`. Hermes must call those registered tools
directly. It must not use `mcporter`, shell, Python scripts or raw REST as a convenience layer when
the native tools are healthy. Read [hermes-native-mcp.md](hermes-native-mcp.md) for exact response
shapes and operation recipes.

Inspection:

- `phone_agent_status`
- `phone_agent_capabilities`
- `phone_agent_identity`
- `phone_agent_control_schema`
- `phone_agent_get_active_package`
- `phone_agent_validate_package`
- `phone_agent_list_deployments`
- `phone_agent_recent_events`
- `phone_agent_list_tasks`

Deployment:

- `phone_agent_stage_package`
- `phone_agent_activate_deployment`
- `phone_agent_rollback_deployment`

Operation:

- `phone_agent_dial`
- `phone_agent_hangup`
- legacy `phone_agent_request_dial` and `phone_agent_execute_approved_dial`

The admin dial tool treats possession of the private local control token as operator authority, but
still applies destination normalization, rate/cooldown, consent, hardware preflight and one-call
locking.

Every tool result keeps its endpoint envelope. Do not assume nested values are flattened. For
example, package validation is `result.validation.valid`, staging returns
`result.deployment.deployment_id`, and activation returns `result.deployment.state`.

## REST API

Authenticated endpoints:

```text
GET  /api/control/schema
GET  /api/control/package
GET  /api/control/deployments
GET  /api/control/events?after=SEQUENCE&limit=1..200
POST /api/control/validate
POST /api/control/stage
POST /api/control/activate
POST /api/control/rollback
POST /api/control/dial
POST /api/control/hangup
```

REST uses the private mode-0600 bearer token at `~/.config/phone-agent/control.token`. Prefer stdio
MCP so an external model never receives the token.

## Codex connection

The installed command is:

```text
~/.local/share/phone-agent/runtime/.venv/bin/phone-agent-mcp
```

Register it with current Codex CLI:

```bash
codex mcp add phoneagent -- \
  "$HOME/.local/share/phone-agent/runtime/.venv/bin/phone-agent-mcp"
codex mcp get phoneagent
```

Existing Codex tasks may need a new task/restart to discover a newly registered server.

For another MCP client, configure the same executable as a local stdio server.

## Event cursor

PhoneAgent retains a bounded in-memory window of recent Studio/call events. Each receives a sequence
and observation time. Top-level caller routing identifiers are hashed. Transcript/tool content remains
administrator-visible because orchestration needs it; treat it as customer data.

Poll with `after` equal to the last `next_after`. Do not busy-loop; callers and tools naturally create
gaps. Event history is operational, not a durable CRM record.

## External-agent prompt pattern

A strong request to the administrator agent has this shape:

```text
Read the PhoneAgent schema and active package. Build a package for <objective> and <audience>.
Preserve protected media and security boundaries. Configure identity, task, knowledge, skills,
tools, CRM behavior, voice/language/channel and evaluation examples. Validate it, explain the
effective diff, stage it, then activate only if validation is contract-clean. Monitor the call
and report only backend-verified results.
```

The external agent must not construct a package from memory. Clone the current package so masked
credentials, existing integrations and complete strict fields remain intact.

## Conflict and recovery rules

- A 409/stale-state response means read current state, reapply intended changes and stage again.
- A failed deployment is not reusable; create a new stage after correcting it.
- Rollback creates a new deployment from the historical package; it does not rewrite history.
- Do not delete deployment files manually.
- Do not edit active identity/config files behind the control plane while another agent is staging.
- Use native installer rollback for a code/runtime release failure, AgentPackage rollback for behavior
  regression, and business-suite restore for database recovery.
- A one-call channel switch is currently a persistent AgentPackage change. Record the previous
  deployment and restore it after the call when the operator intended the switch to be temporary.

## Deliberately unavailable powers

AgentPackage has no shell command, file path, source patch, Docker mutation, ADB command, raw secret,
codec, PCM frame, Android mixer or frozen-manifest field. If a job appears to require one, determine
whether it is actually a framework defect requiring a separate authorized engineering change.
