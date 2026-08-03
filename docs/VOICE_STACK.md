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
- MAE text to speech: Qwen3-TTS 1.7B VoiceDesign with the approved synthetic
  female MAE profile
- JACK and fallback text to speech: Kokoro 82M ONNX
- Speech to text: Faster-Whisper distilled small English
- Model loader: automatically restores both models into a persistent cache
- User interface: `/voice`
- LCDash proxy endpoints:
  - `GET /api/voice/status`
  - `POST /api/voice/speech`
  - `POST /api/voice/transcribe`

The browser never connects directly to either speech container. Qwen3-TTS is
private to the Docker network and has no CAD credentials or direct CAD access.

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

MAE uses the Qwen3-TTS synthetic female voice. It is a text-designed synthetic
voice, not an imitation of a real speaker. The speech-only pronunciation
dictionary renders `MAE` as "May" and `911` as "nine one one."

JACK uses a fixed, fully synthetic older male southern West Virginia/Appalachian
character at an unhurried 0.92× cadence. A single generated synthetic reference
is reused for every response so the speaker identity remains stable. It is not
an imitation of a real person.

## JACK conversational voice mode

The JACK technical-assistant page uses the same private conversational loop as
MAE. It listens for a question, transcribes it locally, submits it through
JACK's existing read-only Mindshare documentation workflow, speaks the answer,
and resumes listening.

JACK uses the approved Qwen3-TTS synthetic Southern male voice. Individual
JACK answers also include a **Listen** button.

JACK cancels a stale speech request and stops prior playback before starting a
new answer, so repeated interactions cannot overlap or restart an answer.

JACK uses a compact, product-focused retrieval context and concise response
budget so the CPU-based local model can answer promptly. The browser allows a
longer safety window than the normal response time and shows continued progress
instead of abandoning an answer that is still being generated.

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

## Live STT resource placement

JACK's live microphone transcription runs Faster-Whisper on the server CPU
with INT8 computation and eight CPU threads. This intentional split leaves the
RTX 3090 available for the 27B conversational model and Qwen JACK voice, which
otherwise leave too little CUDA working memory for reliable follow-up
transcription. Short conversational recordings remain local and are not
stored. Parakeet remains a separately evaluated canary rather than the live
default.

## Parakeet STT evaluation plan

Parakeet TDT 0.6B v3 is now available as a private canary for complete-audio,
English transcription. Whisper Large-v3-Turbo remains the live MAE/JACK
transcription default until a supervisor microphone A/B evaluation is complete.

The evaluation compares at least 15 short local recordings containing normal
questions, unit numbers, locations, CAD terms, pauses, and ordinary background
noise. Review accuracy, silence behavior, turnaround time, and any missed
dispatch terminology before changing the default. Do not use this offline TDT
model as a future continuous radio-streaming engine.

## Radio boundary

The radio ingestion service will be separate from the voice API. Before it is
enabled, document each multicast address, UDP port, codec, channel name,
retention rule, and access role. Capture must remain listen-only.
