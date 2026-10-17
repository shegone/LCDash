#!/usr/bin/env python3
"""Local transcription worker for closed radio audio segments."""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import sys
import time
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")
STOP = False


def stop_handler(signum, frame):
    global STOP
    STOP = True


def private_write(path: Path, contents: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(contents, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main():
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        settings = config.get("transcription", {})
        root = Path(config["output_directory"])
        pending = root / "queue" / "pending"
        processing = root / "queue" / "processing"
        failed = root / "queue" / "failed"
        transcripts = root / "transcripts"
        for directory in (pending, processing, failed, transcripts):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise SystemExit(f"Cannot initialize local transcription: {exc}") from exc

    for stale_job in processing.glob("*.json"):
        retry_path = pending / stale_job.name
        if not retry_path.exists():
            os.replace(stale_job, retry_path)

    model_name = settings.get("model", "large-v3")
    device = settings.get("device", "cuda")
    compute_type = settings.get("compute_type", "int8_float16")
    language = settings.get("language", "en")
    beam_size = int(settings.get("beam_size", 5))
    print(
        f"Loading local ASR model={model_name} device={device} "
        f"compute_type={compute_type} language={language}",
        flush=True,
    )
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    print("Transcription worker ready; watching the local pending queue.", flush=True)
    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    while not STOP:
        jobs = sorted(pending.glob("*.json"))
        if not jobs:
            time.sleep(1)
            continue
        for job_path in jobs:
            if STOP:
                break
            claimed = processing / job_path.name
            job = None
            try:
                os.replace(job_path, claimed)
                job = json.loads(claimed.read_text(encoding="utf-8"))
                audio_path = Path(job["audio_file"])
                expected_root = (root / "audio").resolve()
                if expected_root not in audio_path.resolve().parents:
                    raise ValueError("Audio file is outside the configured audio directory.")
                transcript_path = transcripts / f"{job['segment_id']}.json"
                if transcript_path.exists():
                    claimed.unlink(missing_ok=True)
                    continue
                segments, info = model.transcribe(
                    str(audio_path),
                    language=language,
                    beam_size=beam_size,
                    condition_on_previous_text=False,
                    vad_filter=False,
                )
                words = []
                text_parts = []
                for segment in segments:
                    text_parts.append(segment.text)
                    words.append({
                        "start": segment.start,
                        "end": segment.end,
                        "text": segment.text,
                        "avg_logprob": segment.avg_logprob,
                        "no_speech_prob": segment.no_speech_prob,
                    })
                result = {
                    **job,
                    "transcript": "".join(text_parts).strip(),
                    "language": info.language,
                    "language_probability": info.language_probability,
                    "model": model_name,
                    "transcribed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "segments": words,
                }
                private_write(transcript_path, json.dumps(result, indent=2))
                claimed.unlink(missing_ok=True)
                print(
                    f"Transcribed {job['segment_id']} "
                    f"({len(result['transcript'])} characters).",
                    flush=True,
                )
            except Exception as exc:
                failure = failed / f"{job_path.stem}.json"
                private_write(failure, json.dumps({
                    "job": job_path.name,
                    "metadata": job,
                    "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "error": str(exc),
                }, indent=2))
                claimed.unlink(missing_ok=True)
                print(f"Transcription failed for {job_path.stem}: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
