# Local AI Model Benchmark - 2026-07-29

## Test system

- Ubuntu Server 24.04
- NVIDIA GeForce RTX 3090 with 24 GB VRAM
- NVIDIA driver 595.71.05
- Docker with NVIDIA Container Toolkit

## Language models

The prompt requested one concise, non-invented dispatcher action for a
synthetic chest-pain call. Qwen 3.5 was tested with reasoning disabled, which
is the mode LCDash uses for interactive responses.

| Model | Cold wall time | Generation rate | Result |
| --- | ---: | ---: | --- |
| Qwen3 8B | 5.98 s | 134.15 tokens/s | Fast compatibility fallback |
| Qwen3.5 9B | 3.29 s | 116.12 tokens/s | Fast general chat |
| Qwen3.5 27B | 5.58 s | 43.16 tokens/s | Best tested response restraint |

Qwen3.5 27B occupied approximately 16 GB of GPU memory at a 4,096-token
context, leaving approximately 6.6 GB free. It is the selected MAE default.
Qwen3.5 9B remains available for faster general chat.

## Speech recognition

The synthetic 8.619-second dispatch sentence was:

> Medic 104, respond to 125 Main Street for a reported chest pain. Dispatch
> time is 1432.

| Model | Cold request | Warm request | Transcript |
| --- | ---: | ---: | --- |
| Faster Distil-Whisper Small English | 0.698 s | 0.142 s | Correct |
| Faster-Whisper Large-v3 Turbo | 69.260 s | 0.217 s | Correct, improved punctuation |
| NVIDIA Parakeet-TDT 0.6B V3 | 2.531 s model load + 0.744 s transcription | Not run as a resident service | Correct |

The 69-second Whisper Large-v3 Turbo result includes its first CUDA model
initialization. Warm requests completed in 0.217 seconds.

## Current selection

- MAE default: `qwen3.5:27b`
- General fast model: `qwen3.5:9b`
- Compatibility fallback: `qwen3:8b`
- Live STT default: `Systran/faster-distil-whisper-small.en`
- Installed STT comparison: `deepdml/faster-whisper-large-v3-turbo-ct2`
- On-demand NVIDIA comparison: `nvidia/parakeet-tdt-0.6b-v3`
- TTS: `speaches-ai/Kokoro-82M-v1.0-ONNX`

## Required follow-up

Do not select a final operational STT model from this synthetic test alone.
Compare the three speech models using authorized local samples containing
radio noise, unit identifiers, Logan County street names, overlapping speech,
and different microphones. Keep all evaluation audio local.
