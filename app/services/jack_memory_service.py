from __future__ import annotations

import re
from uuid import UUID

from app.services.analytics_database import (
    AnalyticsDatabaseError,
    AnalyticsRepository,
)


def _terms(value: str) -> set[str]:
    stop_words = {
        "about", "and", "are", "for", "from", "how", "that", "the",
        "this", "what", "when", "where", "which", "with",
    }
    return {
        term
        for term in re.findall(r"[a-z0-9][a-z0-9_+-]{2,}", (value or "").lower())
        if term not in stop_words
    }


def _looks_like_protected_value(value: str) -> bool:
    text = value or ""
    patterns = (
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        (
            r"\b(?:password|passcode|api[_ -]?key|access[_ -]?token|"
            r"bearer[_ -]?token|license[_ -]?key|private[_ -]?key|secret)"
            r"\s*[:=]\s*\S+"
        ),
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def find_approved_jack_memory(question: str, limit: int = 4) -> list[dict]:
    question_terms = _terms(question)
    if not question_terms:
        return []
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            rows = repository.fetchall(
                """
                SELECT
                    memory_id,
                    title,
                    trigger_text,
                    guidance,
                    approved_at,
                    approved_by
                FROM lcdash_analytics.jack_memory
                WHERE status = 'approved'
                ORDER BY updated_at DESC
                LIMIT 200
                """
            )
            matches = []
            for row in rows:
                trigger_terms = _terms(row[2])
                overlap = question_terms & trigger_terms
                if not overlap:
                    continue
                coverage = len(overlap) / max(len(trigger_terms), 1)
                if coverage < 0.33:
                    continue
                matches.append(
                    {
                        "memory_id": int(row[0]),
                        "title": row[1],
                        "trigger_text": row[2],
                        "guidance": row[3],
                        "approved_at": row[4].isoformat() if row[4] else "",
                        "approved_by": row[5],
                        "matched_terms": sorted(overlap),
                        "coverage": round(coverage, 4),
                    }
                )
            matches.sort(
                key=lambda item: (item["coverage"], len(item["matched_terms"])),
                reverse=True,
            )
            selected = matches[: min(max(limit, 1), 10)]
            if selected:
                repository._execute(
                    """
                    UPDATE lcdash_analytics.jack_memory
                    SET use_count = use_count + 1,
                        last_used_at = NOW()
                    WHERE memory_id = ANY(%s)
                    """,
                    ([item["memory_id"] for item in selected],),
                )
                repository._commit()
            return selected
    except AnalyticsDatabaseError:
        return []


def create_jack_memory_candidate(
    *,
    title: str,
    trigger_text: str,
    guidance: str,
    created_by: str,
    source_interaction_id: str | None = None,
) -> dict:
    clean_title = title.strip()
    clean_trigger = trigger_text.strip()
    clean_guidance = guidance.strip()
    if not clean_title or not clean_trigger or not clean_guidance:
        raise ValueError("Title, trigger text, and guidance are required.")
    if _looks_like_protected_value(" ".join((clean_title, clean_trigger, clean_guidance))):
        raise ValueError("Protected credentials or secret values cannot be stored in JACK memory.")
    parsed_interaction_id = None
    if source_interaction_id:
        try:
            parsed_interaction_id = UUID(source_interaction_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid JACK interaction identifier.") from exc
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            row = repository.fetchone(
                """
                INSERT INTO lcdash_analytics.jack_memory (
                    created_by,
                    title,
                    trigger_text,
                    guidance,
                    source_interaction_id
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING memory_id
                """,
                (
                    created_by[:320],
                    clean_title[:200],
                    clean_trigger[:1000],
                    clean_guidance[:4000],
                    parsed_interaction_id,
                ),
            )
            repository._commit()
        return {"saved": True, "memory_id": int(row[0]), "status": "pending"}
    except AnalyticsDatabaseError as exc:
        return {"saved": False, "message": str(exc)}


def review_jack_memory(
    *,
    memory_id: int,
    decision: str,
    reviewed_by: str,
) -> dict:
    normalized = decision.strip().lower()
    if normalized not in {"approved", "rejected", "retired"}:
        raise ValueError("Unsupported JACK memory review decision.")
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            row = repository.fetchone(
                """
                UPDATE lcdash_analytics.jack_memory
                SET status = %s,
                    approved_at = CASE
                        WHEN %s = 'approved' THEN NOW()
                        ELSE approved_at
                    END,
                    approved_by = CASE
                        WHEN %s = 'approved' THEN %s
                        ELSE approved_by
                    END,
                    updated_at = NOW()
                WHERE memory_id = %s
                RETURNING memory_id, status
                """,
                (
                    normalized,
                    normalized,
                    normalized,
                    reviewed_by[:320],
                    memory_id,
                ),
            )
            repository._commit()
        if not row:
            return {"saved": False, "message": "JACK memory candidate not found."}
        return {"saved": True, "memory_id": int(row[0]), "status": row[1]}
    except AnalyticsDatabaseError as exc:
        return {"saved": False, "message": str(exc)}


def list_jack_memory_items(limit: int = 200) -> list[dict]:
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            rows = repository.fetchall(
                """
                SELECT
                    memory_id,
                    created_at,
                    created_by,
                    approved_at,
                    approved_by,
                    status,
                    title,
                    trigger_text,
                    guidance,
                    source_interaction_id,
                    use_count,
                    last_used_at
                FROM lcdash_analytics.jack_memory
                ORDER BY
                    CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                    updated_at DESC
                LIMIT %s
                """,
                (min(max(limit, 1), 500),),
            )
    except AnalyticsDatabaseError:
        return []
    return [
        {
            "memory_id": int(row[0]),
            "created_at": row[1].isoformat() if row[1] else "",
            "created_by": row[2],
            "approved_at": row[3].isoformat() if row[3] else "",
            "approved_by": row[4],
            "status": row[5],
            "title": row[6],
            "trigger_text": row[7],
            "guidance": row[8],
            "source_interaction_id": str(row[9]) if row[9] else "",
            "use_count": int(row[10] or 0),
            "last_used_at": row[11].isoformat() if row[11] else "",
        }
        for row in rows
    ]
