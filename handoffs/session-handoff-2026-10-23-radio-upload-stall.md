# Session handoff, 2026-10-23 (evening): radio upload stall, ingest fix ready for release

Follows `handoffs/session-handoff-2026-10-23-nexisalert-roster.md`. Verified facts
first, recommendations after. No credentials here.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main`, two local commits after the pushed `aaed12f`: `f7522c6` (ingest fix) and this handoff. NOT pushed. | 2026-10-23 |
| LCDash pilot (AWS) | still revision 118, image `release-fdf657f7a404`, i.e. WITHOUT the fix. Clips over 1 MiB (about 65 s) still answer 500 until released. | 2026-10-23 |
| `.227` uploader | drained: 1,152 segments uploaded 13:27-13:32 EDT, 0 failures, queue at live level (2). One clip skipped via `skip_segment_prefixes`. | 2026-10-23 13:32 EDT |
| Tests | application full suite passed (see line below), radio routes 77 passed; infrastructure untouched this change. | 2026-10-23 |

Full suite line: 2051 passed, 5 skipped, 1 warning, 2618 subtests passed in 64.13s (0:01:04)

## What happened

- Ted reported no radio transcription on the page since about 03:00 EDT.
- `.227` was healthy throughout (capture, transcribe, upload all active, no
  restarts). The uploader had been stuck since 03:45 EDT on
  `ems_tx_20261023T074358_566354Z_54ebe071` (75 s EMS clip, 1,136,364 bytes):
  the pilot answered HTTP 500 on every retry (5 min backoff), and the uploader
  stops at the first retryable failure to keep order, so 1,125 segments queued
  behind it. Nothing was lost; transcripts kept being written locally.
- Pilot log (`/lcdash/lcdash-p1-logan-use1/web`): `FileNotFoundError: No
  usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/app']`.
  Starlette's `MultiPartParser` spools a file part over 1 MiB
  (`spool_max_size`) to a `SpooledTemporaryFile`; the container runs as uid
  10001 with `readonly_root_filesystem=True` and `/tmp` as a Fargate bind
  volume that the user cannot write. Clips under 1 MiB stay in memory, which is
  why 12,580 of 12,581 clips in 15 days worked. The error appears in the log only
  today; earlier "500" matches were port numbers in `201` lines.
- Unblock (approved): the segment id was added to `skip_segment_prefixes` in
  `/opt/mindshare-radio-capture/upload_config.json` (backup
  `upload_config.json.bak-20261023-skip`) and `mindshare-radio-upload` restarted
  13:27 EDT. The skipped clip stays on `.227` under the 15-day audio retention.
- Fix (committed `f7522c6`, not released): `app/main.py` sets
  `parser.spool_max_size = radio_service.MAX_REQUEST_BYTES` for the ingest
  parser; the body is already capped by `capped_stream`. Regression test
  `test_a_clip_over_one_mib_is_ingested_without_a_temp_directory` (1.2 MB clip,
  `tempfile.TemporaryFile` and `gettempdir` patched to raise) passes with the
  fix and fails with the production error without it. Doc note added to
  `docs/MINDSHARE_RADIO_INGEST.md`.

## Open items and risks

1. **Release `f7522c6`** through the guarded path (AGENTS.md): push `main` (Ted's
   yes), both suites, release-builder stack, CodeBuild
   `IMAGE_TAG=release-<12 hex>`, ECR scan, named change set on the foundation
   stack with only `PilotImageDigest`, execute after Ted types the name, verify
   rollout. Until then any clip over 1 MiB fails and will stall the uploader again
   (the skip list would need another entry).
2. **Uploader poison-segment handling** (`radio_listener/uploader.py`): a 5xx is
   retried forever and blocks the queue silently. Proposed: after N server
   errors on one segment, park it (`queue/failed` or a marker) and continue, and
   surface "head segment failing since <time>" in the heartbeat so the page's
   listener health shows it. Separate work package.
3. `HOME=/tmp/home` and `XDG_CACHE_HOME=/tmp/cache` in the task definition point
   into the same unwritable `/tmp`. Nothing needs them today; if a library ever
   caches there it will fail the same way. Infra follow-up: a writable temp
   (Fargate has no tmpfs; an init step or a different owner for the mount).
4. Optional: re-send the skipped clip after the release by removing its prefix
   from `skip_segment_prefixes` and restarting the uploader (its transcript and
   audio are still on `.227`).
5. Carried: items from the two earlier 2026-10-23 handoffs.

## Exact next action

Ask Ted to push `main`, then run the release path for `f7522c6` and ask him to type
the change-set name. Services on `.227` were left running; nothing stopped.
