# Session handoff, 2026-10-23 (late): uploader parking and stall reporting, ready to ship

Follows `handoffs/session-handoff-2026-10-23-radio-upload-stall.md`. Facts first.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main`, local commits after the pushed `aaed12f`: `f7522c6` (ingest spool fix), `e859081` (handoff), `5da506e` (uploader parking), this handoff. NOT pushed. | 2026-10-23 |
| LCDash pilot (AWS) | still revision 118 (no fix released). Server side of this change (optional heartbeat fields, page warning) ships with the next release together with `f7522c6`. | 2026-10-23 |
| `.227` uploader | running the OLD `uploader.py` (no parking). Queue at live level after the 13:27 EDT restart; one clip still on the skip list. | 2026-10-23 13:32 EDT |
| Tests | full application suite: 2058 passed, 5 skipped, 1 warning, 2623 subtests passed in 62.06s (0:01:02). Focused: uploader 69, heartbeat + health 153+1. Infrastructure untouched. | 2026-10-23 |

## What changed (`5da506e`)

- `radio_listener/uploader.py`: `UploaderStats` tracks the head segment's
  consecutive 5xx replies; `process_segment` parks it (`uploaded/<id>.parked`,
  one `status=parked` log line, returns `"parked"`) once
  `park_after_server_errors` is reached (default 12, 0 disables; new config key,
  normalized in `load_configs`); `process_queue` counts `parked` and continues.
  `has_final_marker` includes `.parked`; retention leaves parked files alone
  (it only touches `.done`). Heartbeat `uploader` gains `head_failing_since`,
  `head_attempts`, `parked_total` (marker count, survives restarts).
- `app/services/radio_service.py`: the three fields are optional in
  `validate_heartbeat` (`_hb_object` now takes `optional=`); absent = null/0.
- `static/js/lcdash-admin-radio.js`: Queues card warns "oldest segment refused by
  the server N times since <time>; later segments are waiting" and "N parked on
  the listener after repeated refusals". NOT seen in a browser (needs Cognito).
- Docs: `radio_listener/README.md` (markers, re-queue), `radio_listener/UPLOADER.md`
  (retry/park rules, heartbeat fields, replay), `docs/MINDSHARE_RADIO_HEALTH.md`
  (schema and a new section).

Design choice: only `5xx` replies count. Network errors and `401` affect every
segment, so counting them would park one segment per hour during an outage. A
server-wide `5xx` outage still parks one segment per N attempts; accepted and
visible on the page.

## Deploy order (nothing deployed yet)

1. Push `main` (Ted's yes).
2. Release the pilot (`f7522c6` + `5da506e` server side) through the guarded path.
   The server accepts old and new heartbeats, so the order with step 3 does not
   matter for correctness, but releasing first means the page shows the new
   warnings as soon as the listener sends them.
3. On `.227` (authorised for radio services; report it): back up
   `/opt/mindshare-radio-capture/uploader.py`, copy the new file with LF endings
   (`git show HEAD:radio_listener/uploader.py > ...`), optionally add
   `"park_after_server_errors": 12` to `upload_config.json` (the default applies
   without it), `sudo systemctl restart mindshare-radio-upload`, then check
   `journalctl -u mindshare-radio-upload -n 5` shows "Uploader ready" and
   `heartbeat status=200`. Only the upload service restarts; capture and
   transcribe are untouched.
4. Optional: after the pilot release, re-send the skipped clip by removing
   `ems_tx_20261023T074358_566354Z_54ebe071` from `skip_segment_prefixes` and
   restarting the uploader.

## Exact next action

Ask Ted to push `main`; then the release path (change-set name from Ted); then
the `.227` uploader copy and restart. Services were left running.
