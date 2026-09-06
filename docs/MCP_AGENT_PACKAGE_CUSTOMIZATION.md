# MCP Agent Package customization

PhoneAgent starts from a neutral, unconfigured package. It does not assume a company,
product, price, persona, objective, website, or external integration. Until an MCP client
activates a configured package, auto-answer and business integrations remain disabled.

The MCP server is the complete configuration boundary. Use these tools in order:

1. `phone_agent_get_active_package` returns the current complete package and its effective-state hash.
2. Clone the returned `package` object and replace its identity, task, knowledge, runtime prompt,
   skills, memory blocks, tools, and integration assignments. The task's `knowledge` object is the
   source of product facts; its `allowed_tools` is the action allowlist.
3. `phone_agent_validate_package` checks the complete package without changing runtime state.
4. `phone_agent_stage_package` records the exact validated package, reason, and actor.
5. `phone_agent_activate_deployment` atomically activates the staged deployment between calls.
6. Read `phone_agent_status`, `phone_agent_identity`, and `phone_agent_get_active_package` to verify
   the worker's effective task, identity, providers, and deployment.

Use `phone_agent_delete_task` and `phone_agent_delete_skill` to remove retired user-authored
components. `phone_agent_rollback_deployment` restores a prior exact package through the same
validation path.

An Agent Package controls:

- identity name, role, organization, mission, disclosure, values, boundaries, voice style,
  languages, examples, and evaluation cases;
- task objective, greetings, required inputs, stages, knowledge, sample phrases, objections,
  tool allowlists, approvals, stop conditions, and spoken-length limits;
- STT, bridge LLM, TTS, voice, language, call channel, prompt, and latency features;
- progressive skills and mutable memory blocks;
- generic MCP tools, caller-bound WhatsApp, public research, and business/CRM integrations;
- package labels used for deployment and audit.

The media transport, caller binding, secret redaction, audit integrity, one-call lock, and verified
playout controls are platform invariants. Agent Packages cannot replace or weaken them.

The shipped neutral package uses `PhoneAgent` and `general_conversation`, contains no product facts,
and tells the model to say that missing information is not configured. This makes an incomplete
customization fail closed instead of silently falling back to an old product.

## Contextual consent and channel bindings

Consent fields declare a `consent_scope`; they are not filled by broad keyword matches.
Supported scopes are `continue`, `interest`, `budget`, `whatsapp`, `email`, `sms`, `messaging`,
`purchase`, and `registration`. A short affirmative applies only to the specific proposal whose
playback completed with acknowledgement. Generated, interrupted, stale and unverified proposals
do not grant consent. Explicit caller requests are evaluated independently, and a channel choice
does not authorize another channel or a purchase.

Example task fragment:

```json
{
  "inputs_required": [
    {"id": "budget_alignment", "consent_scope": "budget"},
    {"id": "summary_permission", "consent_scope": "whatsapp"}
  ],
  "required_capabilities": ["whatsapp.send"],
  "allowed_tools": ["whatsapp_send_text_current_customer", "whatsapp_last_delivery_status"]
}
```

The WhatsApp integration must also be enabled, authenticated, session-ready, and assigned to the
task. Declaring a tool in the task does not establish backend readiness. Package validation now
rejects missing configured tool bindings and channel contradictions. It checks positive messaging
promises in authored instructions as well as explicit `required_capabilities`; natural-language
analysis is bounded and is not a universal semantic verifier.

Custom managed tools can declare `capabilities: ["email.send"]` (or `whatsapp.send`, `sms.send`)
alongside `read_only: false`. That metadata travels to the live tool catalog and consent boundary;
the tool name need not contain the channel name. An email read/search tool cannot satisfy sending.
This is a declaration of purpose, not proof of a completed action or a valid recipient.

`phone_agent_set_integration` sends a single `config` envelope to Studio. The server also accepts
complete validated configurations from older installed MCP clients that omitted that envelope.
Empty or unknown configuration fields still fail validation. Use `phone_agent_test_integration`
to check connectivity, then verify real delivery separately with an authorized test recipient.

## Action receipts and retries

Writable managed tools and known business/message actions are journalled before dispatch in the
production pipeline. Python user tools default to writable; declare `read_only=True` on the
`realtime_tool` decorator for actual lookups. Direct async handlers are awaited and synchronous
handlers run outside the event loop with a timeout. A timed-out thread may still finish in its
backend; timeout never proves the action failed or was cancelled.

Message senders return structured receipt fields, for example:

```json
{"accepted": true, "message_id": "backend-message-id", "delivery_confirmed": false}
```

Structured MCP result content is supported. Free-form “sent successfully” text and a generic
`ok: true` are insufficient. Acceptance, device delivery and reading are distinct; self-chat
visibility is not device delivery. Verified CRM registration needs the backend's verification
flag, mutation flag and record ID. Draft orders/quotations remain drafts, not completed purchases.

The production action journal stores request hashes and bounded receipts in a private SQLite
file with 30-day retention. It does not store argument or response bodies. Its key binds the call,
caller, tool and canonical arguments. Matching requests replay recorded status rather than
dispatching again. Explicit direct resend requests create a new logical operation; repeated tool
attempts for that same resend still deduplicate. Interrupted or timed-out submitted actions stay
unknown until reconciled. Cancelled requests that were never submitted can be tried again.

Speech claims are scoped to the receipt channel. A WhatsApp receipt cannot prove an email send.
Delivery-status lookups update only message IDs already known to the current call. Stale action
results cannot authorize speech in a newer caller turn.

This journal is not an exactly-once guarantee from an external backend. Semantic paraphrases,
backend-side idempotency, reconciliation after a lost response, and supported receipt formats
need qualification for each integration. Arbitrary new backend formats must not be treated as
successful merely because the HTTP request completed.

## Greeting disclosure and fact reviews

Configured openings identify the speaking AI before TTS. A missing disclosure adds a short
appositive to the configured name while retaining the call purpose. Contradictory or different
self-identities fall back to the approved identity disclosure. An AI product mention is not
an AI self-disclosure. The persisted greeting is preserved for the operator to edit through MCP.

Material entries in `task.knowledge` have matching records in `task.knowledge_evidence`, keyed
by the exact fact key. The MCP control-schema response exposes this extension's JSON Schema.
Each record contains:

- `value_hash`: `sha256:` plus SHA-256 of the trimmed fact value encoded as UTF-8;
- `source_kind`: `operator`, `document`, or `backend`;
- `source_ref`: a public or internal review reference, without embedded secrets;
- `reviewed_by`: the reviewer or responsible authority;
- `reviewed_at`: an ISO timestamp with timezone;
- optional `expires_at`: an ISO timestamp with timezone.

Changed fact values invalidate their reviews. Missing, malformed, future-dated and expired
reviews fail material-fact validation. Certification claims require a document/backend source,
not only an operator pricing declaration. Price, timeline, certification, guarantee, availability
and compatibility categories are checked. The live guard withholds positive assertions in
categories with unreviewed configured facts, including direct greeting delivery.

These records are review attestations. Schema validation does not fetch the referenced document,
prove that a certification exists, or establish semantic agreement between arbitrary paraphrases
and a source. Inspect the actual source before providing a review record. Broader live backend
fact binding and semantic claim verification remain part of qualification.
