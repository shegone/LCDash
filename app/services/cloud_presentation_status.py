"""One fail-closed, presentation-safe status model for the cloud pilot."""

from __future__ import annotations

from typing import Any, Mapping


VERIFIED_CAD_MODE = "centralsquare-read-poll"


def _cad_source_status(cad_status: Mapping[str, Any]) -> dict[str, Any]:
    enabled = cad_status.get("enabled") is True
    mode = str(cad_status.get("mode") or "")
    freshness = str(cad_status.get("freshness") or "disabled")
    error_code = str(cad_status.get("error_code") or "")
    provider_selected = enabled and mode == VERIFIED_CAD_MODE
    recent_success = provider_selected and freshness == "current"

    if recent_success and not error_code:
        state = "verified-current"
        label = "VERIFIED READ-ONLY"
        notice = "A recent successful read verified the current read-only CAD snapshot."
    elif recent_success:
        state = "last-verified"
        label = "LAST VERIFIED READ"
        notice = "The last verified snapshot remains visible while the latest read is unavailable."
    elif provider_selected and freshness == "stale":
        state = "stale"
        label = "STALE READ-ONLY DATA"
        notice = "The last successful read is stale. Data remains visible as last known only."
    elif provider_selected:
        state = "awaiting-success"
        label = "AWAITING VERIFIED READ"
        notice = "The read-only provider is enabled but has not completed a recent successful read."
    elif enabled:
        state = "unverified-disabled"
        label = "SOURCE UNVERIFIED"
        notice = "The enabled provider mode is not approved for a live-source claim."
    else:
        state = "disconnected"
        label = "SYNTHETIC / DISCONNECTED"
        notice = "No enabled read-only CAD provider has completed a verified read."

    return {
        "state": state,
        "label": label,
        "notice": notice,
        "provider_enabled": enabled,
        "provider_mode": mode or "synthetic-disconnected",
        "provider_selected": provider_selected,
        "recent_success": recent_success,
        "connected": state == "verified-current",
        "may_display_snapshot": provider_selected and (recent_success or freshness == "stale"),
        "freshness": freshness,
        "age_seconds": cad_status.get("age_seconds"),
        "error_code": error_code,
        "call_count": int(cad_status.get("call_count") or 0),
        "unit_count": int(cad_status.get("unit_count") or 0),
    }


def build_cloud_presentation_status(
    *,
    cad_status: Mapping[str, Any],
    ai_status: Mapping[str, Any],
    knowledge_status: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Combine existing local status only; never call a provider to discover health."""

    source = _cad_source_status(cad_status)
    documents_ingested = ai_status.get("documents_ingested") is True
    rag_available = ai_status.get("rag_available") is True
    voice_configured = ai_status.get("voice_enabled") is True
    tts_status = ai_status.get("tts") if isinstance(ai_status.get("tts"), Mapping) else {}
    stt_status = ai_status.get("stt") if isinstance(ai_status.get("stt"), Mapping) else {}
    tts_ready = voice_configured and tts_status.get("ready") is True
    stt_ready = voice_configured and stt_status.get("ready") is True
    local_documents = int(knowledge_status.get("documents") or 0)

    knowledge_ready = documents_ingested and rag_available
    if knowledge_ready:
        knowledge_label = "CITATION SEARCH AVAILABLE"
        knowledge_notice = "Approved private documents are ingested and citation search is available."
    else:
        knowledge_label = "NOT INGESTED"
        knowledge_notice = (
            "Private cloud documents are not ingested. The library remains read-only and "
            "citation search is unavailable pending approval."
        )

    if rag_available:
        advisory_label = "CITATION-ONLY AVAILABLE"
        advisory_notice = "MAE may answer from approved retrieved citations and has no action tools."
    else:
        advisory_label = "ADVISORY PROVIDER UNAVAILABLE"
        advisory_notice = (
            "MAE controls remain available, but document-grounded cloud answers will fail "
            "closed until approved ingestion and provider wiring are complete."
        )

    if tts_ready:
        voice_label = "TTS READY - VERIFIED ON USE"
        voice_notice = (
            "Optional managed speech is configured for bounded synthesis and fails closed. "
            "Conversational microphone mode remains disabled until transcription is approved."
        )
    elif voice_configured:
        voice_label = "TTS CONFIGURED - PROVIDER UNAVAILABLE"
        voice_notice = "Managed speech is configured but not ready; synthesis remains disabled."
    else:
        voice_label = "VOICE NOT CONFIGURED"
        voice_notice = "Voice controls remain visible, but managed speech is not configured."

    return {
        "schema_version": "cloud-presentation-status.v1",
        "source": source,
        "browser_transport": {
            "label": "BROWSER UPDATE CHANNEL",
            "notice": (
                "Browser streaming is an application refresh channel and does not "
                "prove CAD provider connectivity."
            ),
        },
        "knowledge": {
            "ready": knowledge_ready,
            "label": knowledge_label,
            "notice": knowledge_notice,
            "cloud_documents_ingested": documents_ingested,
            "local_document_count": local_documents,
        },
        "advisory": {
            "ready": rag_available,
            "label": advisory_label,
            "notice": advisory_notice,
            "controls_visible": True,
        },
        "voice": {
            "configured": voice_configured,
            "tts_ready": tts_ready,
            "stt_ready": stt_ready,
            "conversation_ready": tts_ready and stt_ready,
            "label": voice_label,
            "notice": voice_notice,
            "controls_visible": True,
        },
    }
