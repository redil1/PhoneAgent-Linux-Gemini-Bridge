# Continuous Silero VAD and French interruption findings

## Result

The slow French barge-in was reproduced with byte-for-byte input tracing. The
PCM accepted by the phone input transport matched the PCM consumed by STT, and
no correction audio was queued ahead of its measured start. The remaining
delay was in speech detection rather than transport buffering.

Installed Pipecat 1.7 resets Silero's recurrent ONNX state every five seconds
of wall time. That timer can expire during the first word of caller speech. A
controlled phase probe using the real French correction changed onset from
280 ms to 700-740 ms when the reset landed at several positions during the
word. The same PCM and the same 0.7 confidence / 120 ms start settings were
used. The result depends on reset phase because Silero is a recurrent model.

`StreamingSileroVADAnalyzer` retains the installed model and VAD state machine
but removes the wall-clock reset. Its state and context arrays remained fixed
at `(2, 1, 128)` and `(1, 64)` during the continuous screen. State is reset at
call/session initialization and once when the assistant first takes the floor,
provided caller speech has not claimed it. Duplicate bot-start frames do not
reset twice; a bot-start racing confirmed caller activity cannot reset speech.

The assistant-floor reset makes each possible barge-in independent of caller
audio from the previous turn. It is an ordered call boundary, rather than a
timer that can fire inside any word.

## Measured interruption path

Five real-provider runs used the same synthetic noisy French first turn and the
same clear French correction. Phone playout and acknowledgements were simulated;
all external tool effects were disabled.

| Run | EOF to first simulated audio | Correction to flush |
|---|---:|---:|
| 1 | 2,530.7 ms | 218.0 ms |
| 2 | 2,166.7 ms | 222.7 ms |
| 3 | 2,260.6 ms | 229.4 ms |
| 4 | 2,347.9 ms | 219.6 ms |
| 5 | 2,248.1 ms | 217.6 ms |

Median barge-in-to-flush was **219.6 ms**, range **217.6-229.4 ms**. Every run
transcribed and answered the correction, resolved playback accounting and
invoked no tools. Captured input bytes exactly matched the fed input prefix in
every trace. A separate default-cloud run measured **217.0 ms** to flush, while
also completing the next turn and playback accounting.

Earlier runs of the same French correction measured approximately 700, 731 and
769 ms. One traced pre-fix run saw an initial cluster briefly cross 0.7 but fail
to remain above it for the required duration; sustained speech was recognized
only around 700 ms. The post-boundary-reset runs consistently reached the
interruption path near 220 ms.

These measurements begin when the synthetic correction is queued into the
20 ms phone clock. They are not physical mouth-to-silence measurements. The
flush callback itself is simulated, so Android/network/device latency remains
outside this evidence.

## Sensitivity screen

An offline screen covered 38 synthetic EN/FR speech cases at clean, 20 dB and
10 dB stationary noise levels, plus silence, noise-only, tones, dual tones,
modulated noise and clicks. It tested confidence thresholds 0.7/0.6/0.5 and
start durations 120/80 ms. Lower thresholds were faster in these fixtures and
did not trigger the six non-speech controls.

A second continuous screen retained one real Silero state for approximately
1,335 seconds of synthetic audio per profile. It interleaved 13 speech cases
with six non-speech controls over three rounds, for 234 control presentations.
Neither 0.7/120 ms nor 0.6/120 ms nor 0.6/96 ms produced a control false start.
At 0.7/120 ms, PCM-window alignment alone moved the French correction between
140 and 700 ms, and its quiet version between 140 and 740 ms. At 0.6/120 ms,
the ranges fell to 140-280 ms for both levels. The 0.6/96 ms candidate ranged
100-260 ms.

Production sensitivity remains **0.7/120 ms**. The safe boundary reset already
meets the tested sub-250 ms target, while the synthetic controls are too narrow
to justify a global sensitivity change. Recorded carrier noise, echo, music,
breathing and diverse voices are required before lowering the threshold.

## Pipecat behavior and validation

Pipecat's VAD analyzer consumes 512 samples per Silero inference at 16 kHz,
tracks consecutive positive frames according to `start_secs`, and requires
both confidence and volume thresholds. The project continues to use that state
machine. Only recurrent-model reset ownership changed.

The configured `VADUserStartedSpeakingFrame` and `VADUserStoppedSpeakingFrame`
now report the analyzer's actual start/stop settings instead of hard-coded
values. This matters to downstream timing observers if the profile is later
qualified and adjusted.

Focused VAD/STT/corroboration tests passed **70 tests**. They cover timer-free
inference, explicit session reset, invalid probabilities, assistant-boundary
reset, duplicate bot-start frames, caller-floor protection, cloud/local turn
behavior, and uncertainty routing. The new VAD module passes strict Pyright.
Final repository and activation results are appended after completion.

## Evidence and remaining scope

- [Reset-phase probe](vad-reset-phase.json)
- [Sensitivity matrix](vad-sensitivity.json)
- [Continuous state/control matrix](vad-continuous.json)
- [Initial continuous diagnostic](vad-continuous-initial-control-window.json)
- [Default cloud pipeline trace](vad-safe-reset-default-cloud.json)
- [Five repeated pipeline traces](vad-safe-reset-run-1.json)

The repeated traces continue through `vad-safe-reset-run-4.json`; the first row
in the table is the separately traced initial post-fix run. Harness source is in
`benchmark_pipeline.py`, `benchmark_vad_sensitivity.py`,
`benchmark_vad_continuous.py`, and `probe_vad_reset_phase.py`.

This closes the reproduced recurrent-reset and French barge-in defect within
the synthetic/simulated-phone scope. Physical phone timing, echo/device behavior,
recorded EN/FR voices, the noisy French recognition gap, alternative provider
qualification, staged Android deployment, verified external actions and frozen
WhatsApp requalification remain part of the active end-to-end goal.

Final validation: **1,188 passed, 1 failed, 39 skipped, 1 deselected**. The
sole failure is the existing frozen-WhatsApp integrity check for
`ai_bridge/whatsapp_client.py` and `ai_bridge/whatsapp_phone_client.py`; neither
those files nor the frozen manifest changed. Changed-file Ruff, configured
Pyright, strict Pyright for the new VAD module, compilation, feature-flag
governance and migration inventory validation passed.

[Activation read-back](runtime-streaming-vad-refresh-result.json) confirms
Studio PID **15213** is healthy and idle. Saved Gemini Live STT, Gemini LLM,
Edge TTS, task and turn settings were retained, and optional local corroboration
remains disabled. The read-back records SHA-256 hashes of both loaded-source
files used by the refreshed checkout.
