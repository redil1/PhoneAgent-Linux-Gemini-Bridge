# Local corroboration, refusal handling and uncertainty routing

## Outcome and rollout decision

The optional cloud/local corroboration path is implemented, wired through Studio configuration and the voice-child environment, and tested with real providers. It remains **disabled by default** because the local recognizer still produces false disagreements on some French proper names. The selected cloud provider is unchanged.

The independently useful fixes are active-source changes for all applicable callers: local capture retains two seconds of pre-roll; untrusted caller intent cannot execute model-requested tools; disputed recognition is replaced by an explicit uncertainty marker in model history; and a short guarded clarification is produced without asking the model to guess. Provider startup failures are fatal and observed by pipeline startup rather than silently treated as readiness.

## What the integrated tests exposed

The first local corroboration experiment appeared to agree with a wrong cloud transcript of a French refusal. Inspection showed VAD claiming the floor about **844 ms** after source onset, while the local capture retained only **250 ms** of pre-roll. Its verification snapshot could lose “Non, pas” and confidently agree with “à l'abonnement annuel”. Both LocalNeuralSTT and the corroborating controller now retain **two seconds** before VAD confirmation. A regression test reproduces delayed onset and verifies that the initial bytes survive.

With the longer buffer, the integrated verifier detected the refusal mismatch and marked the cloud text untrusted. It does not overwrite the caller's words with its own guess. The original uncertain cloud text remains visible in the audit transcript.

The next issue was downstream: `trusted_for_task=false` previously prevented task/memory updates, but generic tool execution only blocked selected literal-argument mismatches. Tools without such a mismatch could still execute. The tool runtime now checks uncertainty and caller-turn ownership before execution, including after awaited notification/grounding events. Tests cover WhatsApp, CRM, end-call and custom tools; none execute on disputed intent. An already-started external action cannot be undone by this guard.

Finally, the real French pipeline still produced an annual-plan sales pitch despite the uncertainty flag. A system-prompt hint was insufficient. Explicitly untrusted turns now receive a history marker and are intercepted between the user aggregator and the model/reflex path. One short EN/FR clarification is sent through the normal guarded TTS/output path. A speech-policy check also prevents a guessed substantive answer if another path reaches generation. Trusted meaningful turns remain model-driven.

## Corroboration architecture

Cloud recognition, VAD, Smart Turn and barge-in use one acoustic controller. A separate owned local decode checks a PCM snapshot after a 300 ms pause. Its identity includes the acoustic epoch, last-speech time and transport revision. Resumed speech, a connection gap or a completed turn invalidates the result. Only one native decode is outstanding; a timed-out turn does not enqueue a backlog behind it.

The local wait budget is 1.8 seconds after its 300 ms start pause. Missing, low-confidence, overflowed or disagreeing evidence yields an untrusted result. A complete local result gives a still-changing cloud partial time to catch up within that budget. Exact normalized agreement can support partial readiness; it does not bypass continuation patience or Smart Turn. The check never authorizes an action by replacing the primary transcript with a model guess. Known uncertainty also cancels speculative preparation instead of continuing to synthesize an unused answer.

The decoder resolves an already cached Hugging Face snapshot with `local_files_only=True`; model startup is bounded and fails explicitly if the cache or Apple GPU decoder is unavailable. No model download is initiated by this option. Native inference cannot be forcibly stopped by an async timeout; shutdown cancels the await, and worker/process lifecycle still owns ultimate resource cleanup.

Comparison normalizes supported punctuation, contractions, number/currency spellings and email orthography. It does not fuzzy-match changed prices, currencies, names, cancellation/renewal or negation. A small channel vocabulary hint includes competing terms—WhatsApp, web/site web, SMS and email—without supplying an expected refusal, price or consent. It improved the clean French WhatsApp request but did not resolve all noisy proper-name errors.

## Measurements and limits

All calls below are synthetic real-provider replays with a simulated phone clock and disabled external tool effects. No physical phone call, message or CRM write was performed by these tests.

| Replay | EOF to first simulated phone audio | Interruption to flush | Result |
|---|---:|---:|---|
| Initial English corroborated pipeline | 2,640.7 ms | 170.0 ms | Correction transcribed/answered; playback resolved |
| Noisy French, before enforced clarification | 7,298.0 ms | 769.0 ms | Untrusted transcript incorrectly received a sales pitch; no tool executed |
| Noisy French, with enforced clarification | 2,305.7 ms | 700.3 ms | “Pardon, pouvez vous répéter ?”; repeated full refusal recognized; no tool calls; playback resolved |
| Latest default cloud pipeline, corroboration disabled | 2,120.5 ms | 165.2 ms | Correction transcribed/answered; playback resolved |

These are individual observations, not stable percentile estimates or physical first-heard measurements. The French interruption result remains slow and is an explicit open issue. The 2.31-second uncertainty response is a direct repair, not a faster language-model generation.

In the 36-turn corroborated matrix before the vocabulary hint, 32 turns agreed. Four were untrusted: the genuinely misrecognized noisy French refusal and three otherwise correct French WhatsApp requests, which the local decoder heard as “web”. Median EOF-to-commit was approximately **1,726 ms**. The six-case French vocabulary experiment fixed the clean request but retained noisy disagreements. Independent website/email/WhatsApp clips showed that competing-channel hints did not simply force every output to WhatsApp, but also exposed additional French proper-name and grammatical recognition differences. The strict comparison was not weakened to pass them.

The first eight-case subset probes used noise seeds based on their selected-case order. Those paired pre-roll probes match each other, but are not identical noise realizations to every corresponding full-matrix row. The harness now records stable case-specific seeds and verification snapshot hashes/durations. This distinction matters when comparing artifacts.

## Governed experiment and remaining qualification

`antigravity_live_local_corroboration` defaults to false. Studio saves/loads it and passes `PHONE_AGENT_ANTIGRAVITY_LOCAL_CORROBORATION` to the voice child. It is registered as an expiring same-Cascade-graph experiment, with owner `voice-runtime`, review/removal target **M14-13**, and expiry **2026-10-05**. Independent recorded EN/FR trials, refusal/channel accuracy, added p95 delay and false-clarification limits must pass before promotion. This phase does not enable it for normal calls.

Open requirements include noisy French recognition, the observed ~700 ms French barge-in, representative recorded/echo/continuous physical conversations, broader provider/model selection, verified real tool delivery, staged Android deployment and frozen-media requalification. This is progress toward the original complete objective, not a human-equivalence certification.

## Evidence

- [Initial short-pre-roll probe](corroborated-noise-before-preroll.json), [longer-pre-roll probe](corroborated-noise-after-preroll.json), [36-turn matrix](corroborated-bilingual-matrix.json).
- [French reference diagnostics](corroborated-french-diagnostic.json), [vocabulary experiment](corroborated-french-vocabulary.json), [independent channel clips](corroborated-channel-holdouts.json).
- [English corroborated pipeline](pipeline-corroborated.json), [incorrect French response before repair](pipeline-corroborated-french-before-repair.json), [French repair replay](pipeline-corroborated-french-refusal.json), [latest default pipeline](pipeline-corroboration-default-final.json).
- `pipeline-corroborated-french-frame-diagnostic.json` preserves a failed intermediate harness run: attempting to pass Pipecat's `init=False` metadata field into `dataclasses.replace` raised an exception. Metadata is now copied after frame construction; dedicated tests pass.

## Startup availability finding

One later default replay could not discover the desktop bridge during app startup. The process inventory subsequently showed the standalone app and language server running, and its HTTPS root returned200. That failed replay is not latency evidence. Discovery previously checked only1.5 seconds after launching an app; it now polls within a bounded readiness window. Cloud discovery and local-model startup errors emit a fatal provider error. Pipeline startup races readiness against failure/worker exit, so a startup exception cannot leave a ready-but-silent call. Tests include a delayed bridge, failed models and a failure racing the ready event. The subsequent default provider replay completed successfully.

Final regression, typing, lint, registry and activation results are appended after verification.

Final validation: **1,184 passed, 1 failed, 39 skipped, 1 deselected**. The sole failure is the existing frozen-WhatsApp integrity check on `whatsapp_client.py` and `whatsapp_phone_client.py`; those files and their manifest were unchanged. Changed-file Ruff, configured Pyright, compilation, feature-flag governance and S2S inventory checks passed.

[Activation read-back](runtime-corroboration-refresh-result.json) confirms Studio PID **10534** is healthy and idle. Gemini Live STT, the Gemini desktop LLM bridge, Edge TTS and the task/turn settings were retained. Experimental local corroboration is explicitly **false**. The global uncertainty/tool/startup fixes and local pre-roll fix are loaded.
