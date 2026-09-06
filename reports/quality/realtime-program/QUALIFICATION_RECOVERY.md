# Qualification recovery and provider-configuration repair

2026-09-05. The overall voice-quality goal remains active.

## Restored protected audio without changing its manifest

The configured recording directory does not exist, and no managed recording manifests were found. Consequently, the supplied calls cannot be acoustically replayed from that location. Recorded English/French WebUI tests have been requested from the operator; no calls or messages were placed by this investigation.

The repository's protected synthetic corpus was missing all nine WAV files. Its existing generator and manifest identify eSpeak NG 1.50 and FFmpeg 4.4.2 on Linux x86_64. Rebuilding in an isolated Ubuntu 22.04 x86_64 container reproduced **all nine expected SHA-256 hashes exactly**. Only those matching audio files were copied into `qualification/corpus/v1/audio`. The original manifest, transcripts and event files were not replaced or re-signed.

The first public-image pull stalled in Docker's credential helper. That owned process was stopped and the public pull retried with a temporary Docker configuration, leaving the normal registry configuration unchanged. An initial generation attempt also correctly refused to overwrite the mounted output directory; generating into a new child directory resolved that setup error.

The corpus was previously absent from the package-data declaration too. The built wheel now contains all nine verified audio files, event/transcript JSON and the corpus manifest. The final wheel was inspected, and every packaged WAV still matches the original hash. The Mac contract profile is included as well.

Evidence: `corpus-recovery-comparison.json`, `corpus-final-wheel-verification.json`.

## Fixed provider-switch defaults and saved-state migration

A legacy saved configuration selecting Supertonic but omitting aggregation inherited the current environment's Edge-TTS `phrase` default. Validation rejected that incompatible combination and ignored the entire saved configuration, including the custom system prompt and auto-answer setting.

Provider defaults now have one shared source. When a provider changes, omitted model/voice/aggregation fields are filled with that provider's defaults in both saved-state loading and API updates. Explicit values remain authoritative; explicitly invalid combinations still fail validation. Saving unchanged providers does not reset a custom voice. Switching back to cloud STT no longer carries a Whisper model label forward.

The existing migration/rollback regression now passes without weakening its assertions. New tests cover explicit overrides, unchanged-provider preservation, cloud model selection and invalid explicit aggregation.

## Reinstated CI and platform-correct contract testing

The missing `.github/workflows/ci.yml` now delegates all ten declared stages to `ci/run-stage.sh`. Official action tags were resolved to immutable commit SHAs. Workflow permissions are read-only for repository contents. Device readiness remains an explicit manual-dispatch option, off by default, on a deliberately labelled self-hosted runner and the named `device-qualification` environment. No hosted workflow was executed or published from this non-Git checkout; remote runner/environment provisioning has not been claimed.

Actionlint passes with an explicit `.github/actionlint.yaml` path. Explicit discovery is needed here because this exported checkout has no `.git` root. The declared Android SDK/build-tools versions match the build scripts' API-34 defaults.

Restoring the corpus exposed two tests that selected a Linux-only contract profile on this Mac. A separate `macos-arm64-contract-ci` profile now exercises the same deterministic contract on the actual matching host. The original Linux and GPU profiles retain their platform requirements. A regression explicitly verifies that a mismatched host is still rejected.

The Mac contract check passes, including its sixty-turn deterministic drift check. **That result verifies the harness contract, not real model latency, speech recognition quality or a physical sixty-turn conversation.**

Twelve previously unclassified reference/adapter/report paths were explicitly added to the migration inventory. Its capture ID and the characterization matrix's source reference were updated together. Detector patterns, control paths and hidden-surface detection were not weakened. The inventory and characterization tests pass.

## Android safeguard staged for device qualification

`DigitalAudioBridge.java` documented a single-attempt Telephony-TX safeguard, but its constant had been changed to three attempts. Repeated failed creation can leave native mixer tracks behind on the documented device/audio-policy path. The constant is restored to one attempt.

The signed APK built successfully. Java/Python golden vectors and remote-link loopback checks passed. **The APK has not been installed.** Its hash and build-only status are recorded in `android-single-attempt-build.json`. The installer also restarts Android audio and manages system roles; deployment and physical validation still need to be coordinated with the operator.

No claim is made that this retry discrepancy caused the supplied WhatsApp latency: that call's measured action waits have a separate established explanation.

## Current verification and remaining gate

- Full isolated software suite: **999 passed, one failed, 39 skipped, one device test deselected**.
- The remaining failure is frozen-WhatsApp byte integrity: `whatsapp_client.py` and `whatsapp_phone_client.py` already differed from their qualified hashes before this work.
- The [PhoneAgent skill](/Users/aziz/Desktop/phone-agent-linux/skills/phoneagent-master/SKILL.md) explicitly says: “Never update `release/frozen-whatsapp.sha256` during ordinary work. A manifest change requires a separate explicit requalification project.” The remaining test and manifest are unchanged.
- Changed-code Ruff, configured Pyright, compilation, dependency-lock check, package build, actionlint and the targeted Android protocol tests passed. The previously documented broader reference/report/stock-ROM lint findings are not being represented as a clean repository-wide gate.
- Python service activation/read-back: `runtime-qualification-refresh-result.json` and `runtime-qualification-health.json`. Gemini STT/LLM and Edge TTS remain selected.

Remaining work includes recorded/physical call evaluation, staged Android deployment, frozen-media requalification or restoration, broader noise/echo and continuous-conversation testing, approximate repetition false positives, model-failure recovery, and other local-provider parity. These results substantially repair the verification foundation; they do not establish universal human-quality conversation.
