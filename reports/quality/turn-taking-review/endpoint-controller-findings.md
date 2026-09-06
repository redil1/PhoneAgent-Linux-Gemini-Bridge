# Endpoint controller audit — 2026-09-04

Scope: read-only audit of the checkout's `ai_bridge/antigravity_live_stt.py`,
`ai_bridge/turn_continuity.py`, the installed Pipecat 1.7.0 Smart Turn source,
and relevant tests. Only report artifacts were added. Offline probes use
invented transcripts and synthetic bytes; they never start a session, load
the ONNX model, send audio, or place a call. These are implementation
reproductions, not proof that a particular observed call hit each defect.

## Findings

### High: a resumed caller can lose the floor because commit uses stale silence

The watchdog calculates `silence_elapsed` and `stable_elapsed` at STT lines
895–897, then can yield while enqueuing a lookahead flush (910) or calling the
speculation handler (941–944). It reuses those old elapsed values at 946–952.
The commit lock at 835 checks the transcript update timestamp (838–842), but
does not recheck the speech epoch, last speech timestamp, current audio
activity, or turn decision generation.

The offline probe resumes speech inside an awaited candidate callback. It
advances the acoustic epoch from 1 to 2 without changing the ASR text (audio
normally precedes recognition). The watchdog then commits the previous text
0.070 ms after the new speech. This proves the missing invariant: fresh speech
must invalidate a pending end-of-turn even if the transcript is unchanged.

This is a scheduling reproduction using a fake yielding callback. Production
wires `SpeculativeTurnCoordinator.consider` at production_pipeline.py:943–945;
that callback can yield in `consider -> cancel -> asyncio.gather` at
speculative_turn.py:99 and 150–152 when previous speculation is active. A full
production timing trace is still needed to estimate how often the race occurs.

Related ordering risk: `run_stt` waits for speculation cancellation at
963–966 *before* recording the resumed speech at 969. Activity and turn
generation should be recorded synchronously before any potentially yielding
work. Revalidate the turn under its lock immediately before publishing it.

### High: the synthetic ASR flush can precede already captured caller audio

STT lines 899–910 enqueue 200 ms of zero PCM at 120 ms of perceived silence.
They do not drain `_audio_buffer`. The normal sender only removes that buffer
when it reaches a full 150 ms chunk (271; 977–986).

The offline probe stages 20 ms of captured speech tail followed by 120 ms of
natural silence (140 ms buffered). The watchdog enqueues synthetic silence as
sequence 0. The next input frame completes the regular chunk; the speech tail
is queued as sequence 1. The recorded result is 6,400 zero bytes before a
4,800-byte chunk beginning with previously captured speech. This is a real
ordering defect in the local queue, not a claim about any particular provider
response. It may encourage segmentation before the actual trailing audio.

Remove artificial mid-turn silence from normal continuous streaming, or at
minimum preserve the captured-audio order and qualify the provider behavior.

### High: silence and ASR finality are used as proxies for a finished idea

With speculation enabled, concise answers and any provider-final hypothesis
use `_speculative_fast_endpoint_sec` (797–803). The source default is 200 ms
(253), ambiguous default is 350 ms (254), transcript stability is 100 ms
(245), and incomplete patience is 1,400 ms (255). These are source defaults,
not measured call timing. A non-final hypothesis with terminal punctuation
can still be committed without waiting for a provider final (805–806 and
946–952); the watchdog never requires provider finality.

`Yes`, `no`, `hello`, etc. bypass acoustic Smart Turn evaluation at 917, based
on turn_continuity.py:174–182. They can start a longer answer as easily as they
can end one. The text heuristic inspects suffixes rather than conversation
context (turn_continuity.py:185–227). A completed grammatical sentence also
does not necessarily mean that the caller's explanation is complete.

Recommended direction: context-aware listening policy and graded uncertainty,
with configurable conversational patience; retain early speculative compute
behind a separate, stricter authorization to speak.

### High: endpoint and interruption clocks do not represent actual voice activity

The local endpoint clock uses only RMS energy versus a fixed threshold
(STT 959; 661–678), default -42 dBFS (248). Quiet speech below that value does
not advance the clock. A synthetic -48 dBFS activity probe confirms this
mechanical behavior; it does not establish the actual caller's loudness.
Noise above the threshold can conversely hold a turn open. Missing input
frames are counted as silence by elapsed wall time (895–897), even though
they could indicate a capture or transport stall.

Caller-start timing begins on the first transcript (710–712), and interruption
waits another `barge_in_min_ms` after that (591–600), default 220 ms (247).
Consequently, this adapter's own interruption adds ASR latency to the configured
delay. Upstream pipeline behavior must be considered before attributing all
observed interruption latency to the adapter.

Recommended direction: use real speech VAD with hysteresis and per-route audio
calibration, distinguish missing media from received silence, and emit
speech-resume cancellation from acoustic activity before waiting for ASR.

### Medium: Smart Turn is advisory and can fail toward premature completion

The classifier is run once per acoustic epoch after >=180 ms silence and
>=400 ms buffered audio (912–927). A result below 0.5 merely selects a fixed
1,400 ms deadline; the watchdog does not require a later complete result
(770–785; 795–799). A pending inference stops blocking after one second (929).
Initialization failure disables the classifier (342–344), and inference
failure returns `probability=1.0`, the same value as certain completion
(749–758). Those outcomes should be represented as unknown/degraded, not high
confidence that the person has finished.

The project calls the private `_predict_endpoint` directly (755), bypassing
the installed analyzer's public buffering/turn-state handling. Pipecat's
`BaseSmartTurn.append_audio` tracks received silence duration and keeps
speech state (base_smart_turn.py:99–151); its default maximum silence is
3 seconds (27; 41). This comparison does not prove the library default is the
right product setting, but shows that the custom adapter owns its own timing
policy rather than inheriting the public analyzer's policy.

### Medium: a same-epoch late extension becomes an isolated second fragment

The comment at STT 686–688 says post-commit hypotheses without new speech are
corrections. However, the prefix-extension branch at 695–698 strips the old
text before the same-acoustic-turn check at 700–708. The probe commits
`Please change my address`, then receives `Please change my address to London`
without a new speech epoch. It stages `to London` as a new candidate. A late
recognizer extension can therefore split one idea into a second request.

Use a turn-ID-aware transcript revision mechanism. Decide how to revise an
unspoken response or merge a resumed utterance with the interrupted turn;
do not universally discard corrections or universally promote them to new
turns.

## Existing test limitations

- `test_short_natural_pause_does_not_finalize_the_turn` at
  tests/test_antigravity_live_stt.py:392 selects 900/1,800 ms delays, so it does
  not qualify the much shorter source defaults.
- Smart Turn tests at 613 and 637 toggle `_smart_turn_incomplete` directly;
  they do not run real acoustic inference or a speech-resume scheduling race.
- The merge test at 357 supplies all transcript segments synchronously before
  the stream completes; it does not interleave recognition, ongoing audio,
  watchdog deadlines, LLM generation, and phone playback.

## Reproducible evidence

Run `.venv/bin/python reports/quality/turn-taking-review/offline_endpoint_probes.py`.
The observed JSON is saved in `offline-endpoint-results.json`. The probes show
the existing behavior; they are not acceptance tests asserting that behavior
is correct. Regression coverage should reverse the undesired outcomes after
the corresponding controller fixes, followed by recorded-audio evaluation
covering French/English, within-sentence pauses, self-corrections, short
backchannels, quiet speech, network stalls, and interruptions.
