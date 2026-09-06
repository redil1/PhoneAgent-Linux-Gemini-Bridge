# Turn-management implementation and verification

The updated controller is active in the checkout-based WebUI service. Final read-back showed healthy/IDLE Studio, a ready voice worker, and `configuration_current=true`. Existing provider, language, voice, task and speculation selections were preserved. No physical calls were placed.

**Implemented behavior**

- Antigravity Live now uses local Silero speech detection to claim the caller's floor and cancel pending output before waiting for cloud transcription.
- A tentative VAD start blocks a new turn commitment without permanently invalidating a currently playing answer. Confirmed speech triggers interruption even when the AI is still preparing its response.
- Smart Turn v3.2 examines short replies and unfinished phrases as well as complete-sounding sentences. Analysis carries the captured speech/turn/transcript revision, so late results cannot describe newer audio.
- Defaults are 600 ms for acoustically confirmed completion, 900 ms for uncertainty, and 3000 ms bounded patience for incomplete material. These are controller waits, not guaranteed end-to-end response times.
- Speculation remains available but cannot accelerate the authoritative speaking deadline.
- Artificial silence injection during live pauses was removed. Capture order is retained. Missing media, pending VAD work and recognition-input backpressure block commitment.
- Final commitment revalidates acoustic activity after awaited work. Same-epoch cumulative transcription revisions no longer become orphan suffix turns.
- The new output guard attaches a turn permission to each synthesis context and rejects stale audio/text at the transport boundary. It handles ordinary responses and direct greetings, preambles, recovery prompts and closings.
- Empty confirmed speech has bounded recovery; canceled audio is never silently resurrected. A fresh clarification goes through normal response policy.
- Conversation repair no longer emits a rejected original line after successful recovery. Temporary retry instructions are removed once resolved.
- WebUI configuration now exposes the active turn-management diagnostics and Smart Turn controls; worker environment export includes the completion threshold.
- Test defaults are isolated from the operator's persona, identity, tasks and memory while retaining explicit fixture paths.

**Verification**

| Check | Result |
|---|---|
| Focused controller/pipeline/configuration set | 185 passed |
| Final race, repair and WebUI set | 147 passed |
| Final complete isolated non-hardware suite | 889 passed, 13 existing failures, 39 skipped, 1 hardware test deselected |
| Changed-source/test Ruff | Passed |
| Configured Pyright scope and Python compilation | Passed |
| Real local Silero + Smart Turn synthetic speech scenarios | 4/4 integration assertions passed |
| Final WebUI and owned voice worker | Healthy, IDLE, ready, configuration current |

The test sets overlap; their counts must not be added as independent coverage. Remaining full-suite failures concern existing frozen WhatsApp drift, Android retry qualification, absent audio corpus, absent CI workflow, migration and S2S inventory. They were present before this work and remain release-qualification limitations. The WhatsApp verifier was run before and after and still reports the same two protected files; these files and Android media code were not edited.

The real-detector smoke cases use local synthesized English/French audio, injected fixture transcripts, and no network speech provider. Both 1.2-second mid-thought pauses retained the full thought and yielded one final turn. Clear cases committed after about 600–620 ms. One completed English continuation received an incomplete score of 0.412 and waited about 2.9 seconds. This is an observed classifier/latency tradeoff, not a claimed human-speech accuracy result. See the [smoke report](/Users/aziz/Desktop/phone-agent-linux/reports/quality/turn-taking-fix/real-local-detector-smoke.md).

**Runtime activation**

The original service ran directly from this checkout, not from the installed macOS LaunchAgent. The usual installer targets Python 3.12 while this checkout requires Python 3.11 and would change deployment shape, so it was not used for this source refresh.

An idle-checked refresh was prepared with preservation of process environment and an automatic health-failure rollback. The first attempt exceeded its graceful-shutdown wait before starting a replacement. Subsequent checks found the old process gone and a new checkout process (PID 75019) already serving the updated configuration. Its launch origin was not established. This process was verified independently; it was not restarted again. No runtime source rollback was executed.

Read-back confirmed 600/900/3000 ms configuration, Smart Turn enabled at its native 0.5 threshold, speculation enabled without a shorter deadline, and generic reflexes disabled. The worker was ready and matched Studio configuration. Details are in [runtime verification](/Users/aziz/Desktop/phone-agent-linux/reports/quality/turn-taking-fix/runtime-refresh-result.json).

Source-only rollback copies are retained at `/private/tmp/phoneagent-turn-fix-before`; that is a temporary location, not a durable backup. The new runtime files are [Antigravity STT](/Users/aziz/Desktop/phone-agent-linux/ai_bridge/antigravity_live_stt.py), [speech output guard](/Users/aziz/Desktop/phone-agent-linux/ai_bridge/speech_floor_guard.py), [policy](/Users/aziz/Desktop/phone-agent-linux/ai_bridge/agent_policy.py), and [pipeline wiring](/Users/aziz/Desktop/phone-agent-linux/ai_bridge/production_pipeline.py).

**Remaining limits**

No Android device was visible over ADB, so new device qualification was unavailable. The changes have not been qualified on physical calls, real provider timing, caller accents, far-end echo, or live French/English switching. Local VAD and Smart Turn classification can still err. The implementation fixes the reproduced control-flow and audio-release defects; “100x” or near-perfect human conversation has not been measured or established.

The next evaluation should use repeated authorized calls with deliberate hesitation, continuation, interruption, quiet speech, short acknowledgements, and code-switching. Measure early audible starts and interruption-to-playback-stop alongside normal reply latency. These results should guide any threshold adjustment rather than tuning to one synthetic example.

Machine-readable evidence: [source hashes and checks](/Users/aziz/Desktop/phone-agent-linux/reports/quality/turn-taking-fix/implementation-evidence.json).
