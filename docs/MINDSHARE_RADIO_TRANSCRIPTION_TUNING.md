# Mindshare radio: local transcription tuning (2026-11-07)

Status: measured on `.227`, 2026-11-07. Ted Sparks asked for the on-premises
tweaks first (the alternative, Amazon Transcribe with a custom vocabulary or
custom language model, stays open; see "What this does not fix"). Numbers
only: no transcript text, no audio left this host.

## What is being transcribed

Inventory of the local audio on `.227` (read-only, 2026-11-07 11:58 EST):
28,038 WAV clips over 22 days, 30.9 hours in all, about 1.4 hours of audio a
day across 16 active channels. Clip length: median 2.5 s, 75th percentile
4.2 s, 90th percentile 9.7 s; 80 % of clips are under 5 s. The audio is G.711
mu-law at 8 kHz from the radio network. Two thirds of the traffic is therefore
very short acknowledgements and unit identifiers on narrowband audio, which is
the hardest case for a general speech model.

Ground truth is thin: 14 human-corrected segments exist on the pilot (51 words)
and only 2 of them still had audio on `.227` (local audio is kept 15 days), so
word error rate could not be measured this round. The comparisons below use
two objective proxies on 300 randomly chosen unlabelled clips (0.6 s or
longer, the same 300 for every variant in a run):

- **noise**: the share of transcripts that are stock phrases the model
  invents from channel noise ("Thanks for watching!", "Bye.", credit lines;
  the list mirrors `app/services/radio_text.py`);
- **empty**: the share of clips with no text at all.

A good variant turns noise into empty without emptying real speech. The
clips a variant emptied were read against today's output to count how many
had several real-looking words.

## Variants and results

Production today ("base"): faster-whisper `large-v3`, CUDA, `int8_float16`,
beam 5, no voice-activity filter, hints off, the WAV path handed to the model.
Run-to-run variance is real: the same base settings on the same 300 clips gave
a noise share of 16.6 %, 16.9 % and 12.9 % in three runs (int8 GPU decoding is
not deterministic), so compare variants within one run.

Run 1 (base, VAD on, temperature 0, VAD + padding/gain/filter, stricter
thresholds, a neutral prompt, the distilled model):

| variant | noise | empty | s/clip |
| --- | ---: | ---: | ---: |
| base | 16.6 % | 0.7 % | 0.23 |
| VAD (Silero, defaults) | 2.0 % | 19.9 % | 0.17 |
| temperature 0 only | 16.6 % | 0.7 % | 0.20 |
| VAD + temperature 0 | 2.0 % | 19.9 % | 0.16 |
| VAD + 0.5 s padding + gain | 2.0 % | 15.6 % | 0.17 |
| the same, stricter no-speech / log-prob thresholds | 1.7 % | 17.9 % | 0.18 |
| the same, neutral domain prompt | 3.3 % | 14.9 % | 0.18 |
| distil-large-v3 with VAD + padding + gain | 2.0 % | 14.9 % | 0.10 |

Findings: temperature fallback is not where the invented phrases come from
(no change). The VAD removes almost all of them. Plain VAD emptied 4 clips of
6+ words up to 11 s long (real speech lost); padding each clip with 0.5 s of
silence and normalising its gain brought that down. The neutral prompt added
noise, as the hint-word A/B of 2026-10-17 had. distil-large-v3 is twice as
fast but made clear word errors on long clips where large-v3 did not; it is
rejected. A band-pass filter in run 1 was mis-scaled (unit DC gain was not
applied per sinc) and is superseded by run 3.

Run 2 (padding alone versus padding + gain, VAD at its default threshold 0.5):

| variant | noise | empty | emptied clips with 6+ words under base |
| --- | ---: | ---: | ---: |
| base | 16.9 % | 0.7 % | |
| VAD + 0.5 s padding | 2.0 % | 19.5 % | 3 (longest 5.9 s) |
| VAD + 0.5 s padding + gain | 2.0 % | 20.2 % | 4 (longest 14.0 s) |

Run 3 (a permissive VAD: speech probability 0.3 instead of 0.5, 100 ms
minimum speech instead of 250 ms; and the corrected 300 to 3400 Hz band-pass):

| variant | noise | empty | emptied clips with 6+ words under base | long clips (8 s+) whose words changed |
| --- | ---: | ---: | ---: | ---: |
| base | 12.9 % | 1.3 % | | |
| VAD 0.3 + padding | 1.3 % | 13.3 % | 3 | 26 |
| **VAD 0.3 + padding + gain** | **1.7 %** | **11.6 %** | **1** | 25 |
| VAD 0.3 + padding + gain + band-pass | 1.7 % | 12.3 % | 2 | 30 |
| VAD 0.5 + padding + gain + band-pass | 1.0 % | 12.9 % | 3 | 38 |

The band-pass changes many more words on long clips than the other steps
and drops more real clips; it is kept as an option but left off. The chosen
setting is the bold row.

## Decision (shipped in `radio_listener/config.json`)

```
"vad_filter": true, "vad_threshold": 0.3, "vad_min_speech_ms": 100,
"vad_min_silence_ms": 300, "vad_speech_pad_ms": 200,
"pad_seconds": 0.5, "normalize_gain": true
```
(`bandpass` absent = off; `temperature` unchanged.) Expected effect on live
traffic: invented stock phrases fall from roughly one clip in six or seven to
about one in sixty; one or two clips in three hundred that carried real words
comes out empty instead (it still has its audio and its radio ID, and the
page shows "(no speech recognised)"). Word accuracy on the clips that do get
text is unchanged: on long clips the two settings agree almost everywhere, and
on short clips they disagree about as often as they agree, with no way to say
which is right without a listener.

## What this does not fix

The complaint is accuracy on unit numbers, ten-codes and names in short
transmissions. None of the decoder settings move that. The routes that can:

1. **Ground truth first.** Corrections on the radio page are the only human
   references. A few hundred corrected short clips (acknowledgements with unit
   numbers especially) make word error rate measurable and become training
   text. Corrected segments keep their audio for a year, so they survive the
   15-day local purge on the pilot even though `.227` deletes its copy.
   **Convention for noise:** the correction form will not save an empty
   text (the API requires at least one character), so Ted marks a key-up,
   beep or pure-noise clip by saving a single period `.` (Ted, chat,
   2026-11-07). Any scoring must read a corrected text that holds no
   letters or digits as "no speech": the right machine output for that
   clip is empty, and any words are an invented phrase. A real "no speech"
   correction in the UI is an open follow-up.
2. **A domain model.** Either a Whisper fine-tune on the corrections (the RTX
   3090 can LoRA-tune large-v3) or Amazon Transcribe streaming with a custom
   vocabulary (unit names, stations, streets, ten-codes) and, with 10,000+
   words of corrected text, a custom language model on the narrowband base
   model. Transcribe streaming is already granted to the web task role;
   sending radio audio to it needs Ted's approval and an AI services opt-out
   policy on the account first (Transcribe keeps voice input to improve the
   service by default).
3. **Use the signalling.** The radio ID from the PT103 signalling already
   names the transmitting unit on TX clips; the page shows it next to the text.

## Method

`eval_variants.py` (kept with the session's scratch files, not in the
repository) loads each variant's model once, runs the same 300 clips through
every variant with the conditioning applied in numpy, scores WER / CER against
any references, and counts noise and empty outputs. `analyze.py` cross-tabs
what a variant emptied against what today's settings produced for the same
clip. Everything ran under `~/asr-eval` on `.227` as the administrator, read-only
on the data root, while the three radio services kept running; the GPU held
both the live worker and the evaluation (4.9 GiB used of 24 GiB).
