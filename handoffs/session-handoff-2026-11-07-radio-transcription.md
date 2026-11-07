# Session handoff, 2026-11-07: radio transcription tuning on `.227`

Follows `session-handoff-2026-11-06-discipline-clock.md` (release 128 live,
`aedadfe` pushed). Verified facts first; no transcript text, no credentials.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main` at `633d36f` (one commit past `aedadfe`): VAD and audio conditioning for the radio listener's transcription worker. **Not pushed.** | 2026-11-07 |
| LCDash pilot (AWS) | Release 128 unchanged (web revision 128, collector 88). Nothing in this session touched AWS beyond read-only page reads as Ted through Chrome. | 2026-11-07 |
| `.227` | Three radio services active, GPU RTX 3090. The new worker code is **not installed yet**; the running worker still uses the old settings. Evaluation files are under `/home/administrator/asr-eval` (owner-only; they hold transcript text and must not leave the host). | 2026-11-07 12:00 EST |

## What Ted asked

The radio page is good except the transcription. Options were laid out
(Amazon Transcribe with a custom vocabulary / custom language model versus
tuning the local Whisper worker); Ted chose the on-premises tweaks first.

## What was done

1. Inventory on `.227` (read-only): 28,038 clips over 22 days, 30.9 hours,
   median clip 2.5 s, 80 % under 5 s; `large-v3`, int8_float16, beam 5, no
   VAD, hints off; faster-whisper 1.2.1, ctranslate2 4.8.3, PyAV 18.1.0.
2. Ground truth: 14 human-corrected segments on the pilot (51 words), 2 with
   audio still on `.227`. Too thin for word error rate; the comparison used
   invented-boilerplate and empty-output rates on 300 random clips.
3. Three evaluation runs on the GPU beside the live worker (see
   `docs/MINDSHARE_RADIO_TRANSCRIPTION_TUNING.md` for every number). Chosen:
   Silero VAD at speech probability 0.3 with 100 ms minimum speech, 0.5 s of
   silence padding, gain normalised; band-pass tested and left off;
   temperature fallback unchanged; distil-large-v3 rejected.
4. Code: `radio_listener/transcribe.py` reads `vad_filter`, `vad_threshold`,
   `vad_min_speech_ms`, `vad_min_silence_ms`, `vad_speech_pad_ms`,
   `pad_seconds`, `normalize_gain`, `bandpass` from the `transcription`
   config (all default to the old behaviour when absent), conditions the
   audio in numpy and hands the model 16 kHz samples; segment times are
   shifted back by the padding; the hint-suspect rule still uses the clip's
   own length. `radio_listener/config.json` turns the chosen settings on.
   `radio_listener/README.md` documents them. Tests: 47 in
   `tests/test_radio_listener_transcribe.py` (11 new), full suite below.
5. A 20-clip blind listening set (times, channels, both texts) was produced
   for Ted at `/home/administrator/asr-eval/run1/review_base_vs_cond.txt`;
   it compares the first-run variant and is superseded by run 3, so it is
   informative only.

## Expected effect, and what it does not fix

Invented stock phrases ("Thanks for watching!") should fall from roughly one
clip in six or seven to about one in sixty. One or two clips in three hundred
that carried real words (short acknowledgements) will come out empty instead.
Word accuracy on the clips that get text does not change: the accuracy
complaint (unit numbers, ten-codes, names on 2 to 3 second clips) needs a
domain model and, before that, a few hundred human corrections as ground
truth. Both routes (Whisper fine-tune on the 3090, or Amazon Transcribe
streaming with custom vocabulary / custom language model, with an AI
services opt-out policy first) are written up in the tuning doc.

## Tests

- `python -m pytest tests/test_radio_listener_transcribe.py -q`: 47 passed.
- `python -m pytest tests/ -q`: 2100 passed, 6 skipped (2,623 subtests), 61 s.
- Infrastructure tests not run: no infrastructure change.

## Exact next action

1. Ted decides whether to install the new worker on `.227` (the radio
   services there are authorised for this project; this restarts
   `mindshare-radio-transcribe` only, capture and upload keep running and the
   queue holds clips during the restart). Install from the committed LF
   files, keeping a backup:

   ```
   ssh administrator@14.1.1.227 'mkdir -p /opt/mindshare-radio-capture/backup-pre-vad && cp /opt/mindshare-radio-capture/transcribe.py /opt/mindshare-radio-capture/config.json /opt/mindshare-radio-capture/backup-pre-vad/'
   git show HEAD:radio_listener/transcribe.py > <tmp>/transcribe.py ; git show HEAD:radio_listener/config.json > <tmp>/config.json
   scp <tmp>/transcribe.py <tmp>/config.json administrator@14.1.1.227:/opt/mindshare-radio-capture/
   ssh administrator@14.1.1.227 'sudo systemctl restart mindshare-radio-transcribe && sleep 20 && journalctl -u mindshare-radio-transcribe -n 5 --no-pager'
   ```
   The ready line must read `vad_filter=True pad_seconds=0.5 normalize_gain=True bandpass=False`.
   Rollback: copy the two files back from `backup-pre-vad/` and restart.
2. Push the commit when Ted says so (`git push origin main`); no AWS release
   is needed (listener-only change).
3. Grow the ground truth: correct transcripts on the radio page, short
   acknowledgements with unit numbers especially.
4. Open items unchanged from the 2026-11-06 handoff.

Services on `.227` were left running; nothing on `.227` was changed except the
new `~/asr-eval` folder.
