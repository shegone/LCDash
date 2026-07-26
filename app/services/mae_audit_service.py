import json
from uuid import UUID, uuid4

from app.services.analytics_database import (
    AnalyticsDatabaseError,
    AnalyticsRepository,
)


ALLOWED_FEEDBACK_RATINGS = {
    "helpful",
    "incorrect",
    "incomplete",
    "wrong_source",
}


def record_mae_interaction(
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
                INSERT INTO lcdash_analytics.mae_interactions (
                    interaction_id,
                    user_email,
                    question,
                    answer,
                    model,
                    source_metadata,
                    evidence_metadata,
                    entities,
                    write_access
                )
                VALUES (
                    %(interaction_id)s,
                    %(user_email)s,
                    %(question)s,
                    %(answer)s,
                    %(model)s,
                    %(source_metadata)s::JSONB,
                    %(evidence_metadata)s::JSONB,
                    %(entities)s::JSONB,
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
                    "entities": json.dumps(
                        result.get("entities") or {},
                        ensure_ascii=False,
                        default=str,
                    ),
                    "write_access": bool(result.get("write_access")),
                },
            )
            repository._commit()
        return {
            "saved": True,
            "interaction_id": str(interaction_id),
        }
    except AnalyticsDatabaseError as exc:
        return {
            "saved": False,
            "interaction_id": "",
            "error": str(exc),
        }


def record_mae_feedback(
    *,
    interaction_id: str,
    user_email: str,
    rating: str,
    comment: str = "",
) -> dict:
    normalized_rating = rating.strip().lower()
    if normalized_rating not in ALLOWED_FEEDBACK_RATINGS:
        raise ValueError("Unsupported MAE feedback rating.")

    try:
        parsed_interaction_id = UUID(interaction_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid MAE interaction identifier.") from exc

    try:
        with AnalyticsRepository() as repository:
            repository.initialize_schema()
            interaction = repository.fetchone(
                """
                SELECT interaction_id
                FROM lcdash_analytics.mae_interactions
                WHERE interaction_id = %s
                """,
                (parsed_interaction_id,),
            )
            if not interaction:
                return {
                    "saved": False,
                    "message": "The MAE interaction was not found.",
                }
            repository._execute(
                """
                INSERT INTO lcdash_analytics.mae_feedback (
                    interaction_id,
                    user_email,
                    rating,
                    comment
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    parsed_interaction_id,
                    user_email[:320],
                    normalized_rating,
                    comment[:1000],
                ),
            )
            repository._commit()
        return {
            "saved": True,
            "interaction_id": str(parsed_interaction_id),
            "rating": normalized_rating,
        }
    except AnalyticsDatabaseError as exc:
        return {
            "saved": False,
            "message": str(exc),
        }
