# Chatterbox Synthetic Voice Canary

This canary is a private test service for evaluating Chatterbox quality before
any MAE or JACK default voice changes.

## Boundary

- It runs only on the internal Docker network.
- It has no host port, no CentralSquare credentials, and no CAD access.
- It accepts only the `lcdash-chatterbox-canary` model name and `synthetic`
  voice identifier.
- It does not accept, store, or use reference audio. Voice cloning is disabled.
- Kokoro and the existing Speaches transcription service remain the live path.

## Starting the canary

From `/srv/lcdash-platform/current`, start it only when testing:

```sh
docker compose -f deploy/compose.yaml --profile voice-chatterbox up -d --build voice-chatterbox
```

Stop it after an audition to release GPU memory:

```sh
docker compose -f deploy/compose.yaml --profile voice-chatterbox stop voice-chatterbox
```

## Acceptance checks

1. `/health` responds and reports `synthetic-only`.
2. A short English MAE/JACK sample produces a WAV response.
3. The existing `/api/voice/status`, speech, and transcription paths remain
   healthy while the canary is stopped and after it is tested.
4. No assistant default is changed until an authorized reviewer selects it.

## Qwen3-TTS female-voice canary

Qwen3-TTS has its own isolated Python runtime and model cache. It uses the
official VoiceDesign model with a fixed synthetic MAE voice description; it
does not use a reference recording or voice-cloning path. Start it only when
Chatterbox is stopped:

```sh
docker compose -f deploy/compose.yaml --profile voice-qwen3-tts up -d --build voice-qwen3-tts
```

Stop it after its audition to release GPU memory:

```sh
docker compose -f deploy/compose.yaml --profile voice-qwen3-tts stop voice-qwen3-tts
```
