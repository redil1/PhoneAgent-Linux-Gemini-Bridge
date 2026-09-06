# Latency analysis: 5 September, 00:46–00:48 call

**Main finding:** the 11.9-second WhatsApp turn includes approximately five seconds of simulated typing inside OpenWA, followed by a measured three-second delivery-confirmation wait in PhoneAgent. These are application waits on the response path. Changing speech recognition cannot remove them.

This investigation read host logs, deployed OpenWA code/configuration, and ran offline queue/protocol probes. It did not change the running service or send a message. The analysed call used Gemini Live STT; the later Whisper selection is a separate configuration state.

**Reconstructed WhatsApp timeline**

| Boundary | Host time / interval | Evidence |
|---|---|---|
| Caller transcript committed | 00:47:44.274 | Timestamped STT log |
| Model's speculative result consumed | 00:47:46.491 | Timestamped cache-hit log |
| Commit → model/tool decision | About 2.217 seconds | Difference between those timestamps |
| WhatsApp tool returned | 00:47:55.239 | Timestamped execution log |
| Decision → tool return | About 8.748 seconds | Includes execution, API waits and confirmation wait |
| Delivery confirmation portion | 3.0011 seconds | Tool result's measured `delivery_wait_ms` |
| Remaining tool interval | About 5.7469 seconds | Includes typing, contact resolution, send and other overhead |
| After tool return → finalized confirmation text | About 0.9396 seconds | Residual from displayed total; approximate |
| Displayed response total | 11.9046 seconds | Recorded assistant metric |

The first two intervals are wall-log differences, not separately instrumented CPU/model/API spans. Structured child events have no timestamps, so surrounding log times must not be assigned to them as exact occurrence times. The final residual is approximate.

**The five-second typing delay is real**

The running `phoneagent-openwa` container has:

- `SIMULATE_TYPING=true`
- `SIMULATE_TYPING_MAX_MS=5000`

Its deployed `/app/dist/modules/message/message-send.service.js` calls `simulateTypingIfEnabled` before the engine sends the text. The method waits:

`min(5000, 500 + text.length × 45) × random factor from 0.85 to 1.15`

This message contained 195 characters, so the planned delay reaches the 5000 ms cap. Its possible simulated-typing delay is **4.25–5.75 seconds**, plus the typing-state update itself. The exact random draw for this request is not logged. This explains most of the 5.7469-second non-confirmation tool interval without attributing it all to the WhatsApp network.

The source checksum observed in the running container was `c3898ab631f4431059a5c6987561b1406b198702e0a541d30b6042ca91e2aad3`.

Link previews were also examined. The deployed Baileys adapter supplies `linkPreview: null` unless explicitly requested; PhoneAgent's send-text request does not request a preview. There is therefore no evidence that checkout-page preview fetching caused this delay. Send-rate/circuit-breaker controls are separate from simulated typing.

**A second deliberate wait adds three seconds**

`OpenWAToolRuntime._sent` waits for a delivery/read/played acknowledgement before returning. The current configuration allows 3000 ms, and this call consumed 3001.1 ms. It returned `accepted=true`, `delivery_status=accepted`, `delivery_confirmed=false`. A later status event said `sent`, which still is not a device-delivery/read acknowledgement.

An accepted request can be acknowledged honestly without holding the conversation for delivery confirmation. Background events can continue tracking delivery. Removing this blocking wait should not be implemented by falsely treating acceptance as delivery.

**The spoken progress message is blocked behind its own tool**

`ProductionCallPipeline._speak_tool_preamble` queues a `TTSSpeakFrame` at the beginning of the pipeline. But `ToolCallProcessor` is still processing the model response and awaiting the tool. The queued preamble must pass through that same blocked processor before reaching TTS.

The offline probe reproduced this order using real Pipecat frame queues and a mocked slow tool:

1. Preamble queued.
2. Tool starts.
3. Tool finishes.
4. Preamble reaches speech output.

Thus “One moment, let me check that” fails to fill the actual waiting period. The call log is consistent with this: the preamble-associated playback appears after the send result rather than during the long tool wait. The probe is control-flow evidence, not a measurement of phone audio latency.

**Speculation also synthesizes tool protocol text**

The speculative coordinator calls `preview_response` and TTS prefetch on every generated result, including `<tool_call>...</tool_call>` results. It does not recognize that these are executable instructions rather than spoken answers. The offline probe confirmed a WhatsApp tool-only result being passed to TTS prefetch after sanitization.

For this turn, the speculation event recorded about **2301.1 ms LLM work** and **2651.6 ms TTS work**. The TTS work overlaps the tool execution and must not be added again to the 11.9-second timeline. It is wasted preparation for a tool request and can compete with useful speech synthesis.

The “cache hit” label is also misleading as a speed indicator. `_consume_prefetch` can join and wait for an unfinished request, then log a hit. It means reuse of a request, not necessarily an immediately available answer.

**What the other response numbers mean**

The displayed metric is calculated from `AgentPolicyRuntime.observe_transcription` to response text finalization. It excludes the preceding speech-recognition/end-of-turn delay and is not a measurement of first audio heard by the caller. It can include tool waits and multiple model passes. TTS startup, playback queuing and phone buffering require separate measurements.

The Gemini LLM adapter uses `GetModelResponse` and awaits the complete JSON response before emitting its text. Streaming cloud STT does not make this LLM path token-streaming. Ordinary turns at 1.27–1.61 seconds therefore reflect a full model response plus processing, while a tool turn generally requires another model response after execution.

For database registration, the tool returned about 1.523 seconds after transcript commitment and the final response metric was 2.446 seconds. That is consistent with model decision + a short CRM operation + model confirmation. The logs do not isolate the database request itself, so the entire first interval must not be called database latency.

The goodbye performed both `business_record_call_outcome` and `end_call`, which explains why it is slower than a single plain-text reply.

**The failed goodbye marker is a separate issue**

- `end_call` was accepted around 00:48:41.271.
- The phone connection reset at 00:48:45.458.
- Subsequent uplink writes failed.
- Playback reporting failed around 00:48:45.861 because the final audio-end marker was unavailable.
- `terminal_completion_aborted` was emitted; no verified `call_completion` event was observed for this close.

This does not prove the caller heard no goodbye: partial audio may have played before the connection closed. Nor does it prove PhoneAgent's verified completion callback hung up early. The host logs do not establish whether the reset originated from a manual hangup, Android, the relay, or the network. Device-side evidence is needed to distinguish those causes. The host should classify closed-link playback as interrupted/disconnected rather than presenting only a generic marker failure, and cancel remaining output rather than draining it onto a dead link.

**Priorities for reducing this latency**

1. Remove simulated typing from the live-call messaging path, while retaining the existing send-rate and circuit-breaker policy.
2. Return after authenticated send acceptance and track delivery asynchronously. Keep confirmation language accurate.
3. Route a short, action-specific progress utterance through the guarded speech path without sending it through the processor awaiting that action.
4. Skip speculative TTS for tool-only outputs; retain useful LLM prefetch.
5. Add timestamps and durations around contact resolution, send HTTP, delivery wait, first/final LLM output, first TTS PCM and phone-render acknowledgement.
6. Evaluate a genuinely streaming/faster LLM adapter after the deliberate waits are removed.

Subtracting the known three-second wait and the configured typing range from this example gives an estimated **3.15–4.65 seconds** for the displayed metric, assuming other work remains unchanged. This is a counterfactual estimate, not a benchmark or promise. Further model and pipeline changes are needed for consistently lower latency.

**Whisper comparison status**

This call ran `antigravity_live`. The user later selected `whisper_turbo`; at 00:49:07 the log says `Whisper CUDA prewarm notice: No module named 'torch'`. The inspected checkout environment also lacks faster-whisper and CTranslate2. The worker's ready flag therefore does not establish successful local-model loading. No valid Faster-Whisper-versus-Gemini latency comparison has yet been completed, and that selection cannot explain the earlier WhatsApp delay.

Evidence: [timing reconstruction](/Users/aziz/Desktop/phone-agent-linux/reports/quality/call-latency-0047/call-timing-evidence.json), [offline probe results](/Users/aziz/Desktop/phone-agent-linux/reports/quality/call-latency-0047/offline-probe-results.json), [probe script](/Users/aziz/Desktop/phone-agent-linux/reports/quality/call-latency-0047/offline_latency_probes.py).
