# Recognition transport recovery

## Reproduced defect

A controlled EOF probe against the preserved pre-change source committed **“Send the”** as a final caller request after recognition disconnected. The patched source commits nothing from that damaged utterance. The original reader logged an error and exited; the independent endpoint watchdog still considered its partial text eligible. Audio-upload failures were also only logged, allowing later audio to continue after an unknown missing section.

Evidence: [before/after result](asr-failure-commit-probe.json), [probe source](probe_asr_failure_commit.py). The baseline source is preserved at `/private/tmp/phoneagent-asr-recovery-before/ai_bridge/antigravity_live_stt.py`; the probe requires that local backup.

## Changes

A failed reader or audio upload retires the current session, clears pending recognition and speculative text, discards queued audio, and starts bounded reconnection. Opening a new transport does not reset acoustic speech epochs, VAD or floor ownership. Queue entries carry their original session ID so a producer unblocked after failure cannot submit old audio to the replacement session. Late failures from an old reader cannot retire the new connection.

Speech overlapping a connection gap is discarded as a complete request, including any suffix arriving after reconnection. Once the caller pauses and recognition is available, the existing guarded repair path asks for repetition in the current reply language. It does not request repetition while recognition remains unavailable. Fresh subsequent speech can be transcribed normally.

Recovery allows at most three opening attempts within a shared 12-second budget. A ready handshake followed immediately by failure does not reset either limit; a valid recognized turn replenishes the budget. Exhaustion emits an attributed fatal upstream error. Shutdown cancels recovery, and a socket whose blocking handshake succeeds after cancellation is explicitly closed. This verifies worker-level failure signalling; physical call termination on an exhausted provider still needs device qualification.

Local decoder buffer-overflow errors also now travel upstream with STT attribution rather than being misclassified as output/playback failures. Local decoders do not use the cloud reconnect path.

## Tests and replays

- **10 new recovery cases passed**, covering EN/FR partial invalidation, duplicate failure, successful recovery after silence, upload failure, stale-session queue entries, late reader failure, repeated ready/EOF, total deadline, cancellation/late socket disposal, a disconnect during partial publication, and waiting for availability before asking for repetition. Parameterized cases share some assertions.
- Broader focused STT, framing, endpoint and local-controller run: **124 passed** before three additional recovery cases were added.
- Final isolated full suite: **1,098 passed, 1 failed, 39 skipped, 1 deselected**. The remaining failure is the existing frozen-WhatsApp integrity check. Its two protected files and manifest were not changed in this phase.
- Changed-file Ruff, Python compilation and configured Pyright with the checkout interpreter passed. An initial Pyright invocation without the explicit interpreter could not resolve installed Pipecat; the corrected invocation was `pyright --pythonpath .venv/bin/python`.

| Replay | STT reconnection | EOF to first simulated phone audio | Barge-in to flush | Next request / playback |
|---|---:|---:|---:|---|
| Forced disconnect during first caller utterance | 966.3 ms | 3,615.4 ms, repeat request | 149.4 ms | Correctly transcribed and answered; accounting resolved |
| Normal provider path | Not applicable | 3,214.0 ms | 154.3 ms | Correctly transcribed and answered; accounting resolved |

The disconnect replay produced “Sorry, could you repeat that?”, then transcribed “Wait, I want the yearly plan instead.” and answered with the twelve-month plan. No fragment of the interrupted first request was committed. No tool was invoked in that replay. The normal replay attempted a WhatsApp tool with its external effect disabled.

These are single synthetic English replays with real cloud providers and a simulated phone clock, not physical phone measurements or a latency distribution. EN/FR failure-state behavior is covered by deterministic tests; French cloud reconnection audio has not been separately replayed. In the normal run, the speculative model request took 2,234.8 ms and first TTS PCM took 694.9 ms. The normal 3.21-second result is not a demonstrated speed improvement over the earlier 2.49-second replay; model output and provider timings varied.

Artifacts: [disconnect replay](pipeline-stt-disconnect-replay.json), [normal replay](pipeline-normal-asr-recovery.json), [harness](benchmark_pipeline.py), [runtime refresh](runtime-asr-recovery-refresh-result.json).

Activation read-back confirms checkout Studio PID **98947** is healthy and idle, with its saved Gemini Live/Gemini/Edge TTS configuration and Smart Turn preserved. No physical call or external message was placed for this work.

## Remaining goal

This phase repairs a demonstrated incomplete-request failure mode and restores recognition after a transient connection loss. It does not qualify every failure path or guarantee human conversation quality. TTS/fatal recovery, longer noisy and echo-heavy sessions, physical EN/FR call recordings and first-heard measurements, device disconnect attribution, staged Android deployment, verified external actions and frozen-media qualification remain outstanding. The overall goal remains active.
