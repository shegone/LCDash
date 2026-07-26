from datetime import datetime, timezone
import hashlib
from pathlib import Path
import re

import httpx
import psycopg
from pypdf import PdfReader

from app.config.settings import settings
from app.services.knowledge_service import ensure_knowledge_schema


CHUNK_SIZE = 1400
CHUNK_OVERLAP = 180
EMBEDDING_BATCH_SIZE = 24


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _page_chunks(text: str) -> list[str]:
    clean_text = _normalize_text(text)
    if not clean_text:
        return []
    chunks = []
    start = 0
    while start < len(clean_text):
        end = min(start + CHUNK_SIZE, len(clean_text))
        if end < len(clean_text):
            sentence_break = max(
                clean_text.rfind(". ", start, end),
                clean_text.rfind("; ", start, end),
            )
            if sentence_break > start + (CHUNK_SIZE // 2):
                end = sentence_break + 1
        chunks.append(clean_text[start:end].strip())
        if end >= len(clean_text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts or not settings.mae_embedding_model:
        return []
    response = httpx.post(
        f"{settings.ollama_base_url.rstrip('/')}/api/embed",
        json={
            "model": settings.mae_embedding_model,
            "input": texts,
            "truncate": True,
        },
        timeout=settings.mae_embedding_timeout_seconds,
    )
    response.raise_for_status()
    embeddings = response.json().get("embeddings") or []
    if len(embeddings) != len(texts):
        return []
    return [
        [float(value) for value in embedding]
        for embedding in embeddings
    ]


def _embed_chunk_rows(connection, chunk_rows: list[tuple[int, str]]) -> None:
    for start in range(0, len(chunk_rows), EMBEDDING_BATCH_SIZE):
        batch = chunk_rows[start:start + EMBEDDING_BATCH_SIZE]
        try:
            embeddings = _embed_texts([item[1] for item in batch])
        except (httpx.HTTPError, ValueError, TypeError):
            embeddings = []
        if not embeddings:
            break
        for (chunk_id, _), embedding in zip(batch, embeddings):
            connection.execute(
                """
                UPDATE lcdash_knowledge.chunks
                SET embedding = %s,
                    embedding_model = %s
                WHERE chunk_id = %s
                """,
                (embedding, settings.mae_embedding_model, chunk_id),
            )
        connection.commit()


def _index_document(connection, source_root: Path, path: Path) -> tuple[str, int]:
    relative_path = path.relative_to(source_root).as_posix()
    content_hash = _sha256(path)
    existing = connection.execute(
        """
        SELECT document_id, content_hash
        FROM lcdash_knowledge.documents
        WHERE source_path = %s
        """,
        (relative_path,),
    ).fetchone()
    if existing and existing[1] == content_hash:
        missing_embedding_rows = connection.execute(
            """
            SELECT chunk_id, content
            FROM lcdash_knowledge.chunks
            WHERE document_id = %s
              AND (
                    embedding IS NULL
                    OR embedding_model <> %s
              )
            ORDER BY chunk_id
            """,
            (existing[0], settings.mae_embedding_model),
        ).fetchall()
        _embed_chunk_rows(
            connection,
            [(int(row[0]), row[1]) for row in missing_embedding_rows],
        )
        return "unchanged", 0

    reader = PdfReader(str(path), strict=False)
    document_id = connection.execute(
        """
        INSERT INTO lcdash_knowledge.documents (
            source_path,
            file_name,
            title,
            content_hash,
            file_size,
            modified_at,
            page_count,
            indexed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (source_path) DO UPDATE SET
            file_name = EXCLUDED.file_name,
            title = EXCLUDED.title,
            content_hash = EXCLUDED.content_hash,
            file_size = EXCLUDED.file_size,
            modified_at = EXCLUDED.modified_at,
            page_count = EXCLUDED.page_count,
            indexed_at = NOW()
        RETURNING document_id
        """,
        (
            relative_path,
            path.name,
            path.stem,
            content_hash,
            path.stat().st_size,
            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
            len(reader.pages),
        ),
    ).fetchone()[0]
    connection.execute(
        "DELETE FROM lcdash_knowledge.chunks WHERE document_id = %s",
        (document_id,),
    )

    stored_chunks = 0
    chunk_rows: list[tuple[int, str]] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        for chunk_index, content in enumerate(_page_chunks(page_text)):
            row = connection.execute(
                """
                INSERT INTO lcdash_knowledge.chunks (
                    document_id,
                    page_number,
                    chunk_index,
                    content
                )
                VALUES (%s, %s, %s, %s)
                RETURNING chunk_id
                """,
                (document_id, page_number, chunk_index, content),
            ).fetchone()
            chunk_rows.append((int(row[0]), content))
            stored_chunks += 1

    _embed_chunk_rows(connection, chunk_rows)
    return "indexed", stored_chunks


def run_index() -> dict:
    source_root = Path(settings.knowledge_source_dir)
    source_root.mkdir(parents=True, exist_ok=True)
    pdf_paths = sorted(
        path for path in source_root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"
    )
    stats = {
        "documents_found": len(pdf_paths),
        "documents_indexed": 0,
        "documents_unchanged": 0,
        "documents_failed": 0,
        "chunks_stored": 0,
        "errors": [],
    }

    with psycopg.connect(settings.database_url, connect_timeout=10) as connection:
        ensure_knowledge_schema(connection)
        connection.execute(
            """
            UPDATE lcdash_knowledge.index_state
            SET status = 'running',
                started_at = NOW(),
                error_summary = ''
            WHERE state_id = TRUE
            """
        )
        connection.commit()

        present_paths = {
            path.relative_to(source_root).as_posix()
            for path in pdf_paths
        }
        for path in pdf_paths:
            try:
                outcome, chunks = _index_document(connection, source_root, path)
                if outcome == "indexed":
                    stats["documents_indexed"] += 1
                    stats["chunks_stored"] += chunks
                else:
                    stats["documents_unchanged"] += 1
                connection.commit()
            except Exception as exc:
                connection.rollback()
                stats["documents_failed"] += 1
                stats["errors"].append(f"{path.name}: {exc}")

        existing_paths = connection.execute(
            "SELECT source_path FROM lcdash_knowledge.documents"
        ).fetchall()
        removed_paths = [
            row[0] for row in existing_paths if row[0] not in present_paths
        ]
        for removed_path in removed_paths:
            connection.execute(
                "DELETE FROM lcdash_knowledge.documents WHERE source_path = %s",
                (removed_path,),
            )

        status = "complete" if not stats["documents_failed"] else "partial"
        connection.execute(
            """
            UPDATE lcdash_knowledge.index_state
            SET status = %s,
                completed_at = NOW(),
                documents_found = %s,
                documents_indexed = %s,
                documents_unchanged = %s,
                documents_failed = %s,
                chunks_stored = (
                    SELECT COUNT(*) FROM lcdash_knowledge.chunks
                ),
                error_summary = %s
            WHERE state_id = TRUE
            """,
            (
                status,
                stats["documents_found"],
                stats["documents_indexed"],
                stats["documents_unchanged"],
                stats["documents_failed"],
                "\n".join(stats["errors"][:20]),
            ),
        )
        connection.commit()

    return stats


if __name__ == "__main__":
    print(run_index())
