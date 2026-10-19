# Session handoff, 2026-10-17: Mindshare radio intelligence live

## Headline

Radio from the `.227` multicast listener now flows to the AWS pilot: each
completed segment is signed on `.227`, POSTed to `aws.logan911.com`, stored
(audio in S3, transcript in Postgres), and shown on an admin-only page.
Decisions (Ted, 2026-10-16): live traffic approved (nothing privileged, not on
the public scanner), admin only while testing, audio 15 days, transcripts 30.

## What changed

- Commit `1d98cca` on `main`, pushed to GitHub and Bitbucket.
  - `docs/MINDSHARE_RADIO_INGEST.md`: the v1 contract (read it first).
  - `app/services/radio_service.py`, `database/radio_schema.sql`, routes in
    `app/main.py`: `POST /api/radio/ingest` (Ed25519 signature, no secret in
    AWS), admin-only `GET /api/radio/segments`, `/segments/{id}/audio`,
    `/stats`, and the `/mindshare/radio` page (replaces the roadmap
    placeholder). Every list and playback is written to
    `lcdash_radio.audit_events` and fails closed if the audit write fails.
  - `radio_listener/`: the `.227` code imported byte-identical to what Codex
    deployed (sha256 verified), plus `uploader.py`, `upload_config.json`,
    `mindshare-radio-upload.service`, `UPLOADER.md`.
  - Foundation stack: second ALB rule (priority 6, POST + exact path
    `/api/radio/ingest`, no Cognito action), 15-day lifecycle on content-bucket
    `radio/audio/`, task-role Get/Put on that prefix plus ListBucket limited to
    it, three plain env vars. Allowlist and tests amended; `additional_grants`
    entry records Ted's approval.
- Tests at that commit: application 1,335 passed; infrastructure 149 passed.

## Live state (verified 2026-10-17)

- Release: build tag `release-1d98cca08cca`, digest
  `sha256:738493198630724ae96279c7db366e784af7a852822a05c99e4b80da730e44d2`.
  Change set `radio-ingest-1d98cca-20261017` executed; foundation stack
  `UPDATE_COMPLETE`; ECS web task definition revision 109, rollout COMPLETED,
  1/1 running, ALB target healthy.
- Previous (rollback target): revision 108, digest
  `sha256:d0beeb897ffbc617f9b0cdcafbed88bca36e6842cfc03d895799344b9d675dfa`.
  Rollback = change set with that digest (note this also removes nothing else:
  the ALB rule and grants stay in the template).
- Probes: `/` and `GET /api/radio/ingest` and `/api/radio/segments` redirect to
  Cognito; unsigned `POST /api/radio/ingest` answers 401 from the app.
- ECR scan accepted by Ted on 2026-10-17: baseline util-linux (4 HIGH, 1
  MEDIUM) plus four newer CVEs, util-linux CVE-2026-53612/53613/53614 and zlib
  CVE-2026-85091 (zlib 1.3.2-r0, no fixed version listed). Cause suspected:
  the build picks up newer Alpine packages; base image is digest-pinned.
  Revisit when fixes ship.
- `.227` (`14.1.1.227`, key auth as administrator): three services active:
  `mindshare-radio-capture`, `mindshare-radio-transcribe`,
  `mindshare-radio-upload` (installed and enabled 2026-10-17). Private ingest
  key `/opt/mindshare-radio-capture/keys/ingest_ed25519.pem` (0600); public key
  `sZTJKqLKu34SpH5JMwyvydrw6wXlq1zc9fp66aTgJfk=` is in
  `infrastructure/lcdash_pilot/config.py`. First catch-up run uploaded the
  backlog with 201 responses, no rejects. Local retention on `.227` follows the
  same 15/30 days (uploaded items only).
- Ted offered the `.227` administrator password in chat; it was not needed
  (key auth, passwordless sudo) and is not stored anywhere. Ted plans to change
  it later.

## Open items / next actions

1. Ted opens `/mindshare/radio` signed in as admin and confirms the page: it was
   unit-tested but never opened in a browser by an agent (no sign-in possible).
2. Fire channel has produced no traffic (low-use channel per Ted); payload
   types other than PCMU are counted by capture but only written on shutdown.
   Add periodic stats logging to `radio_listener/capture.py` (needs a capture
   restart at a quiet moment) so drops and PT 103 are visible.
3. Silent-listener alert: the page shows "last segment received"; no alarm yet.
4. Playback has no HTTP Range support; a seek re-fetches and logs another
   listen. Acceptable for testing.
5. Widen access beyond admin when Ted says it works well (role gate is
   `_require_pilot_admin`; tier middlewares already 403 other roles).
6. Pin exact versions in `radio_listener/requirements.txt` (currently ranges);
   a synthetic `pipeline_check_law_tx_20261016` record remains in `.227` data
   and is skipped by the uploader.
7. Rotate the `.227` administrator password when Ted decides.

## Codex catch-up

Codex built the `.227` listener (handoff zip 2026-10-16). Claude took over both
ends. The listener code now lives in this repo under `radio_listener/`; edit it
here and copy to `/opt/mindshare-radio-capture` (use LF line endings: take
files from `git show HEAD:radio_listener/<file>`, not the CRLF working copy).
Applications and services were left running.

## Update 2026-10-17 (later): page redesign and radio v2

- `0198935`: tabbed card feeds with a real Play button (web revision 110).
- `e540ba6` (web revision 111, image `release-e540ba61b2b9`, digest
  `sha256:f61d7e06fa2d03d14d0ce79e07ee4abf40594c0992bbf34ec5e01d14683ceb5d`,
  change set `radio-v2-e540ba6-20261017`): radio v2 per
  `docs/MINDSHARE_RADIO_CORRECTIONS.md`.
  - Open to every human role (user, supervisor, dispatcher, fire_officer,
    admin); avatar and unidentified stay denied. Tier middlewares open exactly
    the radio human routes (`app/core/radio_access.py`).
  - Any user can correct a transcript; machine text and every edit are kept
    (`lcdash_radio.corrections`). Corrected audio is copied to
    `radio/corrected/` (365-day lifecycle) and the row kept 1 year
    (`retain_until`). Uncorrected: audio 15 d, transcript 30 d.
  - Admins manage fixes (find/replace at display time) and watch phrases;
    matches raise advisory alerts in a dashboard panel (visual only; no sound,
    no CAD/radio/paging/station-alert output).
  - Schema applies under `pg_advisory_xact_lock` (concurrent appliers
    deadlocked in a real-Postgres test). The SQL was verified on PostgreSQL 16
    and 18 (upgrade from v1, fresh, idempotent); production is RDS 17.
  - Same nine ECR CVEs as accepted earlier; no new findings.
- Verified after release: foundation `UPDATE_COMPLETE`, rollout COMPLETED
  1/1, ALB healthy, 41 ingest POSTs returned 201 in 15 minutes, no errors in
  the web log, all three `.227` services active.
- Known: corrected-audio lifecycle counts from the copy time while
  `retain_until` moves with each edit, so audio can expire before the text
  does (the UI shows "audio expired"). Ted has not yet signed in to confirm
  the new UI.
- Next ideas: Whisper hint words/glossary pushed to `.227`; training from the
  corrections history; periodic capture stats logging; silent-listener alarm.

## Update 2026-10-17 (evening): v3 health + admin delete/reset live

- Release `98e803c` (web revision 112, image `release-98e803c9d96a`, digest
  `sha256:f51687f86ff17738b4033c2256c2d421e05e762d8bbfcabcc2fe54efc37c177f`,
  change set `radio-v3-admin-98e803c-20261017` executed by Ted's exact-name
  approval). Same nine accepted ECR CVEs. Includes:
  - Listener health: signed `POST /api/radio/heartbeat` (same ALB machine rule,
    now two paths, POST only), `lcdash_radio.listener_status`, Listener health
    panel on the radio page, `hints` table and admin Hint words tab.
  - Silent-listener alarm `lcdash-p1-logan-use1-radio-listener-silent`
    (heartbeat log line `radio-heartbeat ok`, Sum < 1 for 2 x 5 min, missing
    data = breaching, SNS alerts topic + OK action). It was created in ALARM
    (no data yet) and should move to OK once two full periods contain
    heartbeats; Ted may get one ALARM and one OK email. VERIFY it reads OK.
  - Admin users page: Reset password (Cognito emailed code, or invitation
    re-sent for a never-signed-in user; the app never sees a password) and
    Delete (typed email, refuses self and the last enabled admin). Task role
    gained `cognito-idp:AdminDeleteUser` and `AdminResetUserPassword` on this
    pool only; `AdminSetUserPassword` still withheld. Delete/reset are logged
    as `pilot-access delete_user|reset_password target=... acting_subject=...`
    (CloudWatch web log = audit trail). Not exercised against real Cognito;
    Ted should test reset on a throwaway user first.
- `.227` (installed 20:53 local): new `capture.py`, `transcribe.py`,
  `uploader.py`, `config.json` from `c9cb9f2`; old files backed up to
  `/opt/mindshare-radio-capture/backup-pre-v3/`. All three services restarted
  and active; heartbeats arrive every 60 s (HTTP 200); stats file at
  `/var/lib/mindshare-radio-capture/data/status/capture_stats.json`.
- Hint words are OFF at the listener (`glossary_mode: off`). A/B on 120 real
  segments: hotwords and prompt gave no clear gain and added invented place
  names; the prompt method echoed itself. Re-test before enabling.
- NEW FINDING: every active stream also carries RTP payload type 103 packets
  (about 7-15% of packets next to PCMU; Fire TX showed 9 PT103-only packets and
  no PCMU). They are counted but not decoded. Likely PTT/signaling, not voice,
  but UNCONFIRMED. Next action: sample a few PT103 packets (tcpdump on the
  radio NIC, header + first bytes only) to identify them before deciding they
  can be ignored. The Listener health panel now shows the undecoded count.
- Baseline hallucination: ~18% of plain transcripts of clips under ~2 s are
  boilerplate ("Thanks for watching!", "Bye."). Suggested: flag short or
  high no_speech_prob clips as "Possible noise" in the UI (do not delete text).
- Open: hint-cap race (admin 201st hint under 16 concurrent adds; harmless),
  corrected-audio lifecycle vs `retain_until` edge, no Range support on audio.

## Update 2026-10-19: radio v4 (who is talking) live

- Release `ea823a7` (web revision 113, image `release-ea823a783ba2`, digest
  `sha256:b5b7e13dd06902691b8201adb5c5dcf4e332a442243b83108383ed1edbc36172`,
  change set `radio-v4-ea823a7-20261019`, executed after Ted typed its name;
  standard five-resource image bump; same nine accepted ECR CVEs).
- Also shipped since the 10-17 notes: possible-noise flag (`f65b7f8`: boilerplate
  and short low-confidence clips get a display-only badge, hide toggle, no
  alerts; PT103 no longer reported as "undecoded audio"), clearer reset-password
  error (`e58c461`).
- PT103 decoded (docs/MINDSHARE_RADIO_UNITS.md): ASCII colon fields after an
  8-byte header; TX streams carry `<radio_id>:G<talkgroup>:...`, RX streams a
  console label (`Position2`, `maxplus`). Ted confirmed it is the radio ID.
  Fields 3+ (e.g. `371`, trailing `0:0`) are not understood and not stored.
- `.227` (installed 2026-10-19 00:24 UTC): new `capture.py`, `uploader.py`
  (backup in `/opt/mindshare-radio-capture/backup-pre-v4/`); capture and upload
  services restarted, transcribe untouched. First live segments carried
  signal (EMS TX radio ID + talkgroup; EMS RX console label); 11 ingests 201,
  no errors, alarm OK, 2,864 segments uploaded, none rejected.
- App: segments carry `radio_id`, `talkgroup`, `console_label`, `unit_name`;
  who-chip with click-to-filter; search by ID or unit name; admin Units tab
  (`lcdash_radio.units`, events `radio.unit.set|remove`); visible to all human
  roles. Older segments have no radio ID (not captured before).
- Hardening from real-Postgres testing: control characters in `q` -> 400;
  schema apply retried on deadlock. Known and accepted: unit/hint caps are soft
  under concurrent admin writes; a writer can still lose a deadlock to a
  starting task during a rolling deploy (fail-closed, retry).
- Open: Ted to review the AGENTS.md / memory cleanup audit (stale on-prem
  facts, `.227` radio role missing from AGENTS.md); unit names to be entered by
  admins; Mindshare protocol docs would let us decode the remaining PT103 fields.
