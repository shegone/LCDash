"""Private, synthetic-voice Qwen3-TTS canary for LCDash voice testing."""

from __future__ import annotations

from io import BytesIO
import re
import subprocess
from threading import Lock

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
API_MODEL_ID = "lcdash-qwen3-tts-mae"
MAE_VOICE_INSTRUCTION = (
    "A warm, confident adult American female emergency communications "
    "assistant. Calm and reassuring, with clear diction and natural "
    "empathetic expression. Professional and composed, never theatrical."
)
JACK_VOICE_INSTRUCTION = (
    "A mature American male technical assistant with a gentle Appalachian "
    "Southern character. Older, calm, slow, and steady with warm, clear "
    "diction. Reassuring and thoughtful, never exaggerated, theatrical, or "
    "an imitation of a real person."
)
VOICE_PROFILES = {
    "mae-synthetic-female": MAE_VOICE_INSTRUCTION,
    "jack-synthetic-southern-male": JACK_VOICE_INSTRUCTION,
}

app = FastAPI(title="LCDash Qwen3-TTS Canary", docs_url=None, redoc_url=None)
_model = None
_model_lock = Lock()


class SpeechRequest(BaseModel):
    """Subset of the OpenAI speech request used by LCDash."""

    input: str = Field(min_length=1, max_length=4000)
    model: str = API_MODEL_ID
    voice: str = "mae-synthetic-female"
    response_format: str = "wav"
    speed: float = Field(default=1.0, ge=0.7, le=1.3)


def _prepare_text_for_speech(text: str) -> str:
    """Apply voice-only pronunciations without changing displayed text."""
    prepared = str(text or "")
    prepared = re.sub(r"\bMAE\b", "May", prepared, flags=re.IGNORECASE)
    prepared = re.sub(
        r"\bNGA[\s-]*9[\s-]*1[\s-]*1\b",
        "N G A nine one one",
        prepared,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\b9[\s-]*1[\s-]*1\b", "nine one one", prepared)


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            import torch
            from qwen_tts import Qwen3TTSModel

            _model = Qwen3TTSModel.from_pretrained(
                MODEL_ID,
                device_map="cuda:0",
                dtype=torch.bfloat16,
                attn_implementation="sdpa",
            )
    return _model


@app.get("/health")
def health() -> dict:
    return {
        "service": "lcdash-qwen3-tts-canary",
        "ready": True,
        "model": MODEL_ID,
        "model_loaded": _model is not None,
        "voice_mode": "synthetic-voice-design-only",
        "voice_cloning_enabled": False,
    }


@app.get("/v1/models")
def list_models() -> dict:
    return {"object": "list", "data": [{"id": API_MODEL_ID, "object": "model"}]}


@app.post("/v1/audio/speech")
def synthesize(request: SpeechRequest) -> Response:
    if request.model != API_MODEL_ID:
        raise HTTPException(status_code=400, detail="Unsupported canary model.")
    if request.voice not in VOICE_PROFILES:
        raise HTTPException(
            status_code=400,
            detail="This service permits only approved synthetic voice profiles.",
        )
    if request.response_format not in {"wav", "mp3"}:
        raise HTTPException(
            status_code=400,
            detail="Unsupported audio format.",
        )

    model = _load_model()
    wavs, sample_rate = model.generate_voice_design(
        text=_prepare_text_for_speech(request.input),
        language="English",
        instruct=VOICE_PROFILES[request.voice],
    )
    output = BytesIO()
    sf.write(output, wavs[0], sample_rate, format="WAV")
    wav_bytes = output.getvalue()
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
