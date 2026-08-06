"""Deterministic, local-only preparation of explicitly approved documents.

This module performs no discovery, network access, upload, or RAG activation.
Only files individually listed in a review manifest are considered.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
from xml.etree import ElementTree
from zipfile import ZipFile

from app.tools.document_intake_gate import (
    ALLOWED_SUFFIXES,
    FORBIDDEN_SUFFIXES,
    MAX_FILE_BYTES,
    matches_forbidden_path_term,
)

SCHEMA_VERSION = "lcdash.document-preparation.v1"
APPROVED_SCOPES = {
    "centralsquare-current": {".pdf"},
    "mindshare-current": ALLOWED_SUFFIXES,
    "mindshare-sanitized-system": ALLOWED_SUFFIXES,
    "mindshare-software-catalog": {".json", ".csv"},
}
WORD = re.compile(r"[A-Za-z0-9]+")


class PreparationError(ValueError):
    pass


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_bytes(path: Path) -> tuple[bytes, str]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise PreparationError("file exceeds the 25 MiB limit")
    data = path.read_bytes()
    return data, hashlib.sha256(data).hexdigest()


def _extract(path: Path, data: bytes) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        with ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml)
        return "\n".join(t.text or "" for t in root.iter() if t.tag.endswith("}t"))
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise PreparationError("PDF extraction requires the local pypdf package") from exc
        return "\n\f\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    if suffix in {".txt", ".md", ".json", ".xml", ".csv", ".yaml", ".yml"}:
        return data.decode("utf-8")
    raise PreparationError("unsupported document type")


def _chunks(text: str, document_hash: str, size: int = 2400, overlap: int = 240) -> list[dict[str, Any]]:
    normalized = "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()
    if not normalized:
        return []
    if size < 200 or overlap < 0 or overlap >= size:
        raise PreparationError("invalid chunk settings")
    result = []
    start = 0
    index = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        value = normalized[start:end].strip()
        if value:
            chunk_id = hashlib.sha256(f"{document_hash}:{index}:{value}".encode()).hexdigest()
            result.append({"chunk_id": chunk_id, "index": index, "text": value})
            index += 1
        if end == len(normalized):
            break
        start = max(end - overlap, start + 1)
    return result


def _feature_embedding(text: str, dimensions: int = 64) -> list[float]:
    """Model-free local feature hash, useful only for deterministic pipeline tests."""
    vector = [0.0] * dimensions
    for token in WORD.findall(text.lower()):
        digest = hashlib.sha256(token.encode()).digest()
        slot = int.from_bytes(digest[:4], "big") % dimensions
        vector[slot] += -1.0 if digest[4] & 1 else 1.0
    norm = sum(value * value for value in vector) ** 0.5
    return [round(value / norm, 8) for value in vector] if norm else vector


def prepare_documents(
    request_path: str | Path,
    *,
    repository_root: str | Path,
    source_repository_root: str | Path | None = None,
    include_local_embeddings: bool = False,
) -> dict[str, Any]:
    """Prepare explicitly listed documents and return a review-only manifest."""
    root = Path(repository_root).resolve(strict=True)
    request = Path(request_path).resolve(strict=True)
    if not _inside(root, request):
        raise PreparationError("request must be inside the repository")
    payload = json.loads(request.read_text(encoding="utf-8"))
    if set(payload) != {"schema_version", "source_root", "documents"}:
        raise PreparationError("request fields are incomplete or unknown")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise PreparationError("unsupported schema version")
    source_rel = PurePosixPath(str(payload["source_root"]))
    if source_rel.is_absolute() or ".." in source_rel.parts:
        raise PreparationError("source_root is unsafe")
    source_base = (
        Path(source_repository_root).resolve(strict=True)
        if source_repository_root is not None
        else root
    )
    source_root = (source_base / Path(*source_rel.parts)).resolve(strict=True)
    if not _inside(source_base, source_root):
        raise PreparationError("source_root escapes the explicitly allowed source repository")
    if not isinstance(payload["documents"], list):
        raise PreparationError("documents must be a list")

    eligible, rejected = [], []
    for entry in payload["documents"]:
        relative = str(entry.get("path", "")) if isinstance(entry, dict) else ""
        scope = str(entry.get("scope", "")) if isinstance(entry, dict) else ""
        approved = entry.get("approved") is True if isinstance(entry, dict) else False
        reason = None
        rel = PurePosixPath(relative)
        lower = relative.lower()
        if not isinstance(entry, dict) or set(entry) != {"path", "scope", "approved", "approval_id"}:
            reason = "invalid-entry-fields"
        elif rel.is_absolute() or ".." in rel.parts or not rel.parts:
            reason = "unsafe-path"
        elif not approved or not str(entry.get("approval_id", "")).strip():
            reason = "approval-not-recorded"
        elif scope not in APPROVED_SCOPES:
            reason = "scope-not-approved"
        elif rel.suffix.lower() not in APPROVED_SCOPES[scope]:
            reason = "type-not-approved-for-scope"
        elif rel.suffix.lower() in FORBIDDEN_SUFFIXES or matches_forbidden_path_term(lower):
            reason = "hard-exclusion"
        path = (source_root / Path(*rel.parts)).resolve()
        if reason is None and (not _inside(source_root, path) or not path.is_file()):
            reason = "source-missing-or-outside-root"
        if reason:
            rejected.append({"path": relative, "reason": reason})
            continue
        try:
            data, digest = _read_bytes(path)
            chunks = _chunks(_extract(path, data), digest)
            if not chunks:
                raise PreparationError("no extractable text")
        except (OSError, UnicodeError, KeyError, ElementTree.ParseError, PreparationError) as exc:
            rejected.append({"path": relative, "reason": f"extraction-failed: {exc}"})
            continue
        if include_local_embeddings:
            for chunk in chunks:
                chunk["local_feature_embedding"] = _feature_embedding(chunk["text"])
        eligible.append({
            "path": relative,
            "scope": scope,
            "approval_id": entry["approval_id"],
            "bytes": len(data),
            "sha256": digest,
            "chunk_count": len(chunks),
            "chunks": chunks,
        })
    eligible.sort(key=lambda item: item["path"].casefold())
    rejected.sort(key=lambda item: item["path"].casefold())
    canonical = {"schema_version": SCHEMA_VERSION, "eligible": eligible, "rejected": rejected}
    canonical["review_manifest_sha256"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    canonical["eligible_count"] = len(eligible)
    canonical["rejected_count"] = len(rejected)
    canonical["upload_authorized"] = False
    canonical["rag_enabled"] = False
    canonical["embeddings"] = "local-feature-hash-test-only" if include_local_embeddings else "disabled"
    return canonical
