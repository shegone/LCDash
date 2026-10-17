# Mindshare radio: listener health, silent-listener alarm, transcriber hints (v3)

Status: design, 2026-10-17. Extends `MINDSHARE_RADIO_INGEST.md` (v1) and
`MINDSHARE_RADIO_CORRECTIONS.md` (v2). Ted Sparks asked for all three items in
chat: (1) pass a vocabulary glossary to Whisper on `.227`, (2) periodic capture
stats so a quiet channel can be told from a dropped one, (3) an alarm when the
listener stops reporting.

One new machine endpoint carries both (2) and (1): the listener sends a signed
heartbeat with its stats every 60 s, and the reply carries the current
glossary. No new ALB rule, no new secret, same Ed25519 key.

## Heartbeat: `POST /api/radio/heartbeat`

Headers: `X-Radio-Timestamp` (Unix seconds, +/- 300 s), `X-Radio-Signature`
(base64 Ed25519), `Content-Type: application/json`. Body: UTF-8 JSON object,
at most 16 KiB. Signed message (joined by single `\n`, no trailing newline):

```
lcdash-radio-heartbeat-v1
<X-Radio-Timestamp as decimal text>
<sha256 hex (lowercase) of the raw body bytes>
```

Same verification discipline as ingest: 503 when the public key is not
configured, 413 over the cap (check Content-Length and stream), 401 for a bad
or stale signature (verify BEFORE parsing the JSON), 400 for invalid JSON or
schema. Identity headers are ignored. Never echo request content in errors.

Body (`schema_version` 1; unknown keys rejected with 400; all numbers finite
non-negative ints unless noted; strings length-capped):

```
{
  "schema_version": 1,
  "sent_at": ISO-8601 with offset,
  "capture": {
    "stats_at": ISO|null,            // when capture last wrote its stats
    "started_at": ISO|null,
    "uptime_seconds": int,
    "interval_seconds": int,         // length of the "interval" counters below
    "listeners": [                   // 0..64 items
      {"channel": "law", "direction": "rx",
       "packets_total": int, "packets_interval": int,
       "pcmu_packets_total": int,
       "other_payload_types": {"103": int},   // payload type -> packets, <= 16 keys
       "bytes_interval": int, "sources_seen": int,
       "segments_closed_total": int, "last_packet_at": ISO|null}
    ]
  },
  "transcriber": {"pending": int, "failed": int},
  "uploader": {"queued": int, "oldest_queued_age_seconds": int|null,
               "rejected_total": int, "last_success_at": ISO|null},
  "glossary_version": string (<= 32) | null
}
```

Response `200 {"status":"ok","server_time": ISO, "glossary": {...}}` where
`glossary` is `{"version": V}` alone when the request's `glossary_version`
already equals the current version, otherwise
`{"version": V, "hotwords": "...", "prompt": "..."}`.

Storage: one-row table `lcdash_radio.listener_status(id SMALLINT PRIMARY KEY
CHECK (id = 1), payload JSONB NOT NULL, received_at TIMESTAMPTZ NOT NULL)`,
upserted on every heartbeat (idempotent DDL, same advisory-locked schema file).
Heartbeats are not audited (60 s noise). On success the app writes exactly one
log line with the fixed prefix `radio-heartbeat ok` (then
`listeners=<n> packets_interval=<n> undecoded=<n>`), which the alarm counts.
No transcript text and no source IPs ever appear in this payload or the line.

## Listener health on the page

`GET /api/radio/stats` (human roles) gains a `listener` object: `reporting`
(true when the last heartbeat is at most 180 s old), `last_heartbeat_at`,
`age_seconds`, `capture_uptime_seconds`, the per-listener rows exactly as sent
(channel, direction, packets_interval, interval_seconds, other_payload_types,
last_packet_at), and the `transcriber` / `uploader` objects. `null` when no
heartbeat has ever arrived. The page's health panel shows a Listener tile
(green "Reporting, <age>" / red "Not reporting since <time>"), a per-channel
line "law: 3,412 packets in the last minute" or "fire: no traffic (quiet)", a
visible warning when any `other_payload_types` count is non-zero ("undecoded
audio: payload type 103, N packets, these are not being transcribed"), and
queue depths. Honest wording: a quiet channel is normal.

## Silent-listener alarm

Foundation stack: a metric filter on the web log group matching
`"radio-heartbeat ok"` -> custom metric `LCDash/Radio` `HeartbeatCount` (value
1), and an alarm `lcdash-p1-logan-use1-radio-listener-silent`: Sum < 1 over a
5-minute period for 2 consecutive periods, missing data counts as BREACHING,
action = the existing alerts SNS topic (the one the identity alarm uses). The
alarm name/description use hyphens, no em dashes. Document the two added
resources (MetricFilter, Alarm) as an addition in the allowlist, in the style
of the existing `sns_alerts_exception`, `approved_by` "Ted Sparks", chat
2026-10-17.

## Transcriber hints (glossary)

Table `lcdash_radio.hints(hint_id BIGSERIAL PK, phrase TEXT, created_by TEXT,
created_at TIMESTAMPTZ DEFAULT now())`, unique on lower(phrase). Admin API
(admin only, audited as `radio.hint.add` / `radio.hint.remove`, event types
added to the audit CHECK): `GET /api/radio/hints`, `POST /api/radio/hints`
`{phrase}` (2-60 chars, plain text, no control chars, no newlines, max 200
hints, duplicates 409), `DELETE /api/radio/hints/{id}`.

Glossary build (server side): hints first, then enabled watch phrases, then the
`replace_text` of the fixes; dedupe case-insensitively, keep order; join with
", " and stop before 600 characters total. `hotwords` = that string.
`prompt` = `Public safety radio traffic. Names and terms that may be spoken: `
+ hotwords + `.` `version` = first 12 hex chars of sha256(hotwords). Empty
glossary -> version `empty`, empty strings.

UI: an admin-only "Hint words" tab (list, add, delete with two-step confirm)
with the note "Hint words help the transcriber hear local names. They reach
the listener within about a minute and apply to new transcriptions only."

## Listener side (`radio_listener/`, Python stdlib only)

- `capture.py`: keep audio behavior byte-for-byte; add per-listener counters
  (packets total/interval, PCMU packets, other payload type counts, bytes,
  distinct sources, segments closed, last packet time). Every 30 s write
  `<output_directory>/status/capture_stats.json` (0600, atomic) with the
  `capture` object above plus a monotonic-safe `stats_at`; every 5 minutes log
  one summary line per listener to stdout (no content). Counters must never
  block or slow the receive loop.
- `transcribe.py`: before each job, re-read `<root>/status/glossary.json`
  when its mtime changed. `config.json` `transcription.glossary_mode` is
  `"hotwords"` (default; passes `hotwords=` to `model.transcribe`),
  `"prompt"` (passes `initial_prompt=`), or `"off"`. Record `glossary_version`
  in the transcript JSON. A missing/corrupt glossary file means no hints (never
  a failure). Guard against the model parroting the hints on very short
  audio: if the clip is under 1.5 s and every word of the result is in the
  glossary, keep the text but set `hint_suspect: true` in the JSON (the
  uploader ignores it; do not delete text).
- `uploader.py`: every 60 s (independent of segment uploads and its own
  backoff; a heartbeat failure never affects segment delivery and vice
  versa) build the heartbeat from `capture_stats.json` (if older than 120 s,
  send `capture.listeners` empty and `stats_at` as is so the server sees
  stale data), queue directories, and its own counters, sign and POST it,
  and write the returned glossary to `<root>/status/glossary.json` (0600,
  atomic) when the version differs. Never log glossary text.
- Keep every existing uploader behavior and test. Add tests (fake local
  server for the heartbeat; glossary file write; stale-stats handling).

## Safety

Read-only/advisory. No audio or transcript text is added to heartbeats. The
glossary can only influence how the local transcriber spells words; machine
transcripts remain unverified and labeled so.
