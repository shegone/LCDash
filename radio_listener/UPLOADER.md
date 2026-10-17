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
retention if enabled, then exit; exit status 1 if a segment is still waiting),
`--dry-run` (print what retention would delete and exit; uploads nothing).

## Reading status

```bash
systemctl status mindshare-radio-upload.service
journalctl -u mindshare-radio-upload.service -f
ls /var/lib/mindshare-radio-capture/data/uploaded/
```

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
