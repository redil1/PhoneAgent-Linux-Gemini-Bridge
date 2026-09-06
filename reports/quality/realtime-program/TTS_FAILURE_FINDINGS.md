# TTS failures, bounded synthesis and playback identity

## Reproduced defects

1. **Partial speculative audio was treated as successful completion.** A decoder can publish a PCM prefix and then return an empty failure result. The old prefetch stream only recorded `done`, so an attached live consumer returned successfully after that prefix. The before/after probe emits one audio chunk in both versions: the old version reports zero errors; the updated version reports one. [Probe result](tts-partial-prefetch-probe.json), [probe source](probe_tts_partial_prefetch.py).
2. **Later sentences could continue after the same reply failed.** The first injected real-provider replay showed a failed first chunk followed by successful synthesis of the remaining sentence in the same context. Context failure now suppresses its remaining audio and full-text publication. The preserved [intermediate replay](pipeline-tts-failure-before-context-retirement.json) documents that behavior.
3. **A zero-audio failure could delay the next reply.** Installed Pipecat 1.7 infers synchronous TTS from whether a generator yielded PCM. With zero PCM, it can treat the context like an asynchronous websocket request and wait for its idle timeout. Edge now explicitly closes its synchronous context even when it fails before producing audio. A real Pipecat queue test verifies the next request proceeds within one second, using deterministic synthesis.
4. **Failure and playback callbacks could target the wrong reply.** A queued TTS failure previously failed whichever response was currently playing. Response IDs now travel from policy reservation through synthesis, error events, and the ordered media sender. Bot start/stop events capture their response identity at emission; late events from failed speech cannot consume the repair reply's pending ID. Deterministic transport/observer tests reproduce that ordering and verify correct attribution.

## Changes

Edge synthesis now tracks first-audio, inter-chunk idle and total deadlines: **3 seconds**, **2 seconds**, and **15 seconds**, respectively. The first-audio and total budgets span live retry attempts rather than restarting for each attempt. Prefetch and standalone PCM synthesis also have a total budget. Pipecat's context idle timeout no longer closes the context before the adapter can report its own deadline failure. Cleanup cancels the network writer and terminates the decoder; cancellation and consumer closure are tested. These are cooperative async deadlines, not guarantees against an unresponsive OS.

Speculative streams distinguish completion, failure and cancellation. Incomplete PCM is never cached as a complete sentence. A cancelled old prefetch task cannot remove a newer task for the same phrase. Failed contexts retain bounded tombstones, so subsequent chunks or automatically emitted TTSTextFrames cannot pretend the complete reply was heard.

A scoped terminal Edge TTS error marks its actual response failed and can queue one guarded EN/FR repair. It does not automatically rerun tools. Caller speech and stale epochs suppress obsolete repair. Failure of the repair itself does not create an apology loop. A later independent failure can still use the fixed repair wording after the earlier repair completed; normal answer repetition checks remain in place. If another non-interrupted response already has speech queued, it proceeds without adding a delayed apology behind it.

The policy, synthesis and transport changes preserve response identity for generated replies, greeting, tool progress, generation repair, empty-recognition repair and terminal goodbye. Greeting test doubles were updated to return the production identity-bearing finalization contract. Stale greeting/progress reservations are discarded.

## Provider replays

All replays use real cloud providers, a simulated phone clock, synthetic English speech and disabled external tool effects. Deterministic EN/FR tests exercise recovery language and state; these are not physical bilingual call trials.

| Replay | EOF to first simulated phone audio | Barge-in to flush | Result |
|---|---:|---:|---|
| Partial failure, context retirement and repair enabled | 2,168.8 ms | 159.8 ms | Failed reply marked failed; repair queued then interrupted; next request answered and playback resolved |
| Failure before PCM | 3,247.8 ms | 161.7 ms | Recovery speech reached playback about 492 ms after the error; caller interrupted it; next request answered and accounting resolved |
| First TTS request stalled | 5,634.1 ms | 171.0 ms | Deadline reported at 3,000.5 ms with zero PCM; another already-queued response became audible; next request answered and accounting resolved |
| Normal replay before final transport identity patch | 2,285.1 ms | 163.1 ms | Next request answered and accounting resolved |
| Normal replay with transport identity patch | 3,536.0 ms | 156.8 ms | Next request answered and accounting resolved |

The stall replay exposed the delayed-apology queue case; the subsequent skip rule is covered by a deterministic test. It has not been rerun as a separate live-provider comparison. The partial replay's first audio can include the failed prefix; its timing is not the time to hear the entire repair. Synthetic provider timings vary between runs; these single samples do not establish a latency improvement or regression caused by the identity patch.

Artifacts: [partial failure](pipeline-tts-failure-replay.json), [failure before PCM](pipeline-tts-empty-failure-replay.json), [stall](pipeline-tts-stall-failure-replay.json), [latest normal](pipeline-normal-tts-recovery.json), [earlier normal](pipeline-normal-tts-recovery-before-identity.json), [harness](benchmark_pipeline.py).

## Validation and remaining goal

Focused decoder/generation/playback tests passed before the final transport identity additions; the subsequent identity/lifecycle set passed 37 tests. Final full-suite and activation results are recorded below after completion. No external messages, CRM writes or physical calls were performed by this work.

This improves failure handling for the selected Edge TTS path. It does not qualify all alternative speech providers, guarantee provider availability, prove physical first-heard timing, or resolve device-side disconnect attribution. Physical EN/FR continuous/noisy/echo tests, staged Android deployment, verified external actions, frozen-media requalification and broader conversation-quality work remain outstanding. The full objective stays active.

Final isolated suite: **1,113 passed, 1 failed, 39 skipped, 1 deselected**. The sole failure is the existing frozen-WhatsApp integrity check on `ai_bridge/whatsapp_client.py` and `ai_bridge/whatsapp_phone_client.py`. Neither file nor the frozen manifest changed in this phase. Changed-file Ruff, configured Pyright with the checkout interpreter and Python compilation passed. An intermediate full run found two greeting fixture methods still using the old two-value stub; the fixtures now use the identity-bearing production contract and pass.

[Runtime activation read-back](runtime-tts-recovery-refresh-result.json) confirms checkout Studio PID **968** is healthy and idle. The Gemini Live/Gemini/Edge TTS selection, task and Smart Turn settings were preserved. The host output transport's identity metadata change was validated in deterministic tests and simulated-phone replays; physical media qualification remains outstanding.
