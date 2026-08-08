# STT Canary Synthetic Evaluation - August 2, 2026

## Scope

This is a private, synthetic-only smoke evaluation of the NVIDIA Parakeet TDT
0.6B v3 canary against the current Faster-Whisper Large-v3-Turbo service. It
does not validate real microphone, caller, radio, CAD, or medical audio.

## Corpus

The private server-only corpus at
`/srv/lcdash-platform/stt-canary/2026-08-02` contains 16 generated WAV files,
a manifest, and a safety note. It uses approved synthetic MAE and JACK voice
profiles with fictional operational wording only. Audio artifacts and generated
transcripts are not stored in Git.

The samples exercise normal conversational questions, unit numbers, locations,
incident-like identifiers, times, dates, CAD vocabulary, differing cadences,
and the spoken phrase "nine one one."

## Preliminary result

| Engine | Completed files | Average request time | Synthetic word-error rate |
| --- | ---: | ---: | ---: |
| Faster-Whisper Large-v3-Turbo | 16 / 16 | 0.261 seconds | 16.88% |
| NVIDIA Parakeet TDT 0.6B v3 | 16 / 16 | 0.500 seconds | 21.43% |

The canary model loaded successfully and processed every test file. The first
single-file smoke test recognized the spoken phrase "nine one one" as "91," so
numeric normalization and public-safety terminology remain specific review
points. The current Whisper service remains the live MAE and JACK speech-to-text
default. The Parakeet canary was stopped after testing to release GPU capacity.

## Next evaluation step

This synthetic corpus is useful for repeatable integration testing, not a live
replacement decision. Before changing the default, compare both engines using
approved representative microphone audio and review word accuracy, silence
behavior, turnaround time, numerical terms, unit identifiers, and location
names. Do not use this offline complete-audio TDT model as a continuous radio
streaming engine.
