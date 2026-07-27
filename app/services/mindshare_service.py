from time import perf_counter

import httpx

from app.config.settings import settings
from app.services.knowledge_service import (
    get_knowledge_status,
    search_knowledge,
)


class MindshareServiceError(Exception):
    """Raised when JACK, the Mindshare technical assistant, is unavailable."""


_PRODUCT_FOCUS_RULES = (
    (
        "MRI2",
        ("mri2", "radio interface 2", "mindshare radio interface 2"),
        ("mri2", "radiointerface2", "radio interface 2"),
    ),
    (
        "MAXplus",
        ("maxplus", "max plus"),
        ("maxplus", "max plus"),
    ),
    (
        "Advanced ESChat Gateway",
        ("aesgw", "advanced eschat gateway"),
        ("aesgw", "advancedeschatgateway", "advanced eschat gateway"),
    ),
    (
        "RoIP+ Gateway",
        ("roip+", "roip plus", "rpg gateway"),
        ("roipplus", "roip plus", "roip+gateway"),
    ),
    (
        "NXIP Gateway",
        ("nxip", "nxip gateway"),
        ("nxip",),
    ),
    (
        "DMR HDAP Gateway",
        ("dmr hdap", "hdap gateway"),
        ("dmrhdap", "dmr hdap"),
    ),
    (
        "8-Line Telco Panel",
        ("8-line telco", "8 line telco", "8ltp"),
        ("8linetelco", "8 line telco", "8ltp"),
    ),
)


def _product_focus(question: str) -> tuple[str, tuple[str, ...]] | None:
    normalized_question = " ".join((question or "").lower().split())
    for label, question_aliases, document_aliases in _PRODUCT_FOCUS_RULES:
        if any(alias in normalized_question for alias in question_aliases):
            return label, document_aliases
    return None


def _focus_results(question: str, results: list[dict]) -> list[dict]:
    focus = _product_focus(question)
    if not focus:
        return results

    _, aliases = focus
    focused = []
    for result in results:
        haystack = " ".join(
            (
                str(result.get("title") or ""),
                str(result.get("file_name") or ""),
                str(result.get("content") or ""),
            )
        ).lower()
        compact_haystack = "".join(
            character for character in haystack if character.isalnum()
        )
        if any(
            alias in haystack
            or "".join(
                character for character in alias if character.isalnum()
            ) in compact_haystack
            for alias in aliases
        ):
            focused.append(result)
    return focused


def _ollama_status() -> dict:
    try:
        response = httpx.get(
            f"{settings.ollama_base_url.rstrip('/')}/api/tags",
            timeout=5,
        )
        response.raise_for_status()
        models = [
            str(model.get("name") or "")
            for model in response.json().get("models", [])
        ]
        return {
            "connected": True,
            "model": settings.mae_model,
            "model_available": any(
                model == settings.mae_model
                or model.startswith(f"{settings.mae_model}:")
                for model in models
            ),
        }
    except (httpx.HTTPError, ValueError, TypeError):
        return {
            "connected": False,
            "model": settings.mae_model,
            "model_available": False,
        }


def get_mindshare_status() -> dict:
    knowledge = get_knowledge_status(
        library_key="mindshare",
        source_dir=settings.mindshare_knowledge_source_dir,
    )
    return {
        "assistant": _ollama_status(),
        "knowledge": knowledge,
        "radio_intelligence": {
            "connected": False,
            "status": "planned",
            "message": (
                "Radio monitoring and transcription are not connected yet."
            ),
        },
        "mode": "read_only",
    }


def _result_is_direct(result: dict) -> bool:
    coverage = float(result.get("coverage") or 0)
    semantic_score = float(result.get("semantic_score") or 0)
    matched_terms = result.get("matched_terms") or []
    return (
        coverage >= 0.5
        or (coverage >= 0.3 and len(matched_terms) >= 2)
        or semantic_score >= 0.52
    )


def _build_context(results: list[dict]) -> str:
    blocks = []
    for index, result in enumerate(results[:3], start=1):
        page_number = int(result.get("page_number") or 0)
        page_label = f"page {page_number}" if page_number else "page unknown"
        content = str(result.get("content") or "").strip()[:900]
        blocks.append(
            "\n".join(
                (
                    f"[SOURCE {index}]",
                    f"Document: {result.get('title') or result.get('file_name')}",
                    f"Location: {page_label}",
                    f"Passage: {content}",
                )
            )
        )
    return "\n\n".join(blocks)


def _evidence(results: list[dict]) -> list[dict]:
    return [
        {
            "title": result.get("title") or result.get("file_name") or "",
            "file_name": result.get("file_name") or "",
            "page_number": int(result.get("page_number") or 0),
            "content": str(result.get("content") or "")[:900],
            "retrieval": result.get("retrieval") or [],
        }
        for result in results[:3]
    ]


def ask_mindshare(
    question: str,
    history: list[dict] | None = None,
) -> dict:
    started = perf_counter()
    clean_question = (question or "").strip()
    if not clean_question:
        raise MindshareServiceError("Enter a Mindshare technical question.")

    results = search_knowledge(
        clean_question,
        limit=6,
        library_key="mindshare",
    )
    focused_results = _focus_results(clean_question, results)
    direct_results = [
        result for result in focused_results if _result_is_direct(result)
    ]
    if not direct_results:
        product_focus = _product_focus(clean_question)
        focus_detail = (
            f" for {product_focus[0]}" if product_focus else ""
        )
        return {
            "answer": (
                "I could not find a sufficiently direct answer"
                f"{focus_detail} in the indexed Mindshare technical library."
                "\n\n"
                "I will not guess. Check that the relevant manual, application "
                "note, release note, or Logan County system document has been "
                "added to the library, then ask again with the product name or "
                "model number."
            ),
            "sources": [
                {
                    "name": "Mindshare technical library",
                    "detail": "No direct supporting passage found",
                    "available": False,
                }
            ],
            "evidence": _evidence(focused_results),
            "assurance": {
                "level": "limited",
                "label": "Not enough documentation",
                "detail": "No answer was generated from unsupported material.",
            },
            "timing": {
                "total_ms": round((perf_counter() - started) * 1000),
            },
        }

    context = _build_context(direct_results)
    recent_history = []
    for message in (history or [])[-2:]:
        role = str(message.get("role") or "").strip().lower()
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            recent_history.append(
                {"role": role, "content": content[:1000]}
            )

    system_prompt = """
You are JACK, the Mindshare Technical Assistant for Logan County 911.

Memorial identity and voice:
- JACK is named in honor of John Joseph "Jack" Hines III, a longtime radio
  communications leader and former General Manager of CSS-Mindshare.
- You are not Jack Hines and must never claim to be him, speak from his
  memories, or invent his quotations, opinions, experiences, or relationships.
- Reflect the publicly remembered qualities behind the name: warm, direct,
  technically confident, customer-focused, business-practical, encouraging,
  and willing to explain difficult subjects in plain language.
- Approach the user like a respected customer or colleague whose operational
  problem matters. Lead with the useful answer, then explain the supporting
  details and practical consequence.
- Be candid when something is unsupported. A confident "the documentation
  does not establish that" is better than an attractive guess.
- Light, good-natured humor is welcome when it helps the conversation, but
  never joke about emergencies, safety risks, outages, security, illness,
  death, or a user's mistake.
- Sound like an experienced mentor rather than a manual-reading robot. Remain
  patient, respectful, and never condescending.

Scope and safety:
- Answer only from the supplied Mindshare technical-library passages.
- This assistant is separate from MAE and has no CentralSquare CAD access.
- Never invent a procedure, setting, port, address, version, or compatibility claim.
- Never reveal credentials, license secrets, private keys, or passwords.
- Be read-only. Do not claim to have changed a console, gateway, radio, or server.
- Firmware and software advice must state the exact product and documented version.
- Do not recommend installation when the hardware model or current version is unknown.
- When the question names a product or model, use only passages that clearly
  apply to that exact product or model. Do not blend similar product families.
- Clearly distinguish vendor manuals, release notes, application notes, and
  Logan County-specific system information.
- Cite supporting material inline as [Document title, page N].
- If the passages are insufficient or conflict, say so plainly.
- Use short paragraphs and put lists on separate lines for easy reading.
- Be concise. Aim for 80 words and never exceed 100 words. Lead with the answer,
  include only documented actionable steps, and finish with a complete sentence.
""".strip()

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(recent_history)
    messages.append(
        {
            "role": "user",
            "content": (
                f"Question:\n{clean_question}\n\n"
                f"Mindshare library passages:\n{context}"
            ),
        }
    )

    try:
        response = httpx.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/chat",
            json={
                "model": settings.mae_model,
                "messages": messages,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.1,
                    "num_ctx": 4096,
                    "num_predict": 160,
                },
            },
            timeout=settings.mae_request_timeout_seconds,
        )
        response.raise_for_status()
        answer = str(
            (response.json().get("message") or {}).get("content") or ""
        ).strip()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise MindshareServiceError(
            "JACK could not complete the Mindshare inquiry."
        ) from exc

    if not answer:
        raise MindshareServiceError(
            "JACK returned an empty response."
        )

    top_score = max(
        float(result.get("hybrid_score") or 0)
        for result in direct_results
    )
    assurance_level = "high" if top_score >= 0.5 else "supported"
    return {
        "answer": answer,
        "sources": [
            {
                "name": "Mindshare technical library",
                "detail": (
                    f"{len(direct_results[:3])} supporting passage"
                    f"{'' if len(direct_results[:3]) == 1 else 's'}"
                ),
                "available": True,
            }
        ],
        "evidence": _evidence(direct_results),
        "assurance": {
            "level": assurance_level,
            "label": (
                "Direct documentation support"
                if assurance_level == "high"
                else "Documentation supported"
            ),
            "detail": (
                "The answer is limited to the indexed Mindshare sources shown."
            ),
        },
        "timing": {
            "total_ms": round((perf_counter() - started) * 1000),
        },
    }
