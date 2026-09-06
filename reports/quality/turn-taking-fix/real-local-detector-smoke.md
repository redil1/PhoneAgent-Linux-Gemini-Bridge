# Actual local detector integration smoke check

Date: 2026-09-04. Four offline scenarios exercised the real bundled Silero VAD and
`smart-turn-v3.2-cpu.onnx` through the current `AntigravityLiveSTTService`.

All four integration checks passed: speech acquired the floor, each case produced
one final transcript with the expected complete text, and neither unfinished
clause committed during the inserted 1.2-second pause before its continuation.

## Method

- Synthesized original test phrases with the installed macOS Samantha (English)
  and Thomas (French) voices. Audio was written only to a temporary directory and
  was never played. The directory was automatically removed afterward.
- Converted the files to 16 kHz mono signed 16-bit PCM using the installed ffmpeg.
  Trimmed synthesis padding while retaining 80 ms around the signal, then fed the
  service 20 ms frames paced in real time.
- Used the service's actual Silero and Smart Turn instances, default endpoint
  settings, `run_stt()` input path and turn watchdog. Only downstream frame and
  interruption publication were replaced with capturing sinks.
- Injected the known fixture transcript at each segment end. No ASR session,
  transcription provider, language model, phone or customer audio was used.
- Added four seconds of actual received silence after each final segment.
- The restricted sandbox initially made macOS `say` return a silent file. An
  automatically approved execution outside that sandbox permitted the installed
  local speech engine to generate real PCM. No model/provider was downloaded.

Reproduction script: `real_local_detector_smoke.py`. Detailed frame transitions,
model outputs, input hashes and timings: `real-local-detector-results.json`.

## Observed results

| Case | Final commits | Commit during 1.2 s pause | Delay after final fixture segment | Final complete probability |
| --- | ---: | ---: | ---: | ---: |
| English complete sentence | 1 | Not applicable | 620 ms | 0.987 |
| French complete sentence | 1 | Not applicable | 600 ms | 0.981 |
| English unfinished clause + continuation | 1 | 0 | 2,900 ms | 0.412 |
| French unfinished clause + continuation | 1 | 0 | 620 ms | 0.786 |

The first user-speaking frame occurred 200 ms after each fixture segment began.
That includes the retained 80 ms leading padding and is consistent with the
configured 120 ms Silero start window. This is a fixture observation, not a
telephone acoustic-onset measurement.

The unfinished English clause ended in “because”; its completion probability was
0.060. The unfinished French clause ended in “parce que”; its probability was
0.009. The application retained both prefixes and merged the continuations
without creating an extra caller turn.

Measured model inference calls took approximately 68–83 ms on this Mac. This
includes warm-up/idle checks as well as speech endpoint checks and is not a
statistically meaningful latency benchmark.

## Material limitation

The completed English continuation received a model score of 0.412, below the
0.5 completion threshold. The controller consequently used its conservative
three-second silence allowance; relative to the end of this trimmed fixture,
the observed delay was 2.9 seconds. This is a concrete example of the remaining
patience-versus-latency tradeoff. It must not be hidden behind a claim that all
completed utterances respond in 600 ms.

These checks establish that the actual local models integrate with the repaired
turn controller under synthetic speech. They do not measure French/English
speech-recognition accuracy, telephone noise robustness, accent coverage,
human-turn classification accuracy, physical playback cancellation, or perceived
human conversation quality. Transcripts were supplied directly and the voices are
synthetic. Further calibration requires representative consented telephone audio
and explicitly authorized physical call testing.
