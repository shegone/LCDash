# Mindshare Radio Intelligence: ingest contract (v1)

Status: design, 2026-10-16. Owner: Ted Sparks. Decisions in chat the same day:
live radio traffic is approved (the three channels carry nothing privileged
and are not on the public scanner), admin-only during testing, audio kept
15 days, transcripts kept 30 days.

Listener side: the `radio_listener/` package (deployed on `.227`, listen-only,
never transmits). AWS side: this app. Transcripts are machine generated and
unverified; the UI must say so. This feature is inquiry-only: it never
writes to CAD, radio, paging, or any operational output.

## Flow

1. `.227` captures a completed segment and transcribes it locally (existing).
2. `uploader.py` on `.227` signs and POSTs `audio` + `metadata` to
   `POST https://aws.logan911.com/api/radio/ingest`.
3. The app verifies the signature, stores the WAV in the content bucket under
   `radio/audio/`, inserts the transcript row in Postgres, returns 201.
4. Admins use `/mindshare/radio` to browse, read, and play segments. Every
   list and every playback is recorded in `lcdash_radio.audit_events`.

## Authentication (no secrets in AWS)

Ed25519 signatures. The private key lives only on `.227`
(`/opt/mindshare-radio-capture/keys/ingest_ed25519.pem`, mode 0600). The app
holds only the base64 raw 32-byte public key in env
`LCDASH_RADIO_INGEST_PUBLIC_KEY`. Nothing secret is stored in AWS.

The ALB must let this one path skip Cognito: a listener rule at priority 6,
conditions path `/api/radio/ingest` AND method `POST`, action forward. The
app is the authenticator for that path. If the public key env var is empty
the route answers 503 and accepts nothing.

### Request

`multipart/form-data` with exactly two parts:

- `metadata`: `application/json`, the exact bytes that were signed, <= 64 KiB.
- `audio`: `audio/wav`, mono PCM16, 8000 Hz, <= 4 MiB, must start with `RIFF`
  and contain `WAVE`.

Headers:

- `X-Radio-Timestamp`: integer Unix seconds. Rejected (401) if more than 300
  seconds from server time.
- `X-Radio-Signature`: base64 of the Ed25519 signature of the message below.

Signed message (bytes, joined with a single `\n`, no trailing newline):

```
lcdash-radio-v1
<X-Radio-Timestamp as decimal text>
<segment_id from the metadata>
<sha256 hex (lowercase) of the audio bytes>
<sha256 hex (lowercase) of the metadata bytes>
```

Golden vector (test both sides against it):

- private seed hex: `000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f`
- public key (base64): `A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=`
- timestamp `1760000000`, segment_id `law_rx_test`
- metadata bytes `{"segment_id":"law_rx_test"}` (sha256 `b67cc69651e0b68ce1d17c5832020ff280730b8b4ed2ca1e29ef962fba2be714`)
- audio bytes `RIFFtest` (sha256 `79ab531fc8e36fb4aa47a2adf03f3535e971e8dc5a8d84f7db4537cdc5535255`)
- signature (base64): `950QFKVROQjFxt0qIs+S00kgBfYEa6etdYGkd9DGQIaPuUHpmYFoZcniKxlA3mSfXaeJngm9X1zst47K2ssrBw==`

### Metadata JSON (schema_version 1)

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | int | must be 1 |
| `segment_id` | str | `^[A-Za-z0-9_.-]{8,128}$`, primary key, idempotency key |
| `channel` | str | `^[a-z0-9_]{1,32}$` (law, ems, fire, ...) |
| `direction` | str | `tx` or `rx` |
| `started_at`, `ended_at` | str | ISO 8601 with offset, `ended_at >= started_at`, span <= 600 s |
| `codec` | str | `PCMU` |
| `payload_type` | int | 0 |
| `sample_rate_hz` | int | 8000 |
| `pcmu_packet_count` | int | >= 0 |
| `transcript` | str | <= 20000 chars, may be empty |
| `language` | str | <= 8 chars |
| `model` | str | <= 64 chars |
| `transcribed_at` | str | ISO 8601 with offset |
| `segments` | list | <= 500 items of `{start, end, text, avg_logprob, no_speech_prob}` |

Deliberately NOT sent: `audio_file` (a path on `.227`), `source_packet_counts`
and source IPs (operational), `multicast_group`, `port`, and
`language_probability` (always 1 because language is forced to English).
Unknown extra fields are rejected with 400 so the schema cannot drift
silently.

### Responses

- `201 {"status":"created","segment_id":...}` stored.
- `200 {"status":"duplicate","segment_id":...}` already stored, nothing changed.
- `400` invalid body/metadata/WAV, `401` bad or stale signature, `413` too
  large, `503` ingest not configured. Bodies contain a short `detail` only.
  Never echo transcript text or audio in an error.

Order of operations: verify signature first (before parsing metadata beyond
`segment_id`), then validate, then put the S3 object, then insert the row
(`ON CONFLICT (segment_id) DO NOTHING`). A crash between the two leaves an
orphan object that the 15-day lifecycle removes.

## Storage and retention

- Audio: content bucket `lcdash-p1-logan-use1-862772137583-content`, key
  `radio/audio/<channel>/<yyyy>/<mm>/<dd>/<segment_id>.wav`, SSE-S3, lifecycle
  rule expiring prefix `radio/audio/` after 15 days.
- Transcript rows: Postgres schema `lcdash_radio`, table `segments`, purged
  when `received_at` is older than 30 days (done lazily, at most hourly, from
  the ingest path; no new scheduled task).
- When the audio object is gone (expired or missing) the UI shows "audio
  expired" and still shows the transcript.

## Admin interface (admin only; every route 403s non-admins uniformly)

- `GET /mindshare/radio`: the page (replaces the roadmap placeholder). A
  persistent banner: "Machine-generated transcripts. Unverified. Do not act
  on them operationally."
- `GET /api/radio/segments?channel=&direction=&q=&before=&limit=`: newest
  first, keyset pagination on `(started_at, segment_id)`, `limit` <= 200.
  Each item carries a `low_confidence` flag (true if any ASR segment has
  `avg_logprob < -1.0` or `no_speech_prob > 0.6`) and `audio_available`.
- `GET /api/radio/segments/{segment_id}/audio`: proxies the WAV from S3 with
  `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`.
  No presigned URLs. Records `radio.segment.listen`.
- `GET /api/radio/stats`: counts per channel for the last 24 hours and the
  newest `received_at` (so an operator can see a silent listener).

Audit: `lcdash_radio.audit_events(event_id, created_at, event_type, actor,
segment_id, detail)`, allowed `event_type` values `radio.segments.list` and
`radio.segment.listen`. Also written to the log as one `logger.info` line.

## Not in v1

Live streaming, non-admin roles, search across audio, anything operational.
Fire has produced no traffic yet; payload types other than PCMU are counted
on `.227` but not decoded.

## Addendum 2026-10-18: possible-noise flag and payload type 103

- Segment items carry `possible_noise` (display-only; computed from the shown text after fixes and corrections): boilerplate the transcriber invents from background noise ("Thanks for watching", "Bye", "Beep", "Subtitles by..."), or a clip under 1.5 s with 3 or fewer low-confidence words. A corrected segment is never noise. Possible-noise segments do not raise keyword alerts. The page can hide them (client-side checkbox).
- RTP payload type 103 on the radio streams is signaling, not voice (passive 180 s sample, 2026-10-17: voice is 160-byte PCMU only; type 103 packets are 8-27 bytes, a 3-packet keepalive burst every 60 s on idle streams and short bursts around each transmission). The Listener health warning ignores type 103 and still flags any other undecoded type. The 103 payloads probably carry PTT/talker information; decoding them needs the Mindshare protocol documentation.

