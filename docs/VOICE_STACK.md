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
- Model loader: automatically restores both models into a persistent cache
- User interface: `/voice`
- LCDash proxy endpoints:
  - `GET /api/voice/status`
  - `POST /api/voice/speech`
  - `POST /api/voice/transcribe`

The browser never connects directly to the speech container.

The Speaches image is pinned by digest so a future upstream `latest` update
cannot silently change the tested production runtime.

## MAE conversational voice mode

The MAE page includes an optional **Start voice mode** control. Voice mode:

1. Requests microphone permission from the user's browser.
2. Listens locally until it detects a natural pause.
3. Sends the temporary recording to LCDash for local transcription.
4. Submits the transcript through MAE's existing audited, inquiry-only chat
   workflow.
5. Generates MAE's spoken answer locally and resumes listening.

The user can end the session at any time. Microphone capture is paused while
MAE speaks to prevent feedback, and the beta transcription endpoint does not
store the recording. Individual written answers also include a **Listen**
button.

Browser microphone access requires either HTTPS or a localhost connection.
Remote supervisor access must therefore use the secured HTTPS dashboard URL.

## Pronunciation dictionary

LCDash applies speech-only pronunciation rules before text reaches the voice
engine. Displayed and stored text is not changed.

- `MAE` is spoken as `May`.
- `911`, `9-1-1`, and `9 1 1` are spoken as `nine one one`.

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
