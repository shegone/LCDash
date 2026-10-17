# Mindshare radio uploader

`uploader.py` sends each finished, transcribed radio segment from this host
(`.227`) to LCDash on AWS. The contract is `docs/MINDSHARE_RADIO_INGEST.md`.
It is listen-side only: it never transmits on the radio network and never
touches CAD. Standard library only; signing uses the system `openssl` (3.x).

## What it does

- Scans `<data root>/transcripts/*.json` (data root = `output_directory` in
  `config.json`), oldest first.
- Skips segments that already have `<data root>/uploaded/<segment_id>.done` or
  `.rejected`, and ids starting with a skip prefix (default `pipeline_check_`).
- Sends only the contract's whitelisted metadata fields plus the WAV, signed
  with the Ed25519 private key. `audio_file`, `source_packet_counts`,
  `multicast_group`, `port` and `language_probability` are never sent.
- `201` or `200` writes `<segment_id>.done`. `400` or `413` writes
  `<segment_id>.rejected` (status and a short detail) and is not retried.
  `401`, `5xx` and network errors are retried with backoff (5 s doubling to
  5 min) and the segment is never dropped. The queue is processed in order, so
  one retryable failure pauses the pass.
- Segments that can never be valid (bad id, missing or out-of-tree WAV, WAV over
  `max_bytes`, malformed transcript) get a `.rejected` marker with status
  `local`.
- Once an hour, when `local_retention_days` is set, deletes local audio older
  than 15 days and transcript JSONs plus `.done` markers older than 30 days,
  only for segments that have a `.done` marker.
- Transcript text and audio are never written to the log.
- Runs a second, independent loop that sends a signed health heartbeat every
  60 s and keeps the transcriber's glossary current (see "Heartbeat" below).

## Install (on .227)

```bash
sudo install -d -m 0700 -o administrator -g administrator /opt/mindshare-radio-capture/keys
sudo install -m 0644 uploader.py upload_config.json /opt/mindshare-radio-capture/
sudo install -m 0644 mindshare-radio-upload.service /etc/systemd/system/
```

Key: `/opt/mindshare-radio-capture/keys/ingest_ed25519.pem`, mode 0600, owner
`administrator`. Generate it on this host with `openssl genpkey -algorithm
ED25519` and give only the base64 raw 32-byte public key to the app as
`LCDASH_RADIO_INGEST_PUBLIC_KEY`. The private key must never leave this host.

Check once by hand, then start the service:

```bash
cd /opt/mindshare-radio-capture
.venv/bin/python uploader.py --config config.json --upload-config upload_config.json --dry-run
.venv/bin/python uploader.py --config config.json --upload-config upload_config.json --once
sudo systemctl daemon-reload
sudo systemctl enable --now mindshare-radio-upload.service
```

Flags: `--config`, `--upload-config`, `--once` (process the queue once, run
retention if enabled, then exit; exit status 1 if a segment is still waiting;
no heartbeat is sent), `--heartbeat-once` (send one heartbeat, log its status
and exit; 0 on a 200 reply, 1 otherwise), `--dry-run` (print what retention
would delete and exit; uploads nothing).

Upload config keys (all optional except `endpoint` and `private_key`):
`heartbeat_endpoint` (default: the `endpoint` host with the path
`/api/radio/heartbeat`; https only, http only for 127.0.0.1) and
`heartbeat_seconds` (default 60).

## Heartbeat

Contract: `docs/MINDSHARE_RADIO_HEALTH.md`. Every 60 s a separate thread builds a
JSON body (counts and timestamps only: no transcript text, audio or source IPs),
signs `lcdash-radio-heartbeat-v1
<unix seconds>
<sha256 hex of the body>` with
the same Ed25519 key and the same `openssl` signer, and POSTs it to
`/api/radio/heartbeat` with `X-Radio-Timestamp` and `X-Radio-Signature`. The
same rules as segment uploads apply: redirects are never followed and https is
required (http only for 127.0.0.1).

The body is built from:

- `<data root>/status/capture_stats.json`, written by `capture.py` every 30 s.
  If its `stats_at` is more than 120 s old (or it is missing or unreadable) the
  heartbeat sends `capture.listeners` as `[]` and leaves `stats_at` as written
  (`null` when there is no readable file), so the server sees the staleness.
  Only the whitelisted fields are copied; anything else in the file is dropped.
- the queue directories: `transcriber.pending` is the number of jobs in
  `queue/pending` plus `queue/processing`, `transcriber.failed` is `queue/failed`;
- the uploader's own counters: `queued` and `oldest_queued_age_seconds` (the
  transcripts still waiting for a `.done` or `.rejected` marker, oldest file
  time), `rejected_total` (segments rejected, including local rejects) and
  `last_success_at` (last 200/201 segment reply). `rejected_total` and
  `last_success_at` start again from 0/null when the uploader restarts;
- `glossary_version`, read from `status/glossary.json`.

The heartbeat has its own timing and backoff and shares no failure path with
segment delivery: a heartbeat failure never pauses or delays segment uploads,
and a segment failure or a slow upload never delays a heartbeat. After a failed
heartbeat the next try is in 5 s, doubling, but never later than the normal 60 s
cadence. The journal gets one `heartbeat status=<code>` line when the status
changes (`network-error`, `sign-error` and `error-<Type>` are the non-HTTP
cases), so a steady state is silent.

Transcriber hints: a 200 reply carries `glossary`. When its `version` differs
from the one on disk and it includes `hotwords` and `prompt`, the uploader
writes `<data root>/status/glossary.json` (`{"version", "hotwords", "prompt"}`,
atomic, 0600) and logs `heartbeat glossary updated version=<hash>`. The glossary
text itself is never logged. See `transcribe.py` below for how it is used.

## Status files and glossary mode (capture and transcribe)

All under `<data root>/status/` (directory 0700, files 0600, written atomically):

| File | Written by | Read by |
| --- | --- | --- |
| `capture_stats.json` | `capture.py`, every 30 s | uploader heartbeat |
| `glossary.json` | uploader, when the server's version changes | `transcribe.py` |

`capture.py` keeps per-listener counters (packets, PCMU packets, other payload
types, bytes, distinct sources, closed segments, last packet time) in the
receive loop with no I/O; the interval counters reset each time the file is
written. It also prints one `stats channel=... direction=...` summary line per
listener every 5 minutes (counts only). The audio path is unchanged.

`transcribe.py` re-reads `status/glossary.json` before each job when its file
time changes. A missing or corrupt file means no hints and is never an error.
`config.json` `transcription.glossary_mode` chooses how the hints are used:

- `"hotwords"` (default): passed as `hotwords=` to `model.transcribe`;
- `"prompt"`: passed as `initial_prompt=`;
- `"off"`: no hints are sent to the model.

Each transcript JSON records `glossary_version` (`null` when no hints were
applied). If a clip is shorter than 1.5 s and every word of the result is a
glossary word, the transcript also gets `"hint_suspect": true` (the text is
kept; the uploader ignores both fields). These are the only changes to the
transcript JSON; the uploader's field whitelist still drops them.

Updating the `.227` install after this change: copy `capture.py`,
`transcribe.py`, `config.json` (its `transcription` block gains `glossary_mode`)
and `uploader.py` to `/opt/mindshare-radio-capture/`, then restart the three
services. The `.service` files are unchanged: every new file lives under the
existing `ReadWritePaths` directory.

## Reading status

```bash
systemctl status mindshare-radio-upload.service
journalctl -u mindshare-radio-upload.service -f
ls /var/lib/mindshare-radio-capture/data/uploaded/
```

Heartbeat check: `.venv/bin/python uploader.py --config config.json --upload-config upload_config.json --heartbeat-once`.

Each upload attempt logs one line: `segment_id=... status=... bytes=...`.
`status=network-error` or `sign-error` means a retry is scheduled.
Waiting segments are the transcripts in `transcripts/` without a `.done` or
`.rejected` marker.

## Replaying a rejected segment

Read the reason, fix the cause, then delete the marker so the next pass retries:

```bash
cat /var/lib/mindshare-radio-capture/data/uploaded/<segment_id>.rejected
rm  /var/lib/mindshare-radio-capture/data/uploaded/<segment_id>.rejected
```

Uploading the same segment twice is safe: the app answers `200 duplicate`.
