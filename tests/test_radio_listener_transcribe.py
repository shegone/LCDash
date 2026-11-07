"""Tests for the glossary (transcriber hint) logic in radio_listener/transcribe.py.

Synthetic data only. The model is a fake object; faster_whisper is never imported.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
import wave
from pathlib import Path

import pytest

TRANSCRIBE_PATH = Path(__file__).resolve().parents[1] / "radio_listener" / "transcribe.py"
_spec = importlib.util.spec_from_file_location("radio_listener_transcribe", TRANSCRIBE_PATH)
transcribe = importlib.util.module_from_spec(_spec)
sys.modules["radio_listener_transcribe"] = transcribe
_spec.loader.exec_module(transcribe)

HOTWORDS = "Zebulon Quarry, Mt. Hanover, Engine 9"
PROMPT = "Public safety radio traffic. Names and terms that may be spoken: " + HOTWORDS + "."
BASE_KWARGS = {
    "language": "en", "beam_size": 5, "condition_on_previous_text": False, "vad_filter": False,
}


def write_glossary(path: Path, version="abc123def456", hotwords=HOTWORDS, prompt=PROMPT):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": version, "hotwords": hotwords, "prompt": prompt}), encoding="utf-8")


def bump_mtime(path: Path):
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))


class FakeSegment:
    def __init__(self, text, start=0.0, end=1.0):
        self.text, self.start, self.end = text, start, end
        self.avg_logprob, self.no_speech_prob = -0.2, 0.01


class FakeModel:
    """Stands in for faster_whisper.WhisperModel."""

    def __init__(self, text="engine nine respond", duration=4.0, reject_hints=False, on_call=None):
        self.text, self.duration, self.reject_hints = text, duration, reject_hints
        self.on_call = on_call
        self.calls = []

    def transcribe(self, audio, **kwargs):
        if self.reject_hints and ({"hotwords", "initial_prompt"} & set(kwargs)):
            raise TypeError("transcribe() got an unexpected keyword argument")
        self.calls.append((audio, kwargs))
        if self.on_call:
            self.on_call()
        info = types.SimpleNamespace(language="en", language_probability=0.99, duration=self.duration)
        return iter([FakeSegment(self.text)]), info


def make_transcriber(model, mode, glossary_path):
    return transcribe.ClipTranscriber(model, "en", 5, mode, transcribe.GlossaryCache(glossary_path))


# ------------------------------------------------------------------ mode


def test_glossary_mode_normalization():
    assert transcribe.normalize_glossary_mode(None) == ("off", True)
    for mode in ("hotwords", "prompt", "off"):
        assert transcribe.normalize_glossary_mode(mode) == (mode, True)
    assert transcribe.normalize_glossary_mode(" Prompt ") == ("prompt", True)
    assert transcribe.normalize_glossary_mode("shout") == ("off", False)
    assert transcribe.normalize_glossary_mode(7) == ("off", False)


def test_repo_config_selects_hotwords_by_default():
    config = json.loads((TRANSCRIBE_PATH.parent / "config.json").read_text(encoding="utf-8"))
    assert config["transcription"]["glossary_mode"] == "off"
    # The existing keys are untouched.
    assert config["transcription"]["model"] == "large-v3" and config["transcription"]["beam_size"] == 5


# ------------------------------------------------------------------ loading


def test_load_glossary_accepts_the_uploader_format(tmp_path):
    path = tmp_path / "glossary.json"
    write_glossary(path)
    assert transcribe.load_glossary(path) == {
        "version": "abc123def456", "hotwords": HOTWORDS, "prompt": PROMPT,
    }


def test_load_glossary_empty_version_is_valid(tmp_path):
    path = tmp_path / "glossary.json"
    write_glossary(path, version="empty", hotwords="", prompt="")
    assert transcribe.load_glossary(path) == {"version": "empty", "hotwords": "", "prompt": ""}


@pytest.mark.parametrize("contents", [
    "", "not json", "[]", "null", '{"version": 1, "hotwords": "x", "prompt": ""}',
    '{"version": "v", "hotwords": 5, "prompt": ""}', '{"hotwords": "x", "prompt": ""}',
    '{"version": "' + "v" * 33 + '", "hotwords": "x", "prompt": ""}', '{"version": "  ", "hotwords": "x"}',
    '{"version": "v", "hotwords": "x", "prompt": ["a"]}',
])
def test_corrupt_glossary_means_no_hints(tmp_path, contents):
    path = tmp_path / "glossary.json"
    path.write_text(contents, encoding="utf-8")
    assert transcribe.load_glossary(path) is None


def test_load_glossary_missing_file_and_bad_bytes(tmp_path):
    assert transcribe.load_glossary(tmp_path / "nope.json") is None
    path = tmp_path / "glossary.json"
    path.write_bytes(b"\xff\xfe\x00bad")
    assert transcribe.load_glossary(path) is None


def test_load_glossary_strips_control_characters_and_caps_length(tmp_path):
    path = tmp_path / "glossary.json"
    write_glossary(path, hotwords="Alpha\nBravo\x00, " + "x" * 5000, prompt="p\r\nq" + "y" * 5000)
    glossary = transcribe.load_glossary(path)
    assert "\n" not in glossary["hotwords"] and "\x00" not in glossary["hotwords"]
    assert len(glossary["hotwords"]) <= transcribe.MAX_HOTWORDS_CHARS
    assert len(glossary["prompt"]) <= transcribe.MAX_PROMPT_CHARS
    assert glossary["hotwords"].startswith("Alpha Bravo")


def test_cache_rereads_only_when_the_file_changes(tmp_path, monkeypatch):
    path = tmp_path / "status" / "glossary.json"
    cache = transcribe.GlossaryCache(path)
    assert cache.get() is None  # missing
    write_glossary(path, version="v1")
    loads = []
    real_load = transcribe.load_glossary
    monkeypatch.setattr(transcribe, "load_glossary", lambda p: loads.append(p) or real_load(p))
    assert cache.get()["version"] == "v1"
    assert cache.get()["version"] == "v1"
    assert len(loads) == 1  # second call used the cached copy
    write_glossary(path, version="v2", hotwords="Alpha")
    bump_mtime(path)
    assert cache.get()["version"] == "v2"
    assert len(loads) == 2


def test_cache_survives_corruption_and_removal(tmp_path):
    path = tmp_path / "glossary.json"
    cache = transcribe.GlossaryCache(path)
    write_glossary(path, version="v1")
    assert cache.get()["version"] == "v1"
    path.write_text("{corrupt", encoding="utf-8")
    bump_mtime(path)
    assert cache.get() is None  # no hints, no exception
    write_glossary(path, version="v3")
    bump_mtime(path)
    bump_mtime(path)
    assert cache.get()["version"] == "v3"
    path.unlink()
    assert cache.get() is None


# ------------------------------------------------------------------ kwargs and hint_suspect


def test_kwargs_follow_the_mode():
    glossary = {"version": "v", "hotwords": HOTWORDS, "prompt": PROMPT}
    assert transcribe.glossary_kwargs(glossary, "hotwords") == {"hotwords": HOTWORDS}
    assert transcribe.glossary_kwargs(glossary, "prompt") == {"initial_prompt": PROMPT}
    assert transcribe.glossary_kwargs(glossary, "off") == {}
    assert transcribe.glossary_kwargs(None, "hotwords") == {}
    assert transcribe.glossary_kwargs({"version": "empty", "hotwords": "", "prompt": ""}, "hotwords") == {}
    assert transcribe.glossary_kwargs({"version": "empty", "hotwords": "", "prompt": ""}, "prompt") == {}


def test_hint_suspect_rule():
    words = transcribe.glossary_word_set(HOTWORDS)
    assert {"zebulon", "quarry", "mt", "hanover", "engine", "9"} <= words
    assert transcribe.is_hint_suspect(1.0, "Zebulon Quarry.", words)
    assert transcribe.is_hint_suspect(0.2, "engine 9", words)
    assert not transcribe.is_hint_suspect(1.5, "Zebulon Quarry.", words)       # not under 1.5 s
    assert not transcribe.is_hint_suspect(2.0, "Zebulon Quarry.", words)
    assert not transcribe.is_hint_suspect(1.0, "Zebulon Quarry copy", words)    # a non-glossary word
    assert not transcribe.is_hint_suspect(1.0, "", words)                       # nothing said
    assert not transcribe.is_hint_suspect(1.0, "...", words)
    assert not transcribe.is_hint_suspect(1.0, "Zebulon", set())                # no glossary
    assert not transcribe.is_hint_suspect(None, "Zebulon", words)               # unknown length


def test_clip_duration_prefers_the_model_info_then_the_wav_header(tmp_path):
    wav_path = tmp_path / "clip.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 4000)  # 0.5 s
    assert transcribe.clip_duration_seconds(wav_path, types.SimpleNamespace(duration=3.25)) == 3.25
    assert transcribe.clip_duration_seconds(wav_path, types.SimpleNamespace()) == pytest.approx(0.5)
    assert transcribe.clip_duration_seconds(tmp_path / "missing.wav", types.SimpleNamespace()) is None


# ------------------------------------------------------------------ ClipTranscriber with a fake model


def test_hotwords_mode_passes_hotwords_and_records_the_version(tmp_path):
    glossary_path = tmp_path / "status" / "glossary.json"
    write_glossary(glossary_path)
    model = FakeModel()
    clip = make_transcriber(model, "hotwords", glossary_path).transcribe(tmp_path / "a.wav")
    audio, kwargs = model.calls[0]
    assert audio == str(tmp_path / "a.wav")
    assert kwargs == {**BASE_KWARGS, "hotwords": HOTWORDS}
    assert clip["glossary_version"] == "abc123def456"
    assert "hint_suspect" not in clip
    assert clip["transcript"] == "engine nine respond" and clip["language"] == "en"
    assert clip["segments"] == [{"start": 0.0, "end": 1.0, "text": "engine nine respond",
                                 "avg_logprob": -0.2, "no_speech_prob": 0.01}]


def test_prompt_mode_passes_initial_prompt_only(tmp_path):
    glossary_path = tmp_path / "glossary.json"
    write_glossary(glossary_path)
    model = FakeModel()
    clip = make_transcriber(model, "prompt", glossary_path).transcribe(tmp_path / "a.wav")
    assert model.calls[0][1] == {**BASE_KWARGS, "initial_prompt": PROMPT}
    assert clip["glossary_version"] == "abc123def456"


def test_off_mode_passes_no_hints_and_records_no_version(tmp_path):
    glossary_path = tmp_path / "glossary.json"
    write_glossary(glossary_path)
    model = FakeModel()
    clip = make_transcriber(model, "off", glossary_path).transcribe(tmp_path / "a.wav")
    assert model.calls[0][1] == BASE_KWARGS
    assert clip["glossary_version"] is None and "hint_suspect" not in clip


@pytest.mark.parametrize("setup", ["missing", "corrupt"])
def test_missing_or_corrupt_glossary_transcribes_normally(tmp_path, setup):
    glossary_path = tmp_path / "glossary.json"
    if setup == "corrupt":
        glossary_path.write_text("{nope", encoding="utf-8")
    model = FakeModel()
    clip = make_transcriber(model, "hotwords", glossary_path).transcribe(tmp_path / "a.wav")
    assert model.calls[0][1] == BASE_KWARGS
    assert clip["glossary_version"] is None
    assert clip["transcript"] == "engine nine respond"


def test_glossary_changes_apply_to_the_next_job(tmp_path):
    glossary_path = tmp_path / "glossary.json"
    model = FakeModel()
    transcriber = make_transcriber(model, "hotwords", glossary_path)
    transcriber.transcribe(tmp_path / "a.wav")
    write_glossary(glossary_path, version="v-one", hotwords="Alpha, Bravo")
    transcriber.transcribe(tmp_path / "b.wav")
    write_glossary(glossary_path, version="v-two", hotwords="Charlie")
    bump_mtime(glossary_path)
    transcriber.transcribe(tmp_path / "c.wav")
    assert [call[1].get("hotwords") for call in model.calls] == [None, "Alpha, Bravo", "Charlie"]


def test_short_clip_made_only_of_hint_words_is_flagged_but_text_is_kept(tmp_path):
    glossary_path = tmp_path / "glossary.json"
    write_glossary(glossary_path)
    model = FakeModel(text=" Zebulon Quarry.", duration=1.0)
    clip = make_transcriber(model, "hotwords", glossary_path).transcribe(tmp_path / "a.wav")
    assert clip["hint_suspect"] is True
    assert clip["transcript"] == "Zebulon Quarry."  # never deleted


def test_long_clip_or_mixed_words_are_not_flagged(tmp_path):
    glossary_path = tmp_path / "glossary.json"
    write_glossary(glossary_path)
    for text, duration in ((" Zebulon Quarry.", 3.0), (" Zebulon Quarry copy that", 1.0)):
        clip = make_transcriber(FakeModel(text=text, duration=duration), "hotwords", glossary_path).transcribe(
            tmp_path / "a.wav")
        assert "hint_suspect" not in clip


def test_no_hint_suspect_when_hints_were_not_sent(tmp_path):
    glossary_path = tmp_path / "glossary.json"
    write_glossary(glossary_path)
    model = FakeModel(text=" Zebulon Quarry.", duration=1.0)
    clip = make_transcriber(model, "off", glossary_path).transcribe(tmp_path / "a.wav")
    assert "hint_suspect" not in clip


def test_model_that_rejects_hint_arguments_falls_back_without_failing(tmp_path, capsys):
    glossary_path = tmp_path / "glossary.json"
    write_glossary(glossary_path)
    model = FakeModel(reject_hints=True)
    transcriber = make_transcriber(model, "hotwords", glossary_path)
    clip = transcriber.transcribe(tmp_path / "a.wav")
    assert model.calls[0][1] == BASE_KWARGS
    assert clip["glossary_version"] is None
    assert transcriber.hints_disabled is True
    transcriber.transcribe(tmp_path / "b.wav")  # stays disabled, no repeated failures
    assert len(model.calls) == 2 and "hotwords" not in model.calls[1][1]
    assert HOTWORDS not in capsys.readouterr().err


def test_a_type_error_without_hints_is_not_swallowed(tmp_path):
    class Broken(FakeModel):
        def transcribe(self, audio, **kwargs):
            raise TypeError("real bug")

    with pytest.raises(TypeError):
        make_transcriber(Broken(), "off", tmp_path / "glossary.json").transcribe(tmp_path / "a.wav")


# ------------------------------------------------------------------ main() end to end with a fake faster_whisper


def test_main_writes_glossary_fields_into_the_transcript_json(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "audio" / "law").mkdir(parents=True)
    pending = root / "queue" / "pending"
    pending.mkdir(parents=True)
    wav_path = root / "audio" / "law" / "law_rx_20261017T120000_000000Z_ab12cd34.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 4000)  # 0.5 s
    segment_id = wav_path.stem
    job = {"segment_id": segment_id, "channel": "law", "direction": "rx", "audio_file": str(wav_path),
           "pcmu_packet_count": 25}
    (pending / f"{segment_id}.json").write_text(json.dumps(job), encoding="utf-8")
    write_glossary(root / "status" / "glossary.json")

    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "output_directory": str(root),
        "transcription": {"model": "fake-model", "device": "cpu", "compute_type": "int8",
                          "language": "en", "beam_size": 3, "glossary_mode": "prompt"},
    }), encoding="utf-8")

    seen = {}

    class StopAfterFirst(FakeModel):
        def __init__(self, *args, **kwargs):
            super().__init__(text=" Engine 9.", duration=0.5, on_call=lambda: setattr(transcribe, "STOP", True))
            seen["model"] = self

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = StopAfterFirst
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(transcribe, "CONFIG", config)
    monkeypatch.setattr(transcribe, "STOP", False)

    transcribe.main()

    result = json.loads((root / "transcripts" / f"{segment_id}.json").read_text(encoding="utf-8"))
    assert seen["model"].calls[0][1] == {
        "language": "en", "beam_size": 3, "condition_on_previous_text": False,
        "vad_filter": False, "initial_prompt": PROMPT,
    }
    assert result["transcript"] == "Engine 9."
    assert result["glossary_version"] == "abc123def456"
    assert result["hint_suspect"] is True
    assert result["segment_id"] == segment_id and result["model"] == "fake-model"
    assert result["segments"][0]["text"] == " Engine 9."
    assert not (root / "queue" / "processing" / f"{segment_id}.json").exists()
    assert not list((root / "queue" / "failed").glob("*.json"))


def test_main_default_mode_is_hotwords_and_a_clean_job_has_no_hint_suspect(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "audio" / "law").mkdir(parents=True)
    pending = root / "queue" / "pending"
    pending.mkdir(parents=True)
    wav_path = root / "audio" / "law" / "law_rx_20261017T120100_000000Z_ef56ab78.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 16000)
    segment_id = wav_path.stem
    (pending / f"{segment_id}.json").write_text(
        json.dumps({"segment_id": segment_id, "audio_file": str(wav_path)}), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(root), "transcription": {"device": "cpu"}}),
                      encoding="utf-8")  # no glossary_mode, no glossary file
    seen = {}

    class StopAfterFirst(FakeModel):
        def __init__(self, *args, **kwargs):
            super().__init__(on_call=lambda: setattr(transcribe, "STOP", True))
            seen["model"] = self

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = StopAfterFirst
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(transcribe, "CONFIG", config)
    monkeypatch.setattr(transcribe, "STOP", False)
    transcribe.main()
    result = json.loads((root / "transcripts" / f"{segment_id}.json").read_text(encoding="utf-8"))
    assert result["glossary_version"] is None and "hint_suspect" not in result
    assert seen["model"].calls[0][1] == {**BASE_KWARGS, "beam_size": 5}


def run_main_with_job(tmp_path, monkeypatch, job_extra):
    root = tmp_path / "data"
    (root / "audio" / "law").mkdir(parents=True)
    pending = root / "queue" / "pending"
    pending.mkdir(parents=True)
    wav_path = root / "audio" / "law" / "law_rx_20261018T120000_000000Z_cd34ef56.wav"
    with wave.open(str(wav_path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 8000)
    segment_id = wav_path.stem
    job = {"segment_id": segment_id, "audio_file": str(wav_path), **job_extra}
    (pending / f"{segment_id}.json").write_text(json.dumps(job), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"output_directory": str(root), "transcription": {"device": "cpu"}}),
                      encoding="utf-8")

    class StopAfterFirst(FakeModel):
        def __init__(self, *args, **kwargs):
            super().__init__(on_call=lambda: setattr(transcribe, "STOP", True))

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = StopAfterFirst
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(transcribe, "CONFIG", config)
    monkeypatch.setattr(transcribe, "STOP", False)
    transcribe.main()
    return json.loads((root / "transcripts" / f"{segment_id}.json").read_text(encoding="utf-8"))


def test_main_keeps_the_capture_signal_in_the_transcript_json(tmp_path, monkeypatch):
    signal = {"radio_id": "98426", "talkgroup": "G4407"}
    result = run_main_with_job(tmp_path, monkeypatch, {"signal": signal})
    assert result["signal"] == signal
    assert result["transcript"]  # the transcript itself is still produced


def test_main_adds_no_signal_key_when_the_job_has_none(tmp_path, monkeypatch):
    result = run_main_with_job(tmp_path, monkeypatch, {})
    assert "signal" not in result


# ------------------------------------------------------------------ VAD and audio conditioning (2026-11-07)


def write_tone(path: Path, seconds=0.5, hz=1000.0, amplitude=0.1, rate=8000):
    import math
    import struct
    frames = int(seconds * rate)
    samples = (int(amplitude * 32767 * math.sin(2 * math.pi * hz * i / rate)) for i in range(frames))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def test_conditioning_defaults_are_todays_behaviour():
    assert transcribe.conditioning_settings({}) == {
        "vad_filter": False, "vad_parameters": None, "pad_seconds": 0.0,
        "normalize_gain": False, "bandpass": False,
    }
    assert transcribe.conditioning_settings(None) == transcribe.conditioning_settings({})
    assert not transcribe.conditioning_active(transcribe.conditioning_settings({}))


def test_conditioning_reads_the_config_and_clamps_odd_values():
    settings = transcribe.conditioning_settings({
        "vad_filter": True, "vad_min_silence_ms": 450, "vad_speech_pad_ms": 99999,
        "pad_seconds": 0.5, "normalize_gain": True, "bandpass": "yes",
    })
    assert settings == {
        "vad_filter": True,
        "vad_parameters": {"threshold": 0.5, "min_speech_duration_ms": 250,
                           "min_silence_duration_ms": 450, "speech_pad_ms": 2000},
        "pad_seconds": 0.5, "normalize_gain": True, "bandpass": False,
    }
    assert transcribe.conditioning_active(settings)
    # Strings, booleans and out-of-range numbers fall back to the defaults.
    odd = transcribe.conditioning_settings({"vad_filter": "true", "pad_seconds": "0.5",
                                            "vad_min_silence_ms": True})
    assert odd["vad_filter"] is False and odd["pad_seconds"] == 0.0
    assert transcribe.conditioning_settings({"pad_seconds": 9})["pad_seconds"] == transcribe.MAX_PAD_SECONDS
    assert transcribe.conditioning_settings({"pad_seconds": -1})["pad_seconds"] == 0.0
    vad_only = transcribe.conditioning_settings({"vad_filter": True})
    assert vad_only["vad_parameters"] == {"threshold": 0.5, "min_speech_duration_ms": 250,
                                          "min_silence_duration_ms": 300, "speech_pad_ms": 200}
    permissive = transcribe.conditioning_settings({"vad_filter": True, "vad_threshold": 0.3,
                                                   "vad_min_speech_ms": 100})
    assert permissive["vad_parameters"]["threshold"] == 0.3
    assert permissive["vad_parameters"]["min_speech_duration_ms"] == 100
    assert (transcribe.conditioning_settings({"vad_filter": True, "vad_threshold": 7})
            ["vad_parameters"]["threshold"] == 0.95)
    assert not transcribe.conditioning_active(vad_only)


def test_condition_audio_pads_normalises_and_resamples_to_16k(tmp_path):
    import numpy as np
    wav = tmp_path / "tone.wav"
    write_tone(wav, seconds=0.5, amplitude=0.1)
    samples = transcribe.read_pcm16(wav)
    assert samples.dtype == np.float32 and len(samples) == 4000
    assert 0.09 < float(np.max(np.abs(samples))) < 0.11

    out = transcribe.condition_audio(samples, transcribe.conditioning_settings(
        {"pad_seconds": 0.5, "normalize_gain": True}))
    assert out.dtype == np.float32
    assert len(out) == int((0.5 + 0.5 + 0.5) * 16000)
    peak = float(np.max(np.abs(out)))
    assert 0.8 < peak <= transcribe.GAIN_TARGET_PEAK + 1e-6  # 0.9 before the 16 kHz interpolation
    assert not np.any(out[:7990]) and not np.any(out[-7990:])  # the padding is silence (one interpolated edge sample aside)

    plain = transcribe.condition_audio(samples, transcribe.conditioning_settings({"pad_seconds": 0.0}))
    assert len(plain) == 8000  # no conditioning except the model's 16 kHz


def test_condition_audio_bandpass_keeps_voice_and_cuts_hum(tmp_path):
    import numpy as np
    settings = transcribe.conditioning_settings({"bandpass": True})
    voice = tmp_path / "voice.wav"
    hum = tmp_path / "hum.wav"
    write_tone(voice, seconds=1.0, hz=1000.0, amplitude=0.5)
    write_tone(hum, seconds=1.0, hz=60.0, amplitude=0.5)
    kept = transcribe.condition_audio(transcribe.read_pcm16(voice), settings)
    cut = transcribe.condition_audio(transcribe.read_pcm16(hum), settings)
    assert len(kept) == len(cut) == 16000
    assert float(np.max(np.abs(kept[2000:-2000]))) > 0.35
    assert float(np.max(np.abs(cut[2000:-2000]))) < 0.1


def test_read_pcm16_rejects_stereo(tmp_path):
    wav = tmp_path / "stereo.wav"
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\x00\x00" * 800)
    with pytest.raises(ValueError):
        transcribe.read_pcm16(wav)


def test_transcriber_without_conditioning_still_passes_the_path(tmp_path):
    model = FakeModel()
    transcribe.ClipTranscriber(model, "en", 5, "off", None).transcribe(tmp_path / "a.wav")
    audio, kwargs = model.calls[0]
    assert audio == str(tmp_path / "a.wav")
    assert kwargs == BASE_KWARGS


def test_transcriber_with_vad_and_padding_passes_conditioned_samples(tmp_path):
    import numpy as np
    wav = tmp_path / "a.wav"
    write_tone(wav, seconds=0.5)
    model = FakeModel()
    settings = transcribe.conditioning_settings({"vad_filter": True, "pad_seconds": 0.5,
                                                 "normalize_gain": True})
    clip = transcribe.ClipTranscriber(model, "en", 5, "off", None, settings).transcribe(wav)
    audio, kwargs = model.calls[0]
    assert isinstance(audio, np.ndarray) and audio.dtype == np.float32 and len(audio) == 24000
    assert kwargs == {
        "language": "en", "beam_size": 5, "condition_on_previous_text": False,
        "vad_filter": True, "vad_parameters": {"threshold": 0.5, "min_speech_duration_ms": 250,
                                               "min_silence_duration_ms": 300, "speech_pad_ms": 200},
    }
    assert clip["transcript"] == "engine nine respond"
    # Segment times are reported against the clip, not the padded audio
    # (the fake model answers 0.0 to 1.0 on audio that starts 0.5 s in).
    assert clip["segments"][0]["start"] == 0.0 and clip["segments"][0]["end"] == 0.5


def test_vad_alone_passes_the_path_with_vad_parameters(tmp_path):
    model = FakeModel()
    settings = transcribe.conditioning_settings({"vad_filter": True, "vad_min_silence_ms": 500})
    transcribe.ClipTranscriber(model, "en", 5, "off", None, settings).transcribe(tmp_path / "a.wav")
    audio, kwargs = model.calls[0]
    assert audio == str(tmp_path / "a.wav")
    assert kwargs["vad_filter"] is True
    assert kwargs["vad_parameters"] == {"threshold": 0.5, "min_speech_duration_ms": 250,
                                        "min_silence_duration_ms": 500, "speech_pad_ms": 200}


def test_padding_does_not_make_a_short_clip_look_long_to_the_hint_rule(tmp_path):
    """The model reports the padded length; the hint-suspect rule must use the
    clip's own length so a parroted 0.5 s key-up is still flagged."""
    glossary_path = tmp_path / "status" / "glossary.json"
    write_glossary(glossary_path, hotwords="Engine 9")
    wav = tmp_path / "a.wav"
    write_tone(wav, seconds=0.5)
    model = FakeModel(text=" Engine 9.", duration=4.5)  # 0.5 s clip + 2 x 2 s padding
    settings = transcribe.conditioning_settings({"pad_seconds": 2.0})
    clip = transcribe.ClipTranscriber(model, "en", 5, "hotwords",
                                      transcribe.GlossaryCache(glossary_path), settings).transcribe(wav)
    assert clip["hint_suspect"] is True


def test_main_applies_the_shipped_conditioning_config(tmp_path, monkeypatch):
    import numpy as np
    root = tmp_path / "data"
    pending = root / "queue" / "pending"
    pending.mkdir(parents=True)
    wav_path = root / "audio" / "ems" / "ems_tx_20261107T120000_000000Z_ab12cd34.wav"
    write_tone(wav_path, seconds=1.0)
    segment_id = wav_path.stem
    (pending / f"{segment_id}.json").write_text(json.dumps(
        {"segment_id": segment_id, "channel": "ems", "direction": "tx", "audio_file": str(wav_path)}),
        encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "output_directory": str(root),
        "transcription": {"model": "fake-model", "device": "cpu", "compute_type": "int8",
                          "language": "en", "beam_size": 5, "glossary_mode": "off",
                          "vad_filter": True, "pad_seconds": 0.5, "normalize_gain": True},
    }), encoding="utf-8")
    seen = {}

    class StopAfterFirst(FakeModel):
        def __init__(self, *args, **kwargs):
            super().__init__(text=" Medic 4 en route.", duration=2.0,
                             on_call=lambda: setattr(transcribe, "STOP", True))
            seen["model"] = self

    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = StopAfterFirst
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(transcribe, "CONFIG", config)
    monkeypatch.setattr(transcribe, "STOP", False)

    transcribe.main()

    audio, kwargs = seen["model"].calls[0]
    assert isinstance(audio, np.ndarray) and len(audio) == 32000  # (1.0 + 2 x 0.5) s at 16 kHz
    assert kwargs["vad_filter"] is True and kwargs["vad_parameters"]["speech_pad_ms"] == 200
    result = json.loads((root / "transcripts" / f"{segment_id}.json").read_text(encoding="utf-8"))
    assert result["transcript"] == "Medic 4 en route."
    assert "hint_suspect" not in result


def test_repo_config_ships_vad_and_padding():
    config = json.loads((TRANSCRIBE_PATH.parent / "config.json").read_text(encoding="utf-8"))
    settings = transcribe.conditioning_settings(config["transcription"])
    assert settings["vad_filter"] is True
    assert settings["pad_seconds"] == 0.5
    assert settings["normalize_gain"] is True and settings["bandpass"] is False
    assert settings["vad_parameters"] == {"threshold": 0.3, "min_speech_duration_ms": 100,
                                          "min_silence_duration_ms": 300, "speech_pad_ms": 200}
