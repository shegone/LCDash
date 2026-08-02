"""Private OpenAI-compatible Parakeet TDT v3 transcription canary."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

import librosa
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from transformers import AutoModelForTDT, AutoProcessor


MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"
_model = None
_processor = None
_model_lock = Lock()

app = FastAPI(title="LCDash Parakeet STT Canary", docs_url=None, redoc_url=None)


def _load_model():
    global _model, _processor
    with _model_lock:
        if _model is None or _processor is None:
            if not torch.cuda.is_available():
                raise RuntimeError("Parakeet STT requires an NVIDIA GPU.")
            _processor = AutoProcessor.from_pretrained(MODEL_ID)
            _model = AutoModelForTDT.from_pretrained(
                MODEL_ID,
                dtype=torch.float16,
                device_map="cuda",
            )
    return _model, _processor


@app.get("/health")
def health() -> dict:
    return {
        "service": "lcdash-parakeet-stt-canary",
        "model": MODEL_ID,
        "ready": True,
        "model_loaded": _model is not None,
        "mode": "complete-audio transcription only",
    }


@app.get("/v1/models")
def list_models() -> dict:
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model"}]}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(MODEL_ID),
    language: str = Form("en"),
    response_format: str = Form("json"),
) -> dict:
    if model != MODEL_ID:
        raise HTTPException(status_code=400, detail="Unsupported Parakeet model.")
    if response_format != "json":
        raise HTTPException(status_code=400, detail="Only JSON responses are supported.")
    if language.lower() not in {"en", "english", "auto"}:
        raise HTTPException(status_code=400, detail="The canary is limited to English evaluation.")

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="No audio was received.")

    suffix = Path(file.filename or "recording.webm").suffix or ".webm"
    with NamedTemporaryFile(suffix=suffix) as temporary:
        temporary.write(audio_bytes)
        temporary.flush()
        model_instance, processor = _load_model()
        sample_rate = processor.feature_extractor.sampling_rate
        audio, _ = librosa.load(temporary.name, sr=sample_rate, mono=True)

    inputs = processor(audio, sampling_rate=sample_rate, return_tensors="pt")
    inputs.to(device=model_instance.device, dtype=model_instance.dtype)
    with torch.inference_mode():
        output = model_instance.generate(
            **inputs,
            max_new_tokens=2048,
            return_dict_in_generate=True,
        )
    transcription = processor.decode(output.sequences, skip_special_tokens=True)
    if isinstance(transcription, list):
        transcription = transcription[0]
    return {"text": str(transcription or "").strip()}
