from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import librosa
import torch
from transformers import AutoModelForTDT, AutoProcessor


MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    args = parser.parse_args()

    if not args.audio.is_file():
        raise SystemExit(f"Audio file not found: {args.audio}")
    if not torch.cuda.is_available():
        raise SystemExit("Parakeet benchmark requires an NVIDIA GPU.")

    load_started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForTDT.from_pretrained(
        MODEL_ID,
        dtype=torch.float16,
        device_map="cuda",
    )
    load_seconds = time.perf_counter() - load_started

    sampling_rate = processor.feature_extractor.sampling_rate
    audio, _ = librosa.load(
        args.audio,
        sr=sampling_rate,
        mono=True,
    )
    audio_seconds = len(audio) / sampling_rate

    inputs = processor(
        audio,
        sampling_rate=sampling_rate,
        return_tensors="pt",
    )
    inputs.to(device=model.device, dtype=model.dtype)

    torch.cuda.synchronize()
    transcribe_started = time.perf_counter()
    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=2048,
            return_dict_in_generate=True,
        )
    torch.cuda.synchronize()
    transcribe_seconds = time.perf_counter() - transcribe_started

    transcription = processor.decode(
        output.sequences,
        skip_special_tokens=True,
    )
    if isinstance(transcription, list):
        transcription = transcription[0]

    print(
        json.dumps(
            {
                "model": MODEL_ID,
                "audio_seconds": round(audio_seconds, 3),
                "load_seconds": round(load_seconds, 3),
                "transcribe_seconds": round(transcribe_seconds, 3),
                "realtime_factor": round(
                    transcribe_seconds / max(audio_seconds, 0.001),
                    4,
                ),
                "transcript": str(transcription).strip(),
                "gpu": torch.cuda.get_device_name(0),
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
