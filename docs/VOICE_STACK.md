# LCDash Voice Stack

## Purpose

The voice stack provides private, local speech services for:

- MAE spoken responses
- JACK spoken responses
- Supervisor voice questions
- Future listen-only Mindshare radio transcription

It does not currently connect to or record the Mindshare radio network.

## Beta architecture

- API server: Speaches, private to the Docker network
- Text to speech: Kokoro 82M ONNX
- Speech to text: Faster-Whisper distilled small English
- User interface: `/voice`
- LCDash proxy endpoints:
  - `GET /api/voice/status`
  - `POST /api/voice/speech`
  - `POST /api/voice/transcribe`

The browser never connects directly to the speech container.

## GPU upgrade

When the RTX 3080 computer is prepared, deploy the CUDA Speaches image and
retain the same API contract. Benchmark:

1. Whisper large-v3-turbo and NVIDIA Parakeet TDT 0.6B V3 for public-safety
   radio transcription.
2. Chatterbox Turbo for more expressive MAE and JACK speech.
3. Kokoro as a low-latency fallback.

No cloned voice should be used without documented permission from the speaker.

## Radio boundary

The radio ingestion service will be separate from the voice API. Before it is
enabled, document each multicast address, UDP port, codec, channel name,
retention rule, and access role. Capture must remain listen-only.
