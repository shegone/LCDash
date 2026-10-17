#!/usr/bin/env python3
"""Local transcription worker for closed radio audio segments."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import signal
import sys
import time
import wave
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")
STOP = False

# Transcriber hints (docs/MINDSHARE_RADIO_HEALTH.md). The uploader writes
# <root>/status/glossary.json; this worker only reads it. A missing or corrupt
# file means "no hints", never a failure.
GLOSSARY_MODES = ("hotwords", "prompt", "off")
DEFAULT_GLOSSARY_MODE = "hotwords"
GLOSSARY_DIR_NAME = "status"
GLOSSARY_FILE_NAME = "glossary.json"
MAX_VERSION_CHARS = 32
MAX_HOTWORDS_CHARS = 1000
MAX_PROMPT_CHARS = 1300
SHORT_CLIP_SECONDS = 1.5


def stop_handler(signum, frame):
    global STOP
    STOP = True


def private_write(path: Path, contents: str):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(contents, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def normalize_glossary_mode(value):
    """Return (mode, valid). Missing means the default; an unknown value falls back to it."""
    if value is None:
        return DEFAULT_GLOSSARY_MODE, True
    if isinstance(value, str) and value.strip().lower() in GLOSSARY_MODES:
        return value.strip().lower(), True
    return DEFAULT_GLOSSARY_MODE, False


def _clean_text(value, limit):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()[:limit]


def load_glossary(path):
    """Read a glossary file. Returns {"version","hotwords","prompt"} or None (never raises)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    version, hotwords = data.get("version"), data.get("hotwords")
    prompt = data.get("prompt", "")
    if not isinstance(version, str) or not version.strip() or len(version) > MAX_VERSION_CHARS:
        return None
    if not isinstance(hotwords, str) or not isinstance(prompt, str):
        return None
    return {
        "version": version,
        "hotwords": _clean_text(hotwords, MAX_HOTWORDS_CHARS),
        "prompt": _clean_text(prompt, MAX_PROMPT_CHARS),
    }


class GlossaryCache:
    """Re-reads the glossary file only when its mtime (or size) changes."""

    def __init__(self, path):
        self.path = Path(path)
        self._stamp = None
        self._glossary = None

    def get(self):
        try:
            stat = self.path.stat()
        except OSError:
            self._stamp = None
            self._glossary = None
            return None
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp != self._stamp:
            self._stamp = stamp
            self._glossary = load_glossary(self.path)
        return self._glossary


def glossary_kwargs(glossary, mode):
    """Keyword arguments to add to model.transcribe for this glossary and mode."""
    if not glossary or mode == "off":
        return {}
    if mode == "prompt":
        return {"initial_prompt": glossary["prompt"]} if glossary["prompt"] else {}
    return {"hotwords": glossary["hotwords"]} if glossary["hotwords"] else {}


def words_of(text):
    """Lower-case word tokens of a string (letters, digits, apostrophes)."""
    return re.findall(r"[a-z0-9']+", str(text).lower())


def glossary_word_set(hotwords):
    return {word.strip("'") for word in words_of(hotwords)} - {""}


def is_hint_suspect(duration_seconds, text, glossary_words):
    """True for a clip under 1.5 s whose every word is a glossary word (likely parroting)."""
    if not glossary_words or duration_seconds is None or duration_seconds >= SHORT_CLIP_SECONDS:
        return False
    words = [word.strip("'") for word in words_of(text)]
    words = [word for word in words if word]
    return bool(words) and all(word in glossary_words for word in words)


def clip_duration_seconds(audio_path, info):
    """Clip length from the model's info, else from the WAV header; None if unknown."""
    duration = getattr(info, "duration", None)
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration >= 0:
        return float(duration)
    try:
        with wave.open(str(audio_path), "rb") as handle:
            return handle.getnframes() / float(handle.getframerate())
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        return None


class ClipTranscriber:
    """Runs one clip through the model, adding the glossary hint when configured."""

    def __init__(self, model, language, beam_size, glossary_mode, glossary_cache):
        self.model = model
        self.language = language
        self.beam_size = beam_size
        self.mode = glossary_mode
        self.cache = glossary_cache
        self.hints_disabled = False

    def _call(self, audio_path, extra):
        return self.model.transcribe(
            str(audio_path),
            language=self.language,
            beam_size=self.beam_size,
            condition_on_previous_text=False,
            vad_filter=False,
            **extra,
        )

    def transcribe(self, audio_path):
        glossary = None
        if self.mode != "off" and not self.hints_disabled and self.cache is not None:
            glossary = self.cache.get()
        extra = glossary_kwargs(glossary, self.mode)
        try:
            segments, info = self._call(audio_path, extra)
        except TypeError:
            if not extra:
                raise
            # An older model library that does not take the hint argument: carry on without hints.
            self.hints_disabled = True
            glossary, extra = None, {}
            print("Model does not accept transcriber hints; continuing without them.",
                  file=sys.stderr, flush=True)
            segments, info = self._call(audio_path, extra)
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
        text = "".join(text_parts).strip()
        clip = {
            "transcript": text,
            "language": info.language,
            "language_probability": info.language_probability,
            "segments": words,
            "glossary_version": glossary["version"] if glossary else None,
        }
        if extra and is_hint_suspect(
            clip_duration_seconds(audio_path, info), text, glossary_word_set(glossary["hotwords"])
        ):
            clip["hint_suspect"] = True
        return clip


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
    glossary_mode, glossary_mode_valid = normalize_glossary_mode(settings.get("glossary_mode"))
    if not glossary_mode_valid:
        print(f"Unknown glossary_mode; using {glossary_mode}.", file=sys.stderr, flush=True)
    print(
        f"Loading local ASR model={model_name} device={device} "
        f"compute_type={compute_type} language={language}",
        flush=True,
    )
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    transcriber = ClipTranscriber(
        model, language, beam_size, glossary_mode,
        GlossaryCache(root / GLOSSARY_DIR_NAME / GLOSSARY_FILE_NAME),
    )
    print(f"Transcription worker ready; glossary_mode={glossary_mode}; "
          "watching the local pending queue.", flush=True)
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
                clip = transcriber.transcribe(audio_path)
                result = {
                    **job,
                    "transcript": clip["transcript"],
                    "language": clip["language"],
                    "language_probability": clip["language_probability"],
                    "model": model_name,
                    "transcribed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "segments": clip["segments"],
                    "glossary_version": clip["glossary_version"],
                }
                if clip.get("hint_suspect"):
                    result["hint_suspect"] = True
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
