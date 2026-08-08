from pathlib import Path


def test_jack_voice_engine_has_cpu_oom_fallback():
    source = (
        Path(__file__).parents[1] / "deploy" / "qwen3-tts-jack" / "app.py"
    ).read_text(encoding="utf-8")

    assert 'device_map="cuda:0"' in source
    assert '"out of memory"' in source
    assert 'device_map="cpu"' in source
    assert '"active_device": _model_device' in source
