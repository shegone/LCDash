"""Static, network-free release-readiness checks for advisory AI surfaces."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_mae_conversation_fails_closed_without_both_voice_components():
    script = _read("static/js/lcdash-mae.js")
    assert "status.tts.ready" in script
    assert "status.stt.ready" in script
    assert "voiceToggle.disabled = !voiceReady" in script
    assert "transcription gate not complete" in script


def test_voice_lab_preserves_component_specific_controls_and_limits():
    template = _read("templates/voice_lab.html")
    script = _read("static/js/lcdash-voice.js")
    assert "cloud_voice and not tts_enabled" in template
    assert "cloud_voice and not stt_enabled" in template
    assert "speakButton.disabled = !ttsReady" in script
    assert "recordButton.disabled = !sttReady" in script
    assert "}, 30000);" in script
    assert "audio was not persisted" in script


def test_all_three_surfaces_state_the_action_free_boundary():
    mae = _read("templates/mae.html")
    knowledge = _read("templates/knowledge.html")
    voice = _read("templates/voice_lab.html")
    assert "MAE cannot dispatch" in mae
    assert "alter CAD" in mae
    assert "Knowledge search has no CAD" in knowledge
    assert "operational-control tools" in knowledge
    assert "Voice cannot write to CAD" in voice
    assert "station tones" in voice
    assert "ESInet" in voice


def test_release_gate_document_forbids_execution_and_bounds_single_call():
    plan = _read("docs/planning/CLOUD_AI_KNOWLEDGE_VOICE_RELEASE_READINESS_2026-08-05.md")
    required = (
        "NO DEPLOYMENT OR PROVIDER CALL AUTHORIZED",
        "exactly one `polly:SynthesizeSpeech` request",
        "LCDash voice test. Nine one one.",
        "No retry",
        "No CAD, dispatch, alert, paging, station-tone, radio, ESInet",
        "No `Retrieve`, `Converse`, `InvokeModel`, or Transcribe call",
        "immutable image digest",
        "named human approver",
    )
    for phrase in required:
        assert phrase in plan
