from pathlib import Path
import math
import re

import httpx
import psycopg

from app.config.settings import settings


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "database" / "knowledge_schema.sql"


class KnowledgeServiceError(Exception):
    """Raised when the MAE knowledge library is unavailable."""


def _drive_sync_status(source_dir: str | None = None) -> dict:
    status_path = Path(
        source_dir or settings.knowledge_source_dir
    ) / ".drive-sync-status"
    try:
        lines = status_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"status": "not_reported"}
    values = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if separator:
            values[key.strip()] = value.strip()
    return values or {"status": "not_reported"}


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


def get_knowledge_status(
    library_key: str = "centralsquare",
    source_dir: str | None = None,
) -> dict:
    library_name = (
        "Mindshare" if library_key == "mindshare" else "CentralSquare"
    )
    if not knowledge_is_configured():
        return {
            "configured": False,
            "connected": False,
            "documents": 0,
            "chunks": 0,
            "index_state": {},
            "message": f"The {library_name} knowledge library is not configured.",
            "drive_sync": _drive_sync_status(source_dir),
        }

    try:
        with _connect() as connection:
            ensure_knowledge_schema(connection)
            counts = connection.execute(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM lcdash_knowledge.documents
                        WHERE library_key = %s
                    ),
                    (
                        SELECT COUNT(*)
                        FROM lcdash_knowledge.chunks AS chunks
                        JOIN lcdash_knowledge.documents AS documents
                            ON documents.document_id = chunks.document_id
                        WHERE documents.library_key = %s
                    )
                """,
                (library_key, library_key),
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
                FROM lcdash_knowledge.library_index_state
                WHERE library_key = %s
                """,
                (library_key,),
            ).fetchone()
    except (KnowledgeServiceError, psycopg.Error):
        return {
            "configured": True,
            "connected": False,
            "documents": 0,
            "chunks": 0,
            "index_state": {},
            "message": f"The {library_name} knowledge library is unavailable.",
            "drive_sync": _drive_sync_status(source_dir),
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
        "drive_sync": _drive_sync_status(source_dir),
    }


def list_knowledge_documents(
    limit: int = 200,
    library_key: str = "centralsquare",
) -> list[dict]:
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
                WHERE documents.library_key = %s
                ORDER BY title
                LIMIT %s
                """,
                (library_key, min(max(limit, 1), 500)),
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


def get_document_passages(
    document_name: str,
    *,
    limit: int = 6,
    library_key: str = "centralsquare",
) -> list[dict]:
    normalized = re.sub(r"[^a-z0-9]+", "", str(document_name or "").lower())
    if not normalized:
        return []
    try:
        with _connect() as connection:
            ensure_knowledge_schema(connection)
            rows = connection.execute(
                """
                SELECT
                    documents.document_id,
                    documents.title,
                    documents.file_name,
                    chunks.page_number,
                    chunks.content,
                    documents.indexed_at
                FROM lcdash_knowledge.documents AS documents
                JOIN lcdash_knowledge.chunks AS chunks
                    ON chunks.document_id = documents.document_id
                WHERE documents.library_key = %s
                  AND regexp_replace(
                        lower(documents.title || ' ' || documents.file_name),
                        '[^a-z0-9]',
                        '',
                        'g'
                      ) LIKE %s
                ORDER BY chunks.page_number, chunks.chunk_index
                LIMIT %s
                """,
                (
                    library_key,
                    f"%{normalized}%",
                    min(max(limit, 1), 30),
                ),
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
            "rank": 1.0,
            "matched_terms": [normalized],
            "query_terms": [normalized],
            "coverage": 1.0,
            "semantic_score": 0.0,
            "retrieval": ["exact-document"],
            "hybrid_score": 1.0,
        }
        for row in rows
    ]


def _fallback_terms(question: str) -> list[str]:
    stop_words = {
        "about",
        "and",
        "can",
        "configuration",
        "configure",
        "does",
        "for",
        "from",
        "how",
        "into",
        "option",
        "procedure",
        "set",
        "setting",
        "settings",
        "setup",
        "steps",
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


def _query_embedding(question: str) -> list[float]:
    if not settings.mae_embedding_model:
        return []
    try:
        response = httpx.post(
            f"{settings.ollama_base_url.rstrip('/')}/api/embed",
            json={
                "model": settings.mae_embedding_model,
                "input": question,
                "truncate": True,
            },
            timeout=settings.mae_embedding_timeout_seconds,
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings") or []
        if not embeddings:
            return []
        return [float(value) for value in embeddings[0]]
    except (httpx.HTTPError, ValueError, TypeError, IndexError):
        return []


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_length = math.sqrt(sum(value * value for value in left))
    right_length = math.sqrt(sum(value * value for value in right))
    if not left_length or not right_length:
        return 0.0
    return numerator / (left_length * right_length)


def _semantic_results(
    question: str,
    limit: int,
    library_key: str = "centralsquare",
) -> list[dict]:
    query_embedding = _query_embedding(question)
    if not query_embedding:
        return []
    candidate_limit = min(
        max(settings.knowledge_semantic_candidates, limit * 20),
        5000,
    )
    try:
        with _connect() as connection:
            ensure_knowledge_schema(connection)
            rows = connection.execute(
                """
                SELECT
                    documents.document_id,
                    documents.title,
                    documents.file_name,
                    chunks.page_number,
                    chunks.content,
                    documents.indexed_at,
                    chunks.embedding
                FROM lcdash_knowledge.chunks AS chunks
                JOIN lcdash_knowledge.documents AS documents
                    ON documents.document_id = chunks.document_id
                WHERE chunks.embedding IS NOT NULL
                  AND chunks.embedding_model = %s
                  AND documents.library_key = %s
                ORDER BY chunks.chunk_id DESC
                LIMIT %s
                """,
                (
                    settings.mae_embedding_model,
                    library_key,
                    candidate_limit,
                ),
            ).fetchall()
    except (KnowledgeServiceError, psycopg.Error):
        return []

    results = []
    for row in rows:
        similarity = _cosine_similarity(query_embedding, list(row[6] or []))
        if similarity <= 0:
            continue
        results.append(
            {
                "document_id": int(row[0]),
                "title": row[1],
                "file_name": row[2],
                "page_number": int(row[3] or 0),
                "content": row[4],
                "indexed_at": row[5].isoformat() if row[5] else "",
                "semantic_score": round(similarity, 5),
                "retrieval": ["semantic"],
            }
        )
    results.sort(key=lambda item: item["semantic_score"], reverse=True)
    return results[: max(limit * 3, 12)]


def search_knowledge(
    question: str,
    limit: int = 8,
    library_key: str = "centralsquare",
) -> list[dict]:
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
                  AND documents.library_key = %(library_key)s
                ORDER BY rank DESC, documents.title, chunks.page_number
                LIMIT %(limit)s
                """,
                {
                    "question": clean_question,
                    "library_key": library_key,
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
                          AND documents.library_key = %s
                        ORDER BY rank DESC, documents.title, chunks.page_number
                        LIMIT %s
                        """,
                        (or_query, library_key, result_limit * 5),
                    ).fetchall()
    except (KnowledgeServiceError, psycopg.Error):
        return []

    query_terms = _fallback_terms(clean_question)
    ranked_results = []
    for row in rows:
        searchable_text = f"{row[1]} {row[4]}".lower()
        matched_terms = [
            term for term in query_terms if term in searchable_text
        ]
        coverage = (
            len(matched_terms) / len(query_terms)
            if query_terms
            else 1.0
        )
        ranked_results.append(
            {
            "document_id": int(row[0]),
            "title": row[1],
            "file_name": row[2],
            "page_number": int(row[3] or 0),
            "content": row[4],
            "indexed_at": row[5].isoformat() if row[5] else "",
            "rank": round(float(row[6] or 0), 4),
            "matched_terms": matched_terms,
            "query_terms": query_terms,
            "coverage": round(coverage, 4),
            "semantic_score": 0.0,
            "retrieval": ["keyword"],
        }
        )

    combined: dict[tuple[int, int, str], dict] = {}
    for result in ranked_results:
        key = (
            result["document_id"],
            result["page_number"],
            result["content"][:120],
        )
        combined[key] = result

    for semantic in _semantic_results(
        clean_question,
        result_limit,
        library_key,
    ):
        key = (
            semantic["document_id"],
            semantic["page_number"],
            semantic["content"][:120],
        )
        existing = combined.get(key)
        if existing:
            existing["semantic_score"] = semantic["semantic_score"]
            existing["retrieval"] = ["keyword", "semantic"]
        else:
            semantic["rank"] = 0.0
            semantic["matched_terms"] = []
            semantic["query_terms"] = query_terms
            semantic["coverage"] = 0.0
            combined[key] = semantic

    final_results = list(combined.values())
    for result in final_results:
        result["hybrid_score"] = round(
            (float(result.get("coverage") or 0) * 0.45)
            + (min(float(result.get("rank") or 0), 1.0) * 0.15)
            + (float(result.get("semantic_score") or 0) * 0.40),
            5,
        )
    final_results.sort(
        key=lambda result: (
            result["hybrid_score"],
            result["coverage"],
            len(result["matched_terms"]),
        ),
        reverse=True,
    )
    return final_results[:result_limit]
