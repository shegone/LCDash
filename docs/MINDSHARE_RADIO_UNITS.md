# Mindshare radio: who is talking (radio ID) and unit names (v4)

Status: design, 2026-10-18. Extends `MINDSHARE_RADIO_INGEST.md`. Ted Sparks
confirmed in chat that the RTP payload-type-103 text carries the radio ID, and
asked for both: show the radio ID on each segment, and an admin-managed unit
name table ("98426 = Medic 4"). Radio IDs, talkgroups and unit names are shown
to every signed-in human role, like the rest of the radio page.

## What payload type 103 carries (observed 2026-10-17, passive 300 s sample)

Voice is 160-byte PCMU. PT103 payloads are 8..30 byte signalling messages:
an 8-byte header followed by ASCII text with colon-separated fields.

```
header (8 bytes): 01 00 10 <03|13> 05 08 LL 00     LL = length of the ASCII part
ascii:            <radio_id>:G<talkgroup>:<f3>:<f4>::<f5>:<f6>   (TX streams: radio transmissions)
                  <console label>::::<console label>               (RX streams: dispatch console side)
keepalive/markers (8 bytes, no text): 14000000 00000000, 03000000 00000000, 03000000 00000001
```

Messages are re-sent every ~100 ms during a transmission and fill in
progressively (the first has empty fields, later ones complete). Test vectors
(hex -> parsed):

| hex | ascii | parsed |
| --- | --- | --- |
| `010010030508160039383432363a47343430373a3337313a303a3a303a30` | `98426:G4407:371:0::0:0` | radio_id `98426`, talkgroup `G4407` |
| `01001003050813003434303539333a47343430353a3a3a3a303a30` | `440593:G4405::::0:0` | radio_id `440593`, talkgroup `G4405` |
| `0100100305080d0039383432363a3a3a3a3a303a30` | `98426:::::0:0` | radio_id `98426`, no talkgroup |
| `01001003050808003a3a3a3a3a303a30` | `:::::0:0` | nothing |
| `0100101305081600506f736974696f6e323a3a3a3a506f736974696f6e32` | `Position2::::Position2` | console_label `Position2` |
| `01001013050812006d6178706c75733a3a3a3a6d6178706c7573` | `maxplus::::maxplus` | console_label `maxplus` |
| `1400000000000000`, `0300000000000000`, `0300000000000001` | (none) | ignored |

Parsing rules (be strict; anything that does not fit is ignored, never raised):
`payload[0] == 0x01`, `len(payload) == 8 + payload[6]`, the text is printable
ASCII; split on `:`. Field 0 matching `^[0-9]{1,10}$` is the **radio_id**; field
1 matching `^G[0-9]{1,6}$` is the **talkgroup**; otherwise a field 0 matching
`^[A-Za-z0-9][A-Za-z0-9 _.-]{0,31}$` that is not all digits is the
**console_label**. The other fields are not understood and are not stored.

## Listener (`radio_listener/`, stdlib only)

- `capture.py`: parse PT103 packets with the rules above (pure function,
  unit-tested with the vectors). Per stream keep the latest parsed signal and
  the time it was seen. A segment takes the signal seen up to 3 s BEFORE its
  first voice packet and every signal seen DURING it; later messages that carry
  more fields win; a radio_id once set for the segment is not replaced by a
  different one (keep the first radio_id; count `signal_conflicts` internally
  but do not store it). Write `"signal": {"radio_id": "...", "talkgroup":
  "...", "console_label": "..."}` into the segment metadata JSON only with the
  keys that were found; omit `signal` when nothing was found. Audio handling
  stays byte-for-byte unchanged; parsing must never raise into the receive loop.
- `transcribe.py` already copies the job metadata into the transcript JSON;
  verify `signal` survives (add a test).
- `uploader.py`: include `signal` in the signed metadata when present, with the
  same patterns re-validated (drop invalid keys, drop the object when empty).
  Never send raw PT103 bytes.

## Server (this app)

- Ingest metadata gains OPTIONAL `signal` (object, keys only from
  `radio_id`, `talkgroup`, `console_label`, same patterns, unknown keys -> 400;
  `schema_version` stays 1; absent is fine). Update the contract doc table.
- `segments` gains `radio_id TEXT`, `talkgroup TEXT`, `console_label TEXT`
  (ALTER ... ADD COLUMN IF NOT EXISTS) and an index on `(radio_id, started_at
  DESC)` where radio_id is not null. Old rows stay NULL (not captured before).
- `units(radio_id TEXT PRIMARY KEY CHECK (radio_id ~ '^[0-9]{1,10}$'), name TEXT
  NOT NULL CHECK (char_length(name) BETWEEN 1 AND 60), updated_by TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`. Name: plain text, no control
  characters, no newlines. Max 2000 units.
- Audit events `radio.unit.set` and `radio.unit.remove` (extend the CHECK
  using the existing guarded DO-block pattern; bump its guard to look for
  `radio.unit.remove`). Fail closed like the other writes.
- Segment items gain `radio_id`, `talkgroup`, `console_label`, `unit_name`
  (from the units table, cached in-process 60 s, dropped on change; null when
  unnamed). The list API gains optional `radio_id` (exact digits) filter, and
  `q` also matches `radio_id` (prefix) and unit names (case-insensitive
  substring: resolve matching radio_ids from the cached units and use
  `radio_id = ANY(...)`, bound parameters only). All query validation happens
  AFTER the access check, as everywhere else.
- Units API: `GET /api/radio/units` (admin) returns `{"units": [{"radio_id",
  "name" or null, "segments_30d", "last_seen_at"}], "max_units": 2000}` merging
  named units with radio IDs seen in `segments` (distinct, most recent first,
  limit 500 of the seen ones); `PUT /api/radio/units/{radio_id}` (admin)
  `{"name": "..."}` upserts and returns the item (201 on create, 200 on
  update); `DELETE /api/radio/units/{radio_id}` (admin) removes the name only
  (segments keep the radio_id). Uniform 403 for non-admin before validation.
  Open the radio routes through the tier modules exactly like the other radio
  routes (human roles for reads; units management admin only, kept closed to
  the restricted tiers like the hint routes).
- A possible-noise segment still shows its radio_id.

## UI

- Card header: a "who" chip: `Medic 4 · 98426` when named, else `ID 98426`; a
  small talkgroup tag (`G4407`); for console streams `Console: Position2`. The
  chip is a button: clicking it filters every tab to that radio ID (a visible
  "Showing radio ID 98426 (Medic 4) - clear" bar). Never show an empty chip.
- Search box also finds radio ID and unit name (server side).
- Admin-only "Units" tab (gated by `is_admin` like the others): table of radio
  IDs (name, ID, segments in 30 days, last heard) with an inline name field and
  Save per row, a Clear button (two-step), a "Show only unnamed" toggle, and an
  "Add by ID" form. Note: "Names appear on every transcript card for everyone."
- Everything via textContent, two-step destructive confirms, no
  alert/confirm, mobile friendly.
