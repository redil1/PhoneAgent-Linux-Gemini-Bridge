# Generation failure recovery

## Result

Terminal model failures now produce one short recovery response for the current caller turn, in English or French, through the guarded speech path. Interrupted or obsolete generations cannot speak a recovery over a newer turn. A failure during a partial tool block discards that block without executing it or automatically retrying the action.

The checkout Studio was refreshed while idle and verified healthy with its saved provider/task settings preserved. Activation evidence: [runtime read-back](runtime-generation-recovery-refresh-result.json), PID 98259. Selected providers remain Gemini Live STT, the Gemini desktop model bridge, and Edge TTS; Smart Turn remains enabled.

## Reproduced gaps and changes

Previously, the pipeline error handler logged a model error but had no caller-facing recovery path. The opted-in model adapters can report terminal generation failure without a usable reply. New per-generation identity and acoustic-turn metadata scope error handling to the request that failed. Error notifications and response-end events are ordered through the tool and response-policy processors. Duplicate error/end events and late background failures cannot consume or corrupt the next generation.

The response policy discards unfinished text from a failed generation. Already released speech remains subject to the existing interruption and acknowledgement accounting. Recovery reserves its own response identity and rechecks the active turn after asynchronous finalization; abandoned reservations are discarded. Caller terminal intent uses the existing acknowledged goodbye path. No automatic tool retry is introduced.

Opt-in adapters are `antigravity_gemini`, `gemini_cli`, and `ollama_native`. This is a terminal-generation-error contract, not a universal retry mechanism for every provider error.

Cloud and local recognition faults now travel upstream as attributed STT errors instead of downstream where playback handling could misclassify them. Empty-transcription repair uses the current reply language and downstream guarded output. Automatic ASR reconnection and invalidation of all partial recognition state after provider failure remain separate work.

## Validation

- Final isolated suite: **1,088 passed, 1 failed, 39 skipped, 1 deselected**. The failure is the existing frozen-WhatsApp integrity check for `ai_bridge/whatsapp_client.py` and `ai_bridge/whatsapp_phone_client.py`. The frozen manifest was not changed.
- Final focused generation/tool/local tests: **29 passed**. Additional stream and recognition boundary tests passed in the broader focused run.
- Generation tests cover current versus obsolete turns, duplicated errors, EN/FR repair, caller goodbye, caller resuming during finalization, partial tool protocol without execution/requeue, and late background error isolation. Real Pipecat queues are exercised; tool effects are mocked.
- Changed-file Ruff, configured Pyright, strict checking of the new recovery module, and compilation passed. This does not claim repository-wide lint/type cleanliness.

| Replay | EOF to first simulated phone audio | Barge-in to output flush | Correction transcribed/answered | Playback accounting |
|---|---:|---:|---|---|
| Normal providers | 2,487.4 ms | 166.6 ms | Yes / yes | Resolved |
| Injected immediate model failure, real STT/TTS | 2,093.4 ms | 154.6 ms | Yes / recovery utterance | Resolved |

These are single synthetic English replays using a simulated phone clock, not physical first-heard measurements or a latency distribution. The fault is injected immediately, so 2.09 seconds does not measure a real provider timeout. The failure replay generated one repair for each current turn and invoked no tools. The normal replay attempted a WhatsApp tool whose external effect was disabled. No messages, CRM writes, or physical phone calls were performed by these tests.

Artifacts: [normal replay](pipeline-normal-generation-recovery.json), [fault replay](pipeline-generation-failure-replay.json), [replay harness](benchmark_pipeline.py). Test source: `tests/test_generation_recovery.py`.

## Remaining objective

This closes a specific silence/stale-generation failure path. It does not establish human-level reliability. TTS/fatal failure recovery, broader ASR recovery, long/noisy/echo conversations, physical phone and final-goodbye correlation, staged Android deployment, and explicit frozen-media qualification remain outstanding. The overall requirements ledger remains active.
