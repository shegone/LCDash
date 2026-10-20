# Session handoff, 2026-10-20: radio VHF channels and group tabs (not deployed)

## Headline

Thirteen analog VHF dispatch channels were added to the listener configuration
and the radio page was reworked into group tabs. Nothing is installed on `.227`
and nothing is released to the AWS pilot yet: both wait for Ted's approval.

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

Branch `main`. Services untouched; the pilot and `.227` are as left in the
2026-10-20 handoff (web revision 114, three `.227` services running).
