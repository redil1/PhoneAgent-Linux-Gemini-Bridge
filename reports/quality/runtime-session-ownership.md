# Runtime session ownership audit

Source inspection: 2026-09-06. This classifies the live RuntimeConfig fields; it does not prove
complete host qualification or enumerate direct environment readers in every other module.

| RuntimeConfig field(s) | Current owner | Package/control status |
| --- | --- | --- |
| `providers` | RuntimeControl and private credential references | Provider fields are represented or privately reference-bound. |
| `task_id` | Active task contract | Derived from the package task ID. |
| `system_prompt` | RuntimeControl | Captured and compared by prompt digest in worker readiness. |
| `auto_answer` | RuntimeControl `auto_answer_enabled` | Operator policy, passed to the worker. |
| `memory_enabled` | RuntimeControl `memory_enabled` | Automatic caller history for subsequent calls; actual recall/writes also require an identifiable caller. |
| `event_stream_enabled` | Studio/worker event protocol | Studio forces event output on; not a business/persona control. |
| `device_id`, `control_host`, `control_port`, `protocol_control_port`, `rx_port`, `tx_port` | Qualified host/gateway topology | Version-7 host requirements and parsed-worker read-back; device selection is hashed. No business-package setter. |
| `sample_rate`, `frame_ms`, `input_queue_frames` | Qualified media/transport profile | Explicit 16 kHz/20 ms profile; queue size is wired to transport. Requirements/read-back bind values without authorizing arbitrary geometry changes. |
| `voice_lock_path` | Exclusive call ownership | Host-owned lock; resolved path hash participates in requirements/read-back. |
| `link_authentication_key` | Private host pairing | Private resolution; only its hash participates in requirements/read-back. Rotation invalidates an older stage. |
| `use_adb_forward` | Host connection topology | Studio derives the setting from relay ownership; included in requirements/read-back. |

There are 18 remaining RuntimeConfig fields. The unused `record_calls` field/environment flag was
retired. Actual recording uses RecordingConfig and per-call consent. Recording root, retention and
queue sizing, plus environment controls in other modules, still need explicit host/privacy ownership
and qualification. Memory enablement does not delete stored data or disable action/audit evidence.

Host/session values are now bound and compared, including private fingerprints. This does not prove
that the selected physical device, model/APK assets or services are qualified, nor that a nondefault
queue has acceptable measured latency. Next work: inventory remaining direct environment controls
and recording/privacy configuration, then complete physical/backend qualification. Source presence
and this table are not completion evidence for the entire configuration requirement.
