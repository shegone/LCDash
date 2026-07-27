from __future__ import annotations

import json
from uuid import UUID, uuid4

from app.services.analytics_database import AnalyticsDatabaseError, AnalyticsRepository


ALLOWED_JACK_FEEDBACK = {
    "helpful",
    "incorrect",
    "incomplete",
    "wrong_source",
}


def record_jack_interaction(
    *,
    user_email: str,
    question: str,
    result: dict,
) -> dict:
    interaction_id = uuid4()
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            repository._execute(
                """
                INSERT INTO lcdash_analytics.jack_interactions (
                    interaction_id, user_email, question, answer, model,
                    source_metadata, evidence_metadata, assurance_metadata,
                    write_access
                )
                VALUES (
                    %(interaction_id)s, %(user_email)s, %(question)s,
                    %(answer)s, %(model)s, %(source_metadata)s::JSONB,
                    %(evidence_metadata)s::JSONB, %(assurance_metadata)s::JSONB,
                    %(write_access)s
                )
                """,
                {
                    "interaction_id": interaction_id,
                    "user_email": user_email[:320],
                    "question": question,
                    "answer": str(result.get("answer") or ""),
                    "model": str(result.get("model") or "")[:200],
                    "source_metadata": json.dumps(
                        result.get("sources") or [],
                        ensure_ascii=False,
                        default=str,
                    ),
                    "evidence_metadata": json.dumps(
                        result.get("evidence") or [],
                        ensure_ascii=False,
                        default=str,
                    ),
                    "assurance_metadata": json.dumps(
                        result.get("assurance") or {},
                        ensure_ascii=False,
                        default=str,
                    ),
                    "write_access": bool(result.get("write_access")),
                },
            )
            repository._commit()
        return {"saved": True, "interaction_id": str(interaction_id)}
    except AnalyticsDatabaseError as exc:
        return {"saved": False, "interaction_id": "", "error": str(exc)}


def record_jack_feedback(
    *,
    interaction_id: str,
    user_email: str,
    rating: str,
    comment: str = "",
) -> dict:
    normalized_rating = str(rating or "").strip().lower()
    if normalized_rating not in ALLOWED_JACK_FEEDBACK:
        raise ValueError("Unsupported JACK feedback rating.")
    try:
        parsed_id = UUID(interaction_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid JACK interaction identifier.") from exc

    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            if not repository.fetchone(
                """
                SELECT interaction_id
                FROM lcdash_analytics.jack_interactions
                WHERE interaction_id = %s
                """,
                (parsed_id,),
            ):
                return {
                    "saved": False,
                    "message": "The JACK interaction was not found.",
                }
            repository._execute(
                """
                INSERT INTO lcdash_analytics.jack_feedback (
                    interaction_id, user_email, rating, comment
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    parsed_id,
                    user_email[:320],
                    normalized_rating,
                    str(comment or "")[:1000],
                ),
            )
            repository._commit()
        return {
            "saved": True,
            "interaction_id": str(parsed_id),
            "rating": normalized_rating,
        }
    except AnalyticsDatabaseError as exc:
        return {"saved": False, "message": str(exc)}


def list_jack_feedback(limit: int = 100) -> list[dict]:
    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            rows = repository.fetchall(
                """
                SELECT feedback.created_at, feedback.rating, feedback.comment,
                       feedback.user_email, interactions.question,
                       interactions.answer
                FROM lcdash_analytics.jack_feedback AS feedback
                JOIN lcdash_analytics.jack_interactions AS interactions
                    ON interactions.interaction_id = feedback.interaction_id
                ORDER BY feedback.created_at DESC
                LIMIT %s
                """,
                (min(max(limit, 1), 500),),
            )
    except AnalyticsDatabaseError:
        return []
    return [
        {
            "created_at": row[0].isoformat() if row[0] else "",
            "rating": row[1],
            "comment": row[2],
            "user_email": row[3],
            "question": row[4],
            "answer": row[5],
        }
        for row in rows
    ]
