# Universal MCP Agent Package migration

Date: 2026-09-05

## Result

PhoneAgent no longer boots as a named sales agent or assumes a product, company, price, URL,
catalog, or business workflow. The active configuration is the neutral `universal_neutral`
Agent Package. It uses identity `unconfigured-agent` with spoken name `PhoneAgent` and task
`general_conversation`.

The active prompt is 10,405 characters and contains none of the retired product, company,
domain, or persona markers. It has no callable business tools. Auto-answer, WhatsApp, public
research, and business/CRM integrations are disabled until a new package explicitly enables
and assigns them.

## MCP ownership

The stdio MCP server now owns the full customization lifecycle:

- read the schema and current complete package;
- validate, stage, activate, list, and roll back exact versioned packages;
- configure identity, voice, task, knowledge, prompts, tools, integrations, skills, and memory;
- delete retired user-authored tasks and skills;
- verify active identity, package, worker status, and recent events.

Identity revisions can now replace `identity_id` as well as name, role, mission, behavior, and
voice. Previously the store silently retained the first identity identifier across every package,
which left the old persona ID active after a valid package replacement.

The customization workflow is documented in
[`docs/MCP_AGENT_PACKAGE_CUSTOMIZATION.md`](../../docs/MCP_AGENT_PACKAGE_CUSTOMIZATION.md).

## Migration and activation evidence

The neutral package was validated, staged, and activated through the real stdio MCP server.
Final active deployment: `dep_9cd7bce865a614f90190cd8c`.

MCP cleanup removed six retired user task contracts and five retired user persona skills. Legacy
persona and Studio backups containing product defaults were removed. Old deployment, identity
revision, history, and audit records remain historical evidence; they are not loaded into the
active prompt or task and cannot act without an explicit future rollback/customization operation.

Studio PID 19438 was healthy and idle at activation. A later read-only check observed an active
call that was not initiated by this migration. That call is running the neutral configuration:

- task: `general_conversation`;
- identity: `unconfigured-agent` / `PhoneAgent`;
- STT: `antigravity_live` bridge;
- LLM: `antigravity_gemini` bridge;
- TTS: Edge TTS;
- auto-answer: disabled;
- Smart Turn: enabled;
- local corroboration: disabled.

The migration did not initiate a phone call, message, CRM write, or public research action.

## Verification

- Full isolated suite: **1,212 passed, 39 skipped, one device test deselected**.
- Sole failure: the existing protected manifest detects the two previously modified frozen
  WhatsApp client files. The manifest was not rewritten.
- Focused neutral-default, identity, task, MCP, and package checks pass.
- Ruff: pass.
- Configured Pyright: 0 errors, 0 warnings.
- Python compilation: pass.
- Feature-control validation: pass.
- migration inventory: pass, 110 classified surfaces.
- Runtime-source scan: no removed product/company/domain marker under `ai_bridge`.
- Active prompt scan: no removed product/company/domain/persona marker.

Historical research and audit evidence can still describe earlier configurations. Those records
are retained for traceability and do not form part of the executable runtime defaults or active
Agent Package.
