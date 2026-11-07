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
DEFAULT_GLOSSARY_MODE = "off"  # A/B on real traffic (2026-10-17): hints did not help and added hallucinations
GLOSSARY_DIR_NAME = "status"
GLOSSARY_FILE_NAME = "glossary.json"
MAX_VERSION_CHARS = 32
MAX_HOTWORDS_CHARS = 1000
MAX_PROMPT_CHARS = 1300
SHORT_CLIP_SECONDS = 1.5

# Voice-activity filtering and audio conditioning (A/B on 300 real clips,
# 2026-11-07, docs/MINDSHARE_RADIO_TRANSCRIPTION_TUNING.md). The Silero VAD
# that ships with faster-whisper drops the squelch and key-up noise that
# the model otherwise turns into stock phrases ("Thanks for watching!"):
# 16.6 % of clips came out as such boilerplate without it, 2 % with it.
# Padding each clip with silence (and normalising its gain) keeps the VAD
# from cutting real speech at the edges of short transmissions. Every
# setting defaults to today's behaviour; the shipped config turns them on.
SAMPLE_RATE = 8000
MODEL_SAMPLE_RATE = 16000
DEFAULT_VAD_THRESHOLD = 0.5  # Silero speech probability; faster-whisper's default
DEFAULT_VAD_MIN_SPEECH_MS = 250
DEFAULT_VAD_MIN_SILENCE_MS = 300
DEFAULT_VAD_SPEECH_PAD_MS = 200
MAX_PAD_SECONDS = 2.0
GAIN_TARGET_PEAK = 0.9
BANDPASS_LOW_HZ = 300.0
BANDPASS_HIGH_HZ = 3400.0
BANDPASS_TAPS = 255


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


def conditioning_settings(settings):
    """Read the VAD and conditioning keys of the transcription config.

    Returns a dict with ``vad_filter`` (bool), ``vad_parameters`` (dict or
    None), ``pad_seconds`` (float, 0 to MAX_PAD_SECONDS), ``normalize_gain``
    and ``bandpass`` (bools). Anything missing or malformed means today's
    behaviour for that key, never a failure.
    """
    settings = settings if isinstance(settings, dict) else {}

    def flag(key):
        return settings.get(key) is True

    def number(key, default, low, high):
        value = settings.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default
        return float(min(max(value, low), high))

    vad_filter = flag("vad_filter")
    vad_parameters = None
    if vad_filter:
        vad_parameters = {
            "threshold": round(number("vad_threshold", DEFAULT_VAD_THRESHOLD, 0.05, 0.95), 3),
            "min_speech_duration_ms": int(number("vad_min_speech_ms",
                                                 DEFAULT_VAD_MIN_SPEECH_MS, 0, 5000)),
            "min_silence_duration_ms": int(number("vad_min_silence_ms",
                                                  DEFAULT_VAD_MIN_SILENCE_MS, 0, 5000)),
            "speech_pad_ms": int(number("vad_speech_pad_ms",
                                        DEFAULT_VAD_SPEECH_PAD_MS, 0, 2000)),
        }
    return {
        "vad_filter": vad_filter,
        "vad_parameters": vad_parameters,
        "pad_seconds": number("pad_seconds", 0.0, 0.0, MAX_PAD_SECONDS),
        "normalize_gain": flag("normalize_gain"),
        "bandpass": flag("bandpass"),
    }


def conditioning_active(conditioning):
    return bool(conditioning["pad_seconds"] or conditioning["normalize_gain"]
                or conditioning["bandpass"])


def read_pcm16(audio_path):
    """A mono PCM16 WAV as float32 samples in [-1, 1] at SAMPLE_RATE."""
    import numpy as np

    with wave.open(str(audio_path), "rb") as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2:
            raise ValueError("Audio must be mono PCM16.")
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if rate != SAMPLE_RATE and len(samples) > 1:
        target = np.linspace(0, len(samples) - 1, int(len(samples) * SAMPLE_RATE / rate))
        samples = np.interp(target, np.arange(len(samples)), samples).astype(np.float32)
    return samples


def condition_audio(samples, conditioning):
    """Band-pass, normalise and pad 8 kHz samples; returns 16 kHz float32 for
    the model (faster-whisper takes arrays at 16 kHz)."""
    import numpy as np

    x = samples.astype(np.float32)
    if conditioning["bandpass"] and len(x):
        n = np.arange(BANDPASS_TAPS) - (BANDPASS_TAPS - 1) / 2
        window = np.hamming(BANDPASS_TAPS)
        # Each windowed sinc is scaled by its own 2 fc / fs so it has unit
        # gain at DC; their difference passes 300 to 3400 Hz at unit gain.
        high_frac = 2 * BANDPASS_HIGH_HZ / SAMPLE_RATE
        low_frac = 2 * BANDPASS_LOW_HZ / SAMPLE_RATE
        low_pass = high_frac * np.sinc(high_frac * n) * window
        high_cut = low_frac * np.sinc(low_frac * n) * window
        kernel = (low_pass - high_cut).astype(np.float32)
        x = np.convolve(x, kernel, mode="same").astype(np.float32)
    if conditioning["normalize_gain"] and len(x):
        peak = float(np.max(np.abs(x)))
        if peak > 1e-4:
            x = (x * (GAIN_TARGET_PEAK / peak)).astype(np.float32)
    if conditioning["pad_seconds"]:
        silence = np.zeros(int(conditioning["pad_seconds"] * SAMPLE_RATE), dtype=np.float32)
        x = np.concatenate([silence, x, silence])
    if len(x) > 1:
        target = np.linspace(0, len(x) - 1, len(x) * (MODEL_SAMPLE_RATE // SAMPLE_RATE))
        x = np.interp(target, np.arange(len(x)), x).astype(np.float32)
    return x


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

    def __init__(self, model, language, beam_size, glossary_mode, glossary_cache,
                 conditioning=None):
        self.model = model
        self.language = language
        self.beam_size = beam_size
        self.mode = glossary_mode
        self.cache = glossary_cache
        self.hints_disabled = False
        self.conditioning = conditioning or conditioning_settings({})

    def _audio(self, audio_path):
        """The path itself (today's behaviour) or the conditioned samples."""
        if not conditioning_active(self.conditioning):
            return str(audio_path)
        return condition_audio(read_pcm16(audio_path), self.conditioning)

    def _call(self, audio_path, extra):
        kwargs = {
            "language": self.language,
            "beam_size": self.beam_size,
            "condition_on_previous_text": False,
            "vad_filter": self.conditioning["vad_filter"],
        }
        if self.conditioning["vad_parameters"]:
            kwargs["vad_parameters"] = dict(self.conditioning["vad_parameters"])
        return self.model.transcribe(self._audio(audio_path), **kwargs, **extra)

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
        # Segment times come back relative to the padded audio; report them
        # against the clip itself.
        offset = self.conditioning["pad_seconds"]
        for segment in segments:
            text_parts.append(segment.text)
            words.append({
                "start": max(0.0, segment.start - offset) if offset else segment.start,
                "end": max(0.0, segment.end - offset) if offset else segment.end,
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
        duration_info = None if self.conditioning["pad_seconds"] else info
        if extra and is_hint_suspect(
            clip_duration_seconds(audio_path, duration_info), text,
            glossary_word_set(glossary["hotwords"])
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
    conditioning = conditioning_settings(settings)
    print(
        f"Loading local ASR model={model_name} device={device} "
        f"compute_type={compute_type} language={language}",
        flush=True,
    )
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    transcriber = ClipTranscriber(
        model, language, beam_size, glossary_mode,
        GlossaryCache(root / GLOSSARY_DIR_NAME / GLOSSARY_FILE_NAME),
        conditioning,
    )
    print(f"Transcription worker ready; glossary_mode={glossary_mode}; "
          f"vad_filter={conditioning['vad_filter']} pad_seconds={conditioning['pad_seconds']} "
          f"normalize_gain={conditioning['normalize_gain']} bandpass={conditioning['bandpass']}; "
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
