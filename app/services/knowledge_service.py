from pathlib import Path
import re

import psycopg

from app.config.settings import settings


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "database" / "knowledge_schema.sql"


class KnowledgeServiceError(Exception):
    """Raised when the MAE knowledge library is unavailable."""


def knowledge_is_configured() -> bool:
    database_url = (settings.database_url or "").strip()
    return bool(database_url and "change_me" not in database_url)


def _connect():
    if not knowledge_is_configured():
        raise KnowledgeServiceError("The knowledge database is not configured.")
    try:
        return psycopg.connect(settings.database_url, connect_timeout=10)
    except psycopg.Error as exc:
        raise KnowledgeServiceError(
            "LCDash could not connect to the knowledge database."
        ) from exc


def ensure_knowledge_schema(connection) -> None:
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    connection.commit()


def get_knowledge_status() -> dict:
    if not knowledge_is_configured():
        return {
            "configured": False,
            "connected": False,
            "documents": 0,
            "chunks": 0,
            "index_state": {},
            "message": "The CentralSquare knowledge library is not configured.",
        }

    try:
        with _connect() as connection:
            ensure_knowledge_schema(connection)
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM lcdash_knowledge.documents),
                    (SELECT COUNT(*) FROM lcdash_knowledge.chunks)
                """
            ).fetchone() or (0, 0)
            state = connection.execute(
                """
                SELECT
                    status,
                    started_at,
                    completed_at,
                    documents_found,
                    documents_indexed,
                    documents_unchanged,
                    documents_failed,
                    chunks_stored,
                    error_summary
                FROM lcdash_knowledge.index_state
                WHERE state_id = TRUE
                """
            ).fetchone()
    except (KnowledgeServiceError, psycopg.Error):
        return {
            "configured": True,
            "connected": False,
            "documents": 0,
            "chunks": 0,
            "index_state": {},
            "message": "The CentralSquare knowledge library is unavailable.",
        }

    index_state = {}
    if state:
        index_state = {
            "status": state[0],
            "started_at": state[1].isoformat() if state[1] else "",
            "completed_at": state[2].isoformat() if state[2] else "",
            "documents_found": int(state[3] or 0),
            "documents_indexed": int(state[4] or 0),
            "documents_unchanged": int(state[5] or 0),
            "documents_failed": int(state[6] or 0),
            "chunks_stored": int(state[7] or 0),
            "error_summary": state[8] or "",
        }
    return {
        "configured": True,
        "connected": True,
        "documents": int(counts[0] or 0),
        "chunks": int(counts[1] or 0),
        "index_state": index_state,
        "message": "",
    }


def list_knowledge_documents(limit: int = 200) -> list[dict]:
    try:
        with _connect() as connection:
            ensure_knowledge_schema(connection)
            rows = connection.execute(
                """
                SELECT
                    document_id,
                    file_name,
                    title,
                    page_count,
                    file_size,
                    modified_at,
                    indexed_at,
                    (
                        SELECT COUNT(*)
                        FROM lcdash_knowledge.chunks AS chunks
                        WHERE chunks.document_id = documents.document_id
                    ) AS chunk_count
                FROM lcdash_knowledge.documents AS documents
                ORDER BY title
                LIMIT %s
                """,
                (min(max(limit, 1), 500),),
            ).fetchall()
    except (KnowledgeServiceError, psycopg.Error):
        return []

    return [
        {
            "document_id": int(row[0]),
            "file_name": row[1],
            "title": row[2],
            "page_count": int(row[3] or 0),
            "file_size": int(row[4] or 0),
            "modified_at": row[5].isoformat() if row[5] else "",
            "indexed_at": row[6].isoformat() if row[6] else "",
            "chunk_count": int(row[7] or 0),
        }
        for row in rows
    ]


def _fallback_terms(question: str) -> list[str]:
    stop_words = {
        "about",
        "and",
        "can",
        "does",
        "for",
        "from",
        "how",
        "into",
        "the",
        "this",
        "what",
        "when",
        "where",
        "with",
        "you",
    }
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", question.lower())
    return [term for term in terms if term not in stop_words][:8]


def search_knowledge(question: str, limit: int = 8) -> list[dict]:
    clean_question = (question or "").strip()
    if not clean_question or not knowledge_is_configured():
        return []

    result_limit = min(max(limit, 1), 12)
    try:
        with _connect() as connection:
            ensure_knowledge_schema(connection)
            rows = connection.execute(
                """
                WITH query AS (
                    SELECT websearch_to_tsquery('english', %(question)s) AS value
                )
                SELECT
                    documents.document_id,
                    documents.title,
                    documents.file_name,
                    chunks.page_number,
                    chunks.content,
                    documents.indexed_at,
                    ts_rank_cd(chunks.search_vector, query.value) AS rank
                FROM lcdash_knowledge.chunks AS chunks
                JOIN lcdash_knowledge.documents AS documents
                    ON documents.document_id = chunks.document_id
                CROSS JOIN query
                WHERE chunks.search_vector @@ query.value
                ORDER BY rank DESC, documents.title, chunks.page_number
                LIMIT %(limit)s
                """,
                {
                    "question": clean_question,
                    "limit": result_limit,
                },
            ).fetchall()

            if not rows:
                terms = _fallback_terms(clean_question)
                if terms:
                    or_query = " OR ".join(terms)
                    rows = connection.execute(
                        """
                        WITH query AS (
                            SELECT websearch_to_tsquery('english', %s) AS value
                        )
                        SELECT
                            documents.document_id,
                            documents.title,
                            documents.file_name,
                            chunks.page_number,
                            chunks.content,
                            documents.indexed_at,
                            (
                                ts_rank_cd(chunks.search_vector, query.value)
                                + (
                                    2.0 * ts_rank_cd(
                                        to_tsvector('english', documents.title),
                                        query.value
                                    )
                                )
                            ) AS rank
                        FROM lcdash_knowledge.chunks AS chunks
                        JOIN lcdash_knowledge.documents AS documents
                            ON documents.document_id = chunks.document_id
                        CROSS JOIN query
                        WHERE chunks.search_vector @@ query.value
                        ORDER BY rank DESC, documents.title, chunks.page_number
                        LIMIT %s
                        """,
                        (or_query, result_limit),
                    ).fetchall()
    except (KnowledgeServiceError, psycopg.Error):
        return []

    return [
        {
            "document_id": int(row[0]),
            "title": row[1],
            "file_name": row[2],
            "page_number": int(row[3] or 0),
            "content": row[4],
            "indexed_at": row[5].isoformat() if row[5] else "",
            "rank": round(float(row[6] or 0), 4),
        }
        for row in rows
    ]
