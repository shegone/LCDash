# Mindshare multicast capture and local transcription

This listen-only service supports up to **24 channels / 48 UDP multicast
listeners**. The configuration holds 21 channels: the three SIRN channels,
13 analog VHF dispatch channels, three DMR channels and three LMARS channels.
Each channel has a separate TX and RX port.

## Current channels

Source: Mindshare system-information sheet v117, as revised 2026-10-20 (card
cages A1-A15, the three cards below A15, the single-enclosure SIRN Multi card
and the P25 DFSI gateway station lines). All are on the `10.5.2.0/26` radio
network except the LMARS cards. TX is the `100NN` port and RX the `120NN` port,
the convention the SIRN channels already prove (the sheet's card-cage columns
list them the other way round).

| Channel name | Sheet label | Multicast group | TX | RX |
|---|---|---|---:|---:|
| `law` | SIRN Law | 235.98.99.32 | 10032 | 12032 |
| `ems` | SIRN EMS | 235.98.99.33 | 10033 | 12033 |
| `fire` | SIRN Fire | 235.98.99.34 | 10034 | 12034 |
| `ems_ward_vhf` | Logan EMS WardRock | 235.98.99.20 | 10020 | 12020 |
| `ems_south_vhf` | Logan EMS South | 235.98.99.21 | 10021 | 12021 |
| `ems_park_vhf` | Logan EMS Park | 235.98.99.22 | 10022 | 12022 |
| `ems_dmr` | DMR EMS | 235.98.99.23 | 10023 | 12023 |
| `fire_ward_vhf` | Logan Fire WardRock | 235.98.99.24 | 10024 | 12024 |
| `fire_south_vhf` | Logan Fire South | 235.98.99.25 | 10025 | 12025 |
| `fire_park_vhf` | Logan Fire Park | 235.98.99.26 | 10026 | 12026 |
| `fire_dmr` | DMR Fire | 235.98.99.27 | 10027 | 12027 |
| `fire_horsepen_vhf` | Logan Fire Horsepen | 235.98.99.28 | 10028 | 12028 |
| `law_ward_vhf` | Logan Law Ward | 235.98.99.29 | 10029 | 12029 |
| `law_south_vhf` | Logan Law South | 235.98.99.30 | 10030 | 12030 |
| `law_park_vhf` | Logan Law Park | 235.98.99.31 | 10031 | 12031 |
| `fire_north_vhf` | Fire North | 235.98.99.35 | 10035 | 12035 |
| `law_dmr` | DMR Law | 235.98.99.36 | 10036 | 12036 |
| `sirn_multi_vhf` | SIRN Multi | 235.98.99.38 | 10038 | 12038 |
| `fire_lmars` | LMARS Fire | 235.98.99.41 | 10041 | 12041 |
| `ems_lmars` | LMARS EMS | 235.98.99.42 | 10042 | 12042 |
| `law_lmars` | LMARS Law | 235.98.99.43 | 10043 | 12043 |

Notes on the sheet:

- The first v117 listed EMS North (23) and Fire North (27) as analog VHF
  channels. The revised sheet shows those cards as DMR EMS and DMR Fire and
  moves Fire North to 35. Segments captured under `ems_north_vhf` and
  `fire_north_vhf` between 2026-10-20 15:10 and the config change are DMR
  audio (and, for `fire_north_vhf`, possibly a few Fire North segments).
- The LMARS cards sit on the `192.168.1.x` network (card IPs `.41` to `.43`).
  Whether their multicast reaches `10.5.2.12` is checked by looking for
  packets after install. The gateway station line for LMARS Law lists the
  same port (10043) for RX and TX; the card row (10043 / 12043) is used.
- Not captured: card 40 (Aux I/O, port 18000, not audio), the unnamed
  `192.168.1.x` cards 44 and 45, and the Command Bus MRI2 channels (50-56,
  `10.10.30.x` network, not reachable on `10.5.2.12`).

The dashboard groups a channel by its name prefix (`law`, `ems`, `fire`; any
other name is "Other"). Keep that prefix rule when naming new channels, and add
the name to `CHANNEL_CATALOG` in `static/js/lcdash-admin-radio.js` so the page
shows a friendly label (an unlisted channel still works, under its own name).
A watch keyword may name at most `MAX_TERM_CHANNELS` channels
(`app/services/radio_text.py`, now 24); raise it with the channel count.

## Local transcription

`transcribe.py` uses `faster-whisper` with the `large-v3` model, CUDA, and
`int8_float16` precision. It reads closed WAV segments from the local pending
queue and writes one JSON transcript per segment. The service pins PyAV to
`av>=11,<19`: PyAV 19 removed an argument still used by faster-whisper 1.2.1.
The downloaded model is cached under `/var/lib/mindshare-radio-capture/models`
and the worker runs with Hugging Face offline mode enabled. Audio and transcript
data remain on `.227`; this prototype has no cloud upload or LCDash connection.
Transcripts are machine-generated and require human review.

## Configuration

Add channels to the `channels` array in `config.json`. Each entry has a unique
`name`, multicast `group`, and `ports` object containing `tx` and `rx` UDP
ports. The configured limit is 24 channels. Adjust the segment idle/max
durations in the same file if needed.

## Data layout

The service data root is `/var/lib/mindshare-radio-capture/data`:

- `audio/<channel>/`: original captured WAV segments, separated by direction.
- `queue/pending/`: closed audio segment metadata awaiting transcription.
- `queue/processing/`: work currently claimed by the transcription worker.
- `queue/failed/`: metadata for segments that failed transcription.
- `transcripts/`: transcript JSON, with timing and model details.
- `summary_*.json`: packet/source counts from each capture-service run.

Directories are private to the service account; files use owner-only
permissions.

## Capacity and scope

The selector loop handles 48 listeners for 24 channels. That is socket
capacity, not a guarantee that every possible codec or traffic load has been
validated. Current transcription is serial on one GPU worker. Disk and ASR
throughput should be monitored as channels are added.

This prototype only listens to UDP multicast. It does not transmit, modify
radio equipment, make dispatch decisions, or send audio/transcripts to AWS.
The separate LCDash AWS work should use an approved integration contract
rather than connecting directly to internal queue files. The first live Law
RX/TX segments and a sample WAV have been transcribed successfully on `.227`.
