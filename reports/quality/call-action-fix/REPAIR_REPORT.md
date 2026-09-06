# Repair of the 5 September 00:01 call

The changes are active in Studio. Final checks show healthy/IDLE status, a ready voice worker whose configuration is current, and an enabled, ready OpenWA session. Existing task, voice, providers and turn-management settings were retained.

**What the log established**

| Evidence | Diagnosis |
|---|---|
| 00:01:01: final catalog contained `end_call`, `load_agent_skill`, `web_research` only | The model had no WhatsApp send tool during the reported call |
| OpenWA revision 16: main `enabled=false`; four individual tools enabled for the active task | The missing connection was the global OpenWA switch, not the task allowlist |
| Existing OpenWA service health and authenticated session read-back succeeded | The saved session did not need re-pairing |
| 00:01:43: eight-character non-final transcript committed after a complete Smart Turn verdict | “I prefer” was accepted as a turn, then the model supplied a preference the caller never stated |
| No WhatsApp tool execution appeared during the send/confirmation exchange | The final apology was not a failed delivery; no send was attempted |
| Frappe tools configured and allowed, but existing service containers stopped for five days | The CRM API was unavailable even though tool permissions were enabled |

The checkout link and plan values came from the active task's existing knowledge. They were not invented during this repair or independently revalidated against the storefront.

**Implemented corrections**

- Recognize bare preferences such as “I prefer”, “I would prefer” and “Je préfère” as unfinished, preserving continuation patience even if the acoustic classifier predicts complete. If the bounded wait ends with the choice still missing, ask a brief clarification rather than choosing movies, sports or a plan for the caller.
- Compile messaging instructions from actual connected capabilities. Possessing the caller number no longer implies that a message can be sent. A clear yes to an immediately preceding send proposal should not trigger repeated consent questions.
- Reject promises to send when no messaging tool is available. Feed streamed-response policy violations into evaluation instead of silently discarding them.
- Track verified WhatsApp acceptance/delivery evidence for the current response turn. Accepted requests permit a sent claim, but do not authorize delivery claims, appointment claims or other unrelated actions. Replace unsupported arrival predictions with a request for the caller to acknowledge receipt.
- Refresh both the system prompt and emitted tool instructions after connection configuration changes, and invalidate speculative answers prepared against the old tool catalog.
- Normalize a model's flat tool-argument layout against the declared schema. Both `{"name":...,"arguments":{"text":...}}` and the observed `{"name":...,"text":...}` are understood. Mixed or undeclared fields remain errors; malformed calls receive bounded corrective feedback.
- Reset the tool-iteration budget at each new caller turn instead of exhausting one budget across the whole conversation.
- Report unavailable tools after integrations finish attaching, avoiding the misleading pre-attachment “no implementation” warning for known integration tools.
- Remove optional model-selected recipient fields from OpenWA's current-caller schemas. Missing authenticated caller metadata now makes messaging unavailable; it does not request an arbitrary replacement recipient from the model.

**Restored services/configuration**

OpenWA's existing main connection was enabled, advancing its revision from 16 to 17. The individual tool settings, credentials, task scope and saved session were preserved. Active WhatsApp capabilities are send text, read current chat, reply, and delivery-status lookup.

The existing Frappe database, Redis dependencies, backend, websocket and loopback frontend containers were started with their existing configuration and volumes. Health returned the CRM, ERPNext, Helpdesk, Telephony and PhoneAgent integration applications. Scheduled campaign workers and the old duplicate OpenWA container were not started as part of this tool-API restoration.

A read-only construction of the active task runtime now produces **20 tools**, including **13 business tools** and **4 WhatsApp tools**, plus call completion, skill loading and web research. No catalog handler was invoked during that readiness check. See [effective catalog](/Users/aziz/Desktop/phone-agent-linux/reports/quality/call-action-fix/effective-catalog.json).

**Verification beyond mocked tests**

The configured Gemini bridge was given English and French replays of an approved offer-send exchange using the active task's prompt and the full 20-tool catalog. Both selected `whatsapp_send_text_current_customer` and included the configured Advanced-plan price, duration and checkout link.

The first French replay exposed an additional real model behavior: `text` appeared at the top level rather than inside `arguments`. This was the reason for the schema-checked compatibility fix, which is covered by regression tests. A second observed behavior was an unsupported “you should receive it shortly” prediction after an accepted-only fixture result; the speech guard now replaces that sentence without claiming receipt.

Message execution was disabled in these model replays. The confirmation results supplied to the model were fixtures. They are not evidence of a real message, delivery or read receipt. See [replay results](/Users/aziz/Desktop/phone-agent-linux/reports/quality/call-action-fix/send-decision-replay.json) and [replay script](/Users/aziz/Desktop/phone-agent-linux/reports/quality/call-action-fix/replay_send_decision.py).

**Final checks and practical limits**

- Complete isolated non-hardware suite: **903 passed, 13 pre-existing failures, 39 skipped, 1 hardware test deselected**.
- Final binding/tool integration subset: **54 passed**. Other focused subsets also passed; counts overlap and should not be added.
- Changed-file Ruff, compilation and configured type-check scope passed.
- Studio was refreshed while idle and independently read back as healthy, with a ready/current voice worker.
- No real WhatsApp messages, customer calls or business documents were created for testing.

The existing failures still concern frozen WhatsApp drift, Android qualification, missing qualification audio/CI files, migration and inventory. They are recorded in [verification evidence](/Users/aziz/Desktop/phone-agent-linux/reports/quality/call-action-fix/verification.json). This is not a full production-release qualification or proof of physical message delivery.

The burst of phone-uplink errors begins after the connection closes around 00:03:02, after the messaging failure in the transcript. It is a separate transport teardown symptom, not evidence that a WhatsApp send failed. The cellular media implementation was not changed in this repair.

Style/fidelity scores remain distinct from task completion. The corrected checks account for modeled policy violations, but an actual tool result and recipient acknowledgement are still the evidence for a completed send/delivery.

Source and private configuration rollback snapshots are under `/private/tmp/phoneagent-call-action-fix-before` (temporary storage). Final activation details are in [runtime refresh](/Users/aziz/Desktop/phone-agent-linux/reports/quality/call-action-fix/runtime-refresh-result.json).
