"""Private, fixed synthetic JACK voice using a generated reference prompt."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess
from threading import Lock

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
API_MODEL_ID = "lcdash-qwen3-tts-jack"
VOICE_ID = "jack-synthetic-southern-male"
REFERENCE_AUDIO = Path("/voices/jack_synthetic_southern_reference.wav")
REFERENCE_TEXT = (
    "Good evening. I am JACK, your steady technical assistant. "
    "I will speak clearly, carefully, and at an unhurried pace."
)

app = FastAPI(title="LCDash Fixed JACK Voice", docs_url=None, redoc_url=None)
_model = None
_voice_clone_prompt = None
_model_device = "unloaded"
_model_lock = Lock()


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=4000)
    model: str = API_MODEL_ID
    voice: str = VOICE_ID
    response_format: str = "wav"
    speed: float = Field(default=0.92, ge=0.7, le=1.3)


def _load_model_and_prompt():
    global _model, _voice_clone_prompt, _model_device
    with _model_lock:
        if not REFERENCE_AUDIO.is_file():
            raise HTTPException(status_code=503, detail="JACK reference voice is not ready.")
        if _model is None:
            import torch
            from qwen_tts import Qwen3TTSModel

            try:
                _model = Qwen3TTSModel.from_pretrained(
                    MODEL_ID,
                    device_map="cuda:0",
                    dtype=torch.bfloat16,
                    attn_implementation="sdpa",
                )
                _model_device = "cuda"
            except RuntimeError as exc:
                if "out of memory" not in str(exc).lower():
                    raise
                # The 27B conversational model may temporarily consume nearly
                # all of the shared RTX 3090. Preserve JACK availability by
                # using the same local voice model on CPU instead of returning
                # a 500 response to the supervisor.
                torch.cuda.empty_cache()
                _model = Qwen3TTSModel.from_pretrained(
                    MODEL_ID,
                    device_map="cpu",
                    dtype=torch.float32,
                    attn_implementation="sdpa",
                )
                _model_device = "cpu"
        if _voice_clone_prompt is None:
            _voice_clone_prompt = _model.create_voice_clone_prompt(
                ref_audio=str(REFERENCE_AUDIO),
                ref_text=REFERENCE_TEXT,
            )
    return _model, _voice_clone_prompt


def _apply_speed(wav_bytes: bytes, speed: float) -> bytes:
    if abs(speed - 1.0) < 0.001:
        return wav_bytes
    converted = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", "pipe:0", "-filter:a",
            f"atempo={speed:.2f}", "-f", "wav", "pipe:1",
        ],
        input=wav_bytes,
        capture_output=True,
        check=False,
    )
    if converted.returncode != 0:
        raise HTTPException(status_code=503, detail="Speech cadence conversion failed.")
    return converted.stdout


@app.get("/health")
def health() -> dict:
    return {
        "service": "lcdash-qwen3-tts-jack",
        "ready": True,
        "model": MODEL_ID,
        "model_loaded": _model is not None,
        "reference_ready": REFERENCE_AUDIO.is_file(),
        "active_device": _model_device,
        "voice_mode": "fixed-synthetic-reference-only",
        "voice_cloning_enabled": False,
    }


@app.get("/v1/models")
def list_models() -> dict:
    return {"object": "list", "data": [{"id": API_MODEL_ID, "object": "model"}]}


@app.post("/v1/audio/speech")
def synthesize(request: SpeechRequest) -> Response:
    if request.model != API_MODEL_ID or request.voice != VOICE_ID:
        raise HTTPException(status_code=400, detail="Unsupported JACK voice request.")
    if request.response_format not in {"wav", "mp3"}:
        raise HTTPException(status_code=400, detail="Unsupported audio format.")

    model, voice_clone_prompt = _load_model_and_prompt()
    wavs, sample_rate = model.generate_voice_clone(
        text=request.input,
        language="English",
        voice_clone_prompt=voice_clone_prompt,
    )
    output = BytesIO()
    sf.write(output, wavs[0], sample_rate, format="WAV")
    wav_bytes = _apply_speed(output.getvalue(), request.speed)
    if request.response_format == "wav":
        return Response(content=wav_bytes, media_type="audio/wav")

    converted = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "mp3", "pipe:1"],
        input=wav_bytes,
        capture_output=True,
        check=False,
    )
    if converted.returncode != 0:
        raise HTTPException(status_code=503, detail="MP3 conversion failed.")
    return Response(content=converted.stdout, media_type="audio/mpeg")
