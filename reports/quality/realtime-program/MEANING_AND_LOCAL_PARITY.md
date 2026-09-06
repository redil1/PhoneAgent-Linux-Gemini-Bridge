# Meaning-preserving repetition checks and local turn-controller parity

2026-09-05. The full voice-quality goal remains active.

## Reproduced correction suppression

Eight controlled probes showed that the old approximate repetition guard rejected meaningful changes: price, day, customer name, negation, clause order and added information. Examples included “thirty nine” changing to “fifty nine” and “can” changing to “cannot.” Character similarity, substring containment and word-set overlap were not sufficient evidence to suppress speech.

The hard repetition guard now uses normalized exact matching against acknowledged previous speech and the current draft. It no longer guesses semantic equivalence from approximate similarity. Multi-sentence remembered responses retain their exact sentence units too, so subsequently streaming the same sentences still detects exact duplication. Existing repetition-request exceptions remain intact.

All eight correction probes now pass. Exact-repeat and within-draft-loop regressions still pass. This deliberately prioritizes preserving meaning: near-paraphrase repetition remains a conversation-quality concern rather than an automatic speech veto. It does not claim to resolve every possible repetition or dialogue-policy issue.

## Consistent buffered local control

The CPU/CUDA Whisper, distilled Whisper, Parakeet and unsupported-French SenseVoice fallback routes previously used an older buffered/RMS controller. They now use the same `LocalNeuralSTTService` acoustic ownership and stale-snapshot logic as the MLX route. Local recognition no longer loses the shared Silero/Smart Turn/floor protections merely because the selected decoder changes.

The local incomplete-turn default is now 3000 ms, consistent with the current conversational profile; explicit legacy local endpoint values remain honored. MLX retains its existing shared endpoint configuration. The English SenseVoice research path still uses its legacy service and is not claimed as qualified here.

## Slow decoding exposed a second watchdog conflict

A real CPU Whisper replay took approximately 13.5 seconds to produce its first transcript. Pipecat's default five-second turn-stop watchdog had already stopped that turn, leaving the later transcript stranded before another fallback handled it. This added orchestration delay on top of the slow decoder.

Local decode awaits now have a 20-second deadline. The aggregator's fallback budget is derived from that deadline and allows one stale in-flight decode before the current snapshot gets its own await budget. Its default is 44 seconds; ordinary completion still occurs as soon as the authoritative local transcript/stop handshake is ready. This fallback setting is not an intentional 44-second conversational pause.

The local service explicitly declares its committed-transcript contract, including fallback routes, and the aggregator uses `CommittedTranscriptStopStrategy`. A wiring regression verifies that the decoder budget and correct strategy reach the actual aggregator.

Native inference running on an executor thread is not forcibly stopped by an asyncio timeout. Its obsolete result is discarded. More complete provider-failure recovery remains outstanding.

## Actual CPU replay result

The final CPU replay completed both caller turns and playback accounting, with:

- **15993.2 ms** from source audio EOF to first simulated phone playout.
- **182.1 ms** from correction speech onset to output flush.
- Both turns finalized through the committed-transcript strategy rather than the premature watchdog fallback.

A 60-second correction observation window was used to observe the slow CPU path completely; earlier 20/30-second observation failures are retained. This is **not** a relaxed low-latency pass criterion. The measured 16-second first response is unsuitable for the requested real-time experience on this Mac. Earlier faster component numbers and the current end-to-end result measure different stages/configurations.

Gemini remains selected. No customer call, message or CRM write was made by these probes; external action handlers were disabled, and phone timing was simulated.

Evidence: `pipeline-benchmark-whisper-cpu-before-debounce.json` (initial failure; the filename predates the identified watchdog cause), `pipeline-benchmark-whisper-cpu-single-budget.json`, `pipeline-benchmark-whisper-cpu.json`, and `whisper-shared-controller-summary.json`.

## Verification

Focused controller/configuration/meaning/repair tests: 123 passed. Full isolated suite: **1082 passed, one frozen-WhatsApp integrity failure, 39 skipped, one device test deselected**. Changed-file Ruff, configured Pyright and Python compilation passed. The frozen files and manifest were not modified.

Activation/read-back evidence: `runtime-meaning-parity-refresh-result.json` and `runtime-meaning-parity-health.json`. Remaining work includes graceful provider-failure recovery, broader recorded/noisy/echoing and long-conversation evaluation, physical qualification and staged Android deployment, and the separate frozen-media integrity issue. No human-equivalence guarantee or CPU speed win is claimed.
