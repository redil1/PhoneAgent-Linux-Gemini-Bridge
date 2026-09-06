# Test isolation and runtime refresh review

Date: 2026-09-04. This is an offline engineering review. No call was placed, no
service was restarted, and no operator configuration was copied or modified.

## Test isolation finding and repair

`AgentPolicyRuntime` initializes `PersonaCompiler`, which reads the installed user
persona and initializes/evaluates an `IdentityKernel`. The existing shared test
fixture isolated user tools only. Consequently, ordinary policy tests could read
the operator's live persona, identity and tasks, and attempt private-file writes
outside the checkout. An installed identity readiness score was not a valid
baseline for tests of the shipped policy.

An initial focused run redirected `pathlib.Path.home()` to a temporary root before
application imports and cleared inherited `PHONE_AGENT_*` variables. It produced
**174 passes and 2 failures in 4.18 seconds** across policy, Antigravity STT,
speculation, conversation repair/reflexes and turn continuity/intelligence. All
identity initialization checks in that selection passed. The two observed
failures were duplicate repair output and an internal retry instruction remaining
in the conversation context. They were reported to the implementation owner.

`tests/conftest.py` now additionally redirects the default user persona, identity,
caller memory and task directories to a fresh directory for each test. It clears
inherited overrides for those stores. Explicit test paths remain authoritative;
tests can still set their own environment values after fixture initialization.
No evaluator result, readiness criterion or production code was bypassed.

An expanded direct pytest run after this fixture change produced **204 passes and
3 failures in 6.52 seconds**. The remaining failures were: the Antigravity endpoint
test still expected 260 ms after source defaults changed to 600 ms, retry-context
cleanup, and an ephemeral Graphiti HTTP test server denied by the sandbox. The
implementation was changing concurrently, so this is a progress snapshot rather
than final acceptance evidence. Identity and personality tests otherwise passed.

Ruff passed for the changed fixture and the isolated test launcher.

## Full-suite evidence and limitations

`run_isolated_pytest.py` provides a repeatable launch method that redirects
`Path.home()` before application imports and clears inherited `PHONE_AGENT_*`
variables. It does not change the real `HOME` environment variable or user files.
The override applies to the pytest process; child-process tests remain responsible
for their own explicit temporary configuration, as in the existing test fixtures.

```sh
.venv/bin/python reports/quality/turn-taking-fix/run_isolated_pytest.py -q --tb=short
```

The first full run produced **788 passes, 78 failures, 39 skips, 1 deselection and
3 errors in 41.39 seconds**. Device integration tests remained deselected. Most
extra failures were `PermissionError` when unit/integration tests attempted to bind
ephemeral localhost mock servers under the restricted sandbox. A permitted
loopback execution environment is required for a meaningful complete result; these
failures must not be presented as application defects.

Non-sandbox failures included the previously observed frozen WhatsApp discrepancy,
Android retry qualification mismatch, absent qualification audio and CI workflow,
migration/inventory expectations, the two conversation-repair defects, and the
new endpoint-default expectation described above. The full run began while source
implementation changes were already in progress, before the fixture edit; it is
not a pristine pre-change source baseline. Final verification must use stable
finished source.

Raw evidence:

- `baseline-full-pytest.txt`
- `isolated-focused-pytest.txt`

## Current runtime ownership

Read-only checks established:

- Port 8090 is held by Python PID 71637 on `127.0.0.1`.
- Its working directory is `/Users/aziz/Desktop/phone-agent-linux` and its loaded
  Python packages include this checkout's `.venv/lib/python3.11/site-packages`.
- Its standard streams are inherited pipes, rather than the configured launchd
  log files.
- `launchctl print gui/501/com.phoneagent.studio` reports no loaded service.
- The on-disk LaunchAgent plist points to the separate installed
  `~/.local/share/phone-agent/runtime/.venv/bin/phone-agent-web` runtime. That plist
  therefore does not describe the currently listening checkout process.
- The root investigation identified the active command as
  `python3.11 -m ai_bridge.web_server`; this review independently confirmed its
  working directory and Python dependency location. Full process listing is
  restricted in this sandbox.

## Why the ordinary installer is unsuitable for this refresh

`tools/install_macos.sh` would synchronize every extra, build/sign the desktop app,
stage a self-contained installed runtime and load its LaunchAgent. That changes
the service shape from the current direct checkout process. Its declared Python
3.12 venv is also incompatible with this checkout's `>=3.11,<3.12` package
requirement. The `local` extra currently pins a CUDA torch build, so blindly syncing
all extras on this Mac creates a separate dependency risk. The installer also
requires currently failing frozen/full-suite gates. Do not use it merely to make
the turn controller code active.

The installer's rollback covers app/runtime/LaunchAgent snapshots, not the
currently running checkout service. `tools/rollback_macos.sh` is consequently not
the correct rollback mechanism for a same-checkout refresh.

## Recommended bounded refresh and rollback

1. Finish and verify the code; retain hashes and backups of exactly the changed
   files under the existing `reports/quality/turn-taking-fix/before/` snapshot.
2. Read the authoritative Studio call state immediately before refresh. Defer
   while any call owns the runtime. Capture the current command, working
   directory, interpreter and relevant non-secret launch settings.
3. Stop only the verified checkout Studio process and let its existing shutdown
   path stop its owned voice host. Confirm both the intended process and any
   owned child have exited before reopening the same loopback port.
4. Relaunch the same Python 3.11 module from the same checkout using the same
   configuration and launch environment. Preserve the existing service shape and
   operator settings; dependency synchronization and installation are unnecessary
   for this source-only change when imports already validate.
5. Verify the new process, healthy/IDLE Studio status, owned voice-host health and
   new runtime configuration read-back. Confirm the turn-controller source/version
   used by the new host. HTTP 200 alone does not prove a newly loaded controller.
6. If health or offline acceptance regresses, stop that new instance, restore only
   the changed files from the captured snapshot, and relaunch the same command.
   Preserve unrelated concurrent edits. Recheck the restored runtime.
7. Actual French/English call-quality qualification still requires an explicitly
   authorized physical test call and must be reported separately from the offline
   regression evidence.

This review intentionally supplies the refresh procedure without executing it.
The implementation owner can perform the permitted refresh once the final code
and exact runtime ownership are verified.
