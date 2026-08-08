"""Private, synthetic-voice Chatterbox canary for LCDash voice testing."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from threading import Lock

import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field


app = FastAPI(title="LCDash Chatterbox Canary", docs_url=None, redoc_url=None)
_model = None
_model_lock = Lock()
MAE_FEMALE_REFERENCE = Path("/voices/mae_synthetic_female.wav")
ALLOWED_VOICES = {"synthetic", "mae-synthetic-female"}


class SpeechRequest(BaseModel):
    """Subset of the OpenAI speech request used by LCDash."""

    input: str = Field(min_length=1, max_length=4000)
    model: str = "lcdash-chatterbox-canary"
    voice: str = "mae-synthetic-female"
    response_format: str = "wav"
    speed: float = Field(default=1.0, ge=0.7, le=1.3)


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            from chatterbox.tts import ChatterboxTTS

            _model = ChatterboxTTS.from_pretrained(device="cuda")
    return _model


@app.get("/health")
def health() -> dict:
    return {
        "service": "lcdash-chatterbox-canary",
        "ready": True,
        "model_loaded": _model is not None,
        "voice_mode": "synthetic-only",
        "voice_cloning_enabled": False,
    }


@app.post("/v1/audio/speech")
def synthesize(request: SpeechRequest) -> Response:
    if request.model != "lcdash-chatterbox-canary":
        raise HTTPException(status_code=400, detail="Unsupported canary model.")
    if request.voice not in ALLOWED_VOICES:
        raise HTTPException(
            status_code=400,
            detail="This canary permits only approved synthetic voices.",
        )
    if request.response_format != "wav":
        raise HTTPException(
            status_code=400,
            detail="The Chatterbox canary currently returns WAV only.",
        )

    if request.voice == "mae-synthetic-female" and not MAE_FEMALE_REFERENCE.is_file():
        raise HTTPException(
            status_code=503,
            detail="The approved synthetic MAE reference has not been installed.",
        )

    model = _load_model()
    generation_options = {}
    if request.voice == "mae-synthetic-female":
        generation_options["audio_prompt_path"] = str(MAE_FEMALE_REFERENCE)
    waveform = model.generate(request.input, **generation_options)
    output = BytesIO()
    sf.write(output, waveform.squeeze().cpu().numpy(), model.sr, format="WAV")
    return Response(content=output.getvalue(), media_type="audio/wav")
