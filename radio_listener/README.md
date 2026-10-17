# Mindshare multicast capture and local transcription

This listen-only service supports up to **24 channels / 48 UDP multicast
listeners**. The initial configuration contains the three confirmed IRP
channels. Each channel has a separate TX and RX port.

## Current channels

| Channel | Multicast group | TX | RX |
|---|---|---:|---:|
| Law | 235.98.99.32 | 10032 | 12032 |
| EMS | 235.98.99.33 | 10033 | 12033 |
| Fire | 235.98.99.34 | 10034 | 12034 |

The listener joins each group on `10.5.2.12`. It decodes RTP payload type 0
(PCMU) into mono 8 kHz, 16-bit PCM WAV audio. Other payload types, including
dynamic PT 103, are counted but not interpreted. A segment is closed after
1.5 seconds without PCMU packets, or after 120 seconds, and queued for local
transcription.

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
