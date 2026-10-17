# Mindshare radio: corrections, fixes, keyword alerts, wider access (v2)

Status: design, 2026-10-17. Extends `docs/MINDSHARE_RADIO_INGEST.md` (v1).
Decisions (Ted Sparks, chat, 2026-10-17):

- Open the radio page to all users on the system.
- Any user can correct a transcript. Corrected segments keep their audio and
  corrected transcript for **1 year**. Uncorrected stay 15 days (audio) and 30
  days (transcript).
- Admins can add key words/phrases that raise an alert on the dashboard.
- (Earlier "defaults" Ted approved:) admins can add find-and-replace "fixes"
  that are always applied to machine transcripts.

Advisory only. Transcripts and alerts are machine generated or human typed and
unverified. Nothing here writes to CAD, radio, paging, or any station alert,
and nothing makes a sound or sends a notification outside the web UI. A
dashboard alert is a visible panel entry, nothing more.

## Access

- "Human roles": `user`, `supervisor`, `dispatcher`, `fire_officer`, `admin`.
  The `avatar` kiosk role and unidentified requests stay denied (403).
- Human roles: view the page and feed, play audio, correct/revert corrections,
  see and dismiss dashboard alerts.
- Admin only: manage watch terms and replacement fixes.
- The role tier middlewares in `app/core/*_tier.py` currently deny
  `/mindshare` and `/api/radio/` for restricted roles (dispatcher denylist,
  sanitized/fire-officer allowlists). Update them so human roles reach
  `/mindshare/radio`, `/api/radio/` (except ingest, which is machine-signed and
  identity-blind) and the dashboard alert poll. `avatar` stays deny-by-default.
  Keep every other restriction on those roles exactly as it is.
- Nav: show the Radio entry to all human roles (it is admin-only today).

## Data model (schema `lcdash_radio`, idempotent DDL in database/radio_schema.sql)

Add columns to `segments` (ALTER TABLE ... ADD COLUMN IF NOT EXISTS):
`corrected_text TEXT NULL`, `corrected_by TEXT NULL`, `corrected_at
TIMESTAMPTZ NULL`, `retain_until TIMESTAMPTZ NULL`.

New tables:

- `corrections(correction_id BIGSERIAL PK, segment_id TEXT REFERENCES
  segments ON DELETE CASCADE, previous_text TEXT, new_text TEXT NULL,
  actor TEXT, created_at TIMESTAMPTZ DEFAULT now())`. Append-only history.
  `new_text NULL` means a revert. This is the training data; never update it.
- `replacements(replacement_id BIGSERIAL PK, find_text TEXT, replace_text
  TEXT, whole_word BOOL DEFAULT true, created_by TEXT, created_at)`.
- `watch_terms(term_id BIGSERIAL PK, phrase TEXT, channels TEXT[] DEFAULT '{}'
  (empty = every channel), enabled BOOL DEFAULT true, created_by, created_at)`;
  unique on lower(phrase).
- `alerts(alert_id BIGSERIAL PK, segment_id TEXT REFERENCES segments ON DELETE
  CASCADE, term_id BIGINT REFERENCES watch_terms ON DELETE CASCADE, phrase
  TEXT, created_at TIMESTAMPTZ DEFAULT now(), dismissed_by TEXT NULL,
  dismissed_at TIMESTAMPTZ NULL, UNIQUE (segment_id, term_id))`.

Retention: the lazy purge becomes
`DELETE FROM segments WHERE received_at < now() - interval '30 days' AND
(retain_until IS NULL OR retain_until < now())`. Audit events are never purged.

## Display text

`display_text(segment) = corrected_text` if set, else the machine
`transcript` with the replacement fixes applied at read time (literal,
case-insensitive, whole-word when `whole_word`, longest `find_text` first,
rules cached in-process for 60 s, at most 200 rules, `find_text` 1-80 chars,
NO regular expressions, so no ReDoS). Fixes never alter the stored machine
transcript, and never apply on top of a human correction.

Every segment item returned by the list API gains: `text` (the display text),
`machine_transcript` (stored raw text), `corrected` (bool), `corrected_by`,
`corrected_at`, `retained_until` (ISO or null). Keep `transcript` as an alias
of `text` so older clients still work.

## Corrections

- `PUT /api/radio/segments/{segment_id}/correction`, body `{"text": "..."}`:
  1-20000 chars after strip, plain text (reject control characters other than
  newline/tab). Human roles. Sets corrected_text/by/at, `retain_until = now()
  + 365 days` (extended on each edit), appends a `corrections` row, records
  audit `radio.segment.correct`, re-evaluates watch terms (below). On the FIRST
  correction of a segment whose audio still exists, copy the WAV with S3
  CopyObject from `radio/audio/...` to
  `radio/corrected/<channel>/<yyyy>/<mm>/<dd>/<segment_id>.wav` and set the row's
  `audio_key` to the new key. If the audio is already gone, still save the text
  (`audio_available` stays false). Returns the updated item. 404 unknown id,
  400 bad text.
- `DELETE /api/radio/segments/{segment_id}/correction`: clears corrected_text
  (history row with new_text NULL, audit `radio.segment.correct_revert`).
  Retention already granted stays (once kept, kept until `retain_until`).
- Concurrency: last write wins; the history keeps every version.
- `audio_available` is true when `audio_key` is set and either the key is under
  the corrected prefix and `retain_until > now()`, or it is under the normal
  prefix and `received_at` is within 15 days.
- The audio proxy keeps working for both prefixes (same audit, same headers).

## Fixes (replacements), admin only

`GET /api/radio/replacements`, `POST /api/radio/replacements`
`{find, replace, whole_word?}`, `DELETE /api/radio/replacements/{id}`. Max 200
rules; reject empty `find`, `find == replace`, and duplicates (case-insensitive).
Audit `radio.fix.add` / `radio.fix.remove`.

## Keyword alerts, admin manages, everyone sees

- Admin: `GET /api/radio/watch-terms`, `POST /api/radio/watch-terms`
  `{phrase, channels?: [..], enabled?: true}` (phrase 2-80 chars, max 100
  terms), `PATCH /api/radio/watch-terms/{id}` `{enabled?, channels?}`,
  `DELETE /api/radio/watch-terms/{id}`. Audit `radio.watch.add|change|remove`.
- Matching: case-insensitive, whole word/phrase (word boundaries, literal text,
  `re.escape`), on the segment's display text. Evaluate at ingest for each new
  segment (machine text with fixes) and again whenever a correction is saved.
  One alert per (segment, term). Term channel filter applies when non-empty.
  Matching failures must NEVER fail an ingest (log and continue).
- Human roles: `GET /api/radio/alerts` returns undismissed alerts created in the
  last 24 hours, newest first, max 50: `{alert_id, segment_id, channel,
  direction, started_at, phrase, snippet (display text, max 300 chars),
  created_at, audio_available, text_corrected}`. Not audited per poll (the
  dashboard polls every 15 s); transcript access is still audited where audio
  is played and where the full list is fetched.
- `POST /api/radio/alerts/{alert_id}/dismiss` (human roles): dismissal is
  shared for everyone, records who/when, audit `radio.alert.dismiss`. It is a
  UI state only, not an operational acknowledgement of anything.
- Dashboard: a "Radio keyword alerts" panel on the main dashboard for all
  human roles: shows alerts (channel chip, time, the matched phrase highlighted,
  the snippet, Play button, Dismiss button, link to the radio page), polls
  every 15 s with the LCDashSession pattern, empty state "No radio keyword
  alerts", and the label "Machine-generated. Unverified. Advisory only."
  Visual only: no sound, no notifications.

## Audit events added

`radio.segment.correct`, `radio.segment.correct_revert`, `radio.fix.add`,
`radio.fix.remove`, `radio.watch.add`, `radio.watch.change`,
`radio.watch.remove`, `radio.alert.dismiss`. (Existing: `radio.segments.list`,
`radio.segment.listen`.) Same fail-closed rule for writes: no audit record,
no change.

## Infrastructure

Content bucket lifecycle: add a rule for prefix `radio/corrected/` expiring
after 365 days. Task role: Get/Put on `radio/corrected/*` (CopyObject needs
GetObject on the source and PutObject on the destination), and ListBucket
conditions cover both prefixes. New env var `LCDASH_RADIO_CORRECTED_PREFIX`
(`radio/corrected/`). No new buckets, keys, or resource types.
