# Session handoff, 2026-10-20: radio VHF/DMR/LMARS channels and group tabs

## Headline

Analog VHF, DMR and LMARS channels were added to the listener configuration and
the radio page was reworked into group tabs. The listener config is installed on
`.227`; the web release is live (revision 116). See the updates at the end for the
revised channel map.

## Decisions (Ted, 2026-10-20)

- The twelve district channels and SIRN Multi in the Mindshare sheet table are
  approved. They are analog VHF dispatch channels, nothing secure; names carry
  a `_vhf` suffix.
- Group tabs Law, EMS, Fire, Other, with a per-channel chip row. Approved.
- The three hot-standby cards (235.98.99.35 to .37) are now DMR channels. Ted
  will supply names and addresses; they are not configured.

## What changed

- `radio_listener/config.json`: 16 channels (was 3). Source: Mindshare sheet
  v117 (`MS3042_SI_LoganCountyWVSystemInformation_v117.xlsx`, kept out of Git:
  it holds console passwords and authorization keys). Table in
  `radio_listener/README.md`. TX is the `100NN` port and RX the `120NN` port,
  the convention already proven by the IRP channels; the sheet's card-cage
  columns list them reversed.
- `app/services/radio_service.py`: `GET /api/radio/segments?channel=` accepts a
  comma-separated list (up to 24; each name validated; SQL `channel =
  ANY(%s::TEXT[])`, bound parameter). Single names behave as before.
- `templates/mindshare_radio.html`, `static/js/lcdash-admin-radio.js`,
  `static/css/lcdash-admin-radio.css`: tabs are All, Law, EMS, Fire, Other. A
  chip row narrows a group to one channel (counts from the 24 h statistics).
  Cards, health tiles and watch-keyword checkboxes use friendly names from
  `CHANNEL_CATALOG`; the old per-channel tab creation is gone. Dashboard alert
  chips (`lcdash-radio-alerts.js`) colour by the same name prefix.
- Cache-bust strings: radio page `20261020-radio8`, dashboard alerts
  `20261020-radio4`.
- Tests: `tests/contracts/test_radio_routes.py` (group filter and rejection of
  bad lists), `tests/contracts/test_radio_ui_assets.py` (group tabs, catalog
  matches `config.json`, VHF addresses and ports).
- Checked in the in-app browser against a stubbed API (not the real page; no
  sign-in is possible for an agent): tabs, chip counts, group and single-channel
  queries, Other group picking up an unlisted channel, 17 watch checkboxes.

## Rules to remember

- A channel joins a group by name prefix: `law`, `ems`, `fire`; anything else is
  Other. Name the DMR channels accordingly (for example `ems_ward_dmr`) or they
  land in Other.
- `MAX_TERM_CHANNELS` is 16 (watch terms): exactly the current channel count.
  Raise it in `app/services/radio_text.py` before adding a seventeenth channel
  to a keyword.

## Not done / needs approval

1. Push to `main`, release through the guarded path, change set execution.
2. Install the new `config.json` on `.227` (Ted's approval is on record for the
   radio services only; do it at a quiet moment) and restart capture, upload
   and transcribe. Take the file from `git show HEAD:radio_listener/config.json`
   (LF line endings). Back up the old config first.
3. After install: watch the transcription queue
   (`/var/lib/mindshare-radio-capture/data/status/capture_stats.json`, pending
   count). Transcription is one serial GPU worker on large-v3; thirteen added
   channels may queue. Confirm each channel shows traffic or is quietly idle,
   and that the switch on `10.5.2.12` actually delivers the new groups.
4. DMR channels: names, addresses and ports from Ted.
5. Not captured and why: Command Bus MRI2 (`235.98.99.50` to `.56`) is on the
   `10.10.30.x` network, probably unreachable from `10.5.2.12`; the
   `192.168.1.x` cards (41 to 45) are unnamed; Aux I/O (40) is not audio.

## State

Branch `main`. Pilot web revision 117 (digest `sha256:d85f5bea...`). `.227`
runs the listener config as of the update below; all three services active.

## Update (later 2026-10-20): revised sheet, DMR and LMARS

Ted supplied a revised v117 sheet. It changes the channel map, so the first
config (commit `d0547b3`, installed on `.227` at 15:10 local) was partly wrong:

- Card 23 is now DMR EMS and card 27 is DMR Fire (the first sheet had them as
  EMS North and Fire North VHF). Fire North moved to 35; DMR Law is 36; the
  hot-standby cards are gone. The three LMARS channels (41 to 43) are new.
- Config is now 21 channels: `ems_dmr`, `fire_dmr`, `law_dmr`, `fire_north_vhf`
  (35), `fire_lmars`, `ems_lmars`, `law_lmars` (names follow the group-prefix
  rule so they sit under Law, EMS and Fire).
- The sheet now calls the 32 to 34 cards "SIRN" (the first said "IRP"). The
  channel names `law`, `ems`, `fire` are unchanged so history keeps; the page
  label is "SIRN". Ted should say if "IRP" is what operators use.
- `MAX_TERM_CHANNELS` raised 16 to 24 so a keyword can name every channel.
- Mislabelled history: segments stored as `ems_north_vhf` (about 32 in the
  first hours) and `fire_north_vhf` (2) are DMR audio. Not relabelled.
- Change set `radio-vhf-d0547b3-20261020` is stale (its image carries the old
  catalog). Do not execute it; delete it with Ted's approval.
- Release for the corrected map: commit `ea6ef83` (on both remotes), image tag
  `release-ea6ef8373308`, digest
  `sha256:4c266188ae14d6c132a97002bd874487b645028d7b9a5bfe100d76d9357f7422`,
  the same nine accepted ECR findings. Change set
  `radio-dmr-lmars-ea6ef83-20261020` (standard five resources, only
  `PilotImageDigest` differs) was executed after Ted typed its exact name.
  Verified: foundation `UPDATE_COMPLETE`; web task definition revision 115
  running that digest, rollout COMPLETED 1/1, ALB target healthy; `/mindshare/radio`
  redirects to Cognito, unsigned ingest answers 401; 71 signed ingests returned
  201 in the first minutes, no errors in the web log. Not checked: the signed-in
  page in a browser (no sign-in possible for an agent). Ted should open
  `/mindshare/radio` and confirm the Law/EMS/Fire/Other tabs and chips.
  Rollback target: digest `sha256:1ebf4a96a1dcb3f2ddcb112ca0dcc527669bf205b6f84c9d2662409489d847dc`
  (revision 114).
- `.227` runs the 21-channel config (installed 16:30 local, 42 listeners;
  earlier 16-channel config and the original are in
  `/opt/mindshare-radio-capture/backup-pre-vhf/`). LMARS multicast reaches
  `10.5.2.12` (signalling packets seen); no LMARS voice heard yet.

Open at the end of the day: delete the stale change set
`radio-vhf-d0547b3-20261020` (needs Ted's approval); decide whether to relabel
the ~34 segments stored as `ems_north_vhf` / `fire_north_vhf` (DMR audio);
confirm LMARS voice arrives on a real transmission; confirm whether operators
say "IRP" or "SIRN".

## Update (evening 2026-10-20): colours, folded health, DMR relabel migration

Built after web revision 115 and released together (see the release note below
once it is filled in):

- `c98d0f2` channel labels coloured by discipline: law blue, EMS green, fire red
  (card chips, group tabs, channel chips, dashboard alert chips). "Other"
  channels use violet, amber, slate, magenta. Cache-bust: page `20261020-radio9`,
  dashboard alerts `20261020-radio5`.
- `93c1691` listener health folded behind a Show/Hide button; a one-line status
  (reporting / NOT reporting / undecoded audio / unavailable) stays on screen.
  Open state is remembered in localStorage.
- Relabel migration (Ted chose "option 1"): the end of `database/radio_schema.sql`
  moves segments stored as `ems_north_vhf` to `ems_dmr` and `fire_north_vhf` to
  `fire_dmr` when they started before 2026-10-20 20:30:13 UTC (the moment the
  corrected listener config went live). Later rows under those names are real
  Fire North and stay. It runs once: a marker row in the new
  `lcdash_radio.schema_migrations` (name `relabel-dmr-2026-10-20`) records the
  counts moved, so it cannot run again. Audio objects keep their S3 keys. The
  audit-event constraint was deliberately left alone.
  - Why a migration: the deployment profile cannot `ecs:RunTask`, and the
    database is reachable only from inside the VPC.
  - Tested against a real PostgreSQL 17.6 (EnterpriseDB binaries downloaded with
    Ted's approval to the scratchpad, used only for testing, then deleted): an
    upgrade from the v4 schema, a fresh database, re-runs, and a forced failure
    rolling back. The live tests are in
    `tests/contracts/test_radio_relabel_migration.py` and run when
    `LCDASH_TEST_PG_DSN` is set; without it they skip (3 skipped) and the static
    checks run.
  - Two older guard tests in `test_radio_units.py` were adjusted on purpose:
    the schema now has two `DO $$` blocks (was one). The "no DROP TABLE /
    DELETE FROM / TRUNCATE" guard and "no CREATE after the first DO block" still
    hold.
  - After deploy, the first task to start applies it. Verify by looking at the
    DMR chips on the EMS and Fire tabs (history appears there). The counts
    moved are in `lcdash_radio.schema_migrations.detail`; there is no endpoint
    to read it, so read it from a psql session if exact numbers matter.

Release for this update: commit `ebed969`, image tag `release-ebed969f7dc5`,
digest `sha256:778f9a959b8ec39c3549c76993cc99a5ecdbe4143e4bfcb835e593f9ea2e5456`,
the same nine accepted ECR findings. Change set `radio-dmr-fold-ebed969-20261020`
was executed on 2026-10-21 after Ted typed its exact name (the SSO session had
expired overnight and was renewed first). Verified: foundation `UPDATE_COMPLETE`;
web task definition revision 116 running that digest (task started 10:05 EDT),
rollout COMPLETED 1/1, ALB target healthy; `/mindshare/radio` redirects to
Cognito; unsigned ingest 401; signed ingests 201 from the new task, no errors
in the web log, so the schema (and relabel migration) applied cleanly on its
first radio request. Not verified: the moved row counts (no endpoint; see
`lcdash_radio.schema_migrations.detail`) and the signed-in page in a browser.
Ted should open `/mindshare/radio`: colours, the folded Listener health bar, and
DMR history under the EMS and Fire tabs. Rollback target: digest
`sha256:4c266188ae14d6c132a97002bd874487b645028d7b9a5bfe100d76d9357f7422`
(revision 115). Note: rolling back the image does not undo the relabel (the
marker row stays and the moved rows keep their new channel names).

## Update (2026-10-21): scanner mode (auto-play), not yet released

Ted asked for the page to play calls as they come in, with a Play button on each
call to replay. Built, tested, pushed (`5288951`) and staged for release.

- Toolbar switch "Auto-play new calls" (off on every page load, never remembered,
  so sound does not start unasked). It plays calls that arrive while it is on,
  for the tab and channel chip being watched, oldest first, one at a time.
  Nothing already on screen plays; calls older than 3 minutes when they appear
  are skipped; at most 10 wait (oldest dropped, "N skipped" shown).
- It polls the active feed every 10 s (every list is audited, so not faster) and
  keeps polling while the page is hidden. Switching tab or channel chip clears
  the queue, and the first catch-up refresh after a switch or after turning it on
  is silent (what arrived while nobody was listening is not played).
- It uses the Play button's own path (`startPlayback`), so every listen is
  audited as usual and there is still one `Audio` object. A person's play or
  pause wins: the scanner waits behind anything playing or paused. If the browser
  refuses sound it says "Click anywhere on the page to start playing" and keeps
  the call. The playing card is outlined.
- Cache-bust `20261021-radio10`. Tests: `ScannerModeTests` in
  `tests/contracts/test_radio_ui_assets.py`; behaviour checked in a browser
  harness with simulated audio (order, staleness, tab switch, blocked sound,
  pause, switch off).
- Not tested with real sound or on a real signed-in page. A tab left in the
  background can still be throttled by the browser; keep it visible or on a
  second screen. Operators may want a volume control or a per-channel mute; not
  built.

Release for scanner mode: commit `5288951`, image tag `release-5288951e6343`,
digest `sha256:d85f5bea27d7402916398b6e01e391d18a3c8de7da48077eb6116b65815c6cc1`,
the same nine accepted ECR findings. Change set `radio-scanner-5288951-20261021`
(standard five resources, only `PilotImageDigest` differs) was executed on
2026-10-21 after Ted typed its exact name. Verified: foundation `UPDATE_COMPLETE`;
web task definition revision 117 running that digest (task started 10:31 EDT),
rollout COMPLETED 1/1, ALB target healthy; `/mindshare/radio` redirects to
Cognito; unsigned ingest 401; 15 signed ingests 201, no errors in the web log.
Not verified: the signed-in page and real sound (static files are behind
sign-in, so the new script could not be fetched from outside). Ted should open
`/mindshare/radio`, turn on "Auto-play new calls" and listen to a few calls.
Rollback target: digest
`sha256:778f9a959b8ec39c3549c76993cc99a5ecdbe4143e4bfcb835e593f9ea2e5456`
(revision 116).
