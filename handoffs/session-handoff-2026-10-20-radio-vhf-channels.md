# Session handoff, 2026-10-20: radio VHF/DMR/LMARS channels and group tabs

## Headline

Analog VHF, DMR and LMARS channels were added to the listener configuration and
the radio page was reworked into group tabs. The listener config is installed on
`.227`; the web release is built and staged but its change set has not been
executed. See the update at the end for the revised channel map.

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

Branch `main`. Pilot web revision 114 until a change set is executed. `.227`
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
  `radio-dmr-lmars-ea6ef83-20261020` is staged (standard five resources, only
  `PilotImageDigest` differs) and waits for Ted to type its exact name.
  Rollback target: digest `sha256:1ebf4a96a1dcb3f2ddcb112ca0dcc527669bf205b6f84c9d2662409489d847dc`
  (revision 114).
- `.227` runs the 21-channel config (installed 16:30 local, 42 listeners;
  earlier 16-channel config and the original are in
  `/opt/mindshare-radio-capture/backup-pre-vhf/`). LMARS multicast reaches
  `10.5.2.12` (signalling packets seen); no LMARS voice heard yet.
