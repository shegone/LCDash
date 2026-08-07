"""Cloud knowledge-library documents, served directly from S3.

The 164 approved documents (131 Mindshare, 33 CentralSquare) already live in
the same two S3 prefixes the Bedrock Knowledge Base retrieves from --
``settings.cloud_ai_allowed_s3_prefixes`` is the one source of truth for
both. There is no manifest file: an earlier reviewed design anticipated one
at a ``manifests/approved/`` prefix, but that prefix is empty in the real
bucket, so this module lists objects directly instead.

Read-only. No write/delete action exists anywhere in this module -- the
IAM grant behind it (``_grant_document_library_read`` in
``foundation_stack.py``) only ever includes ``s3:ListBucket``/``s3:GetObject``.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Iterator, Protocol
from urllib.parse import urlsplit

from app.config.settings import Settings


class S3DocumentClient(Protocol):
    def get_paginator(self, operation_name: str) -> Any: ...
    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...


class LazyS3DocumentClient:
    """Create the S3 client only on the first document-library request."""

    def __init__(self, *, region_name: str = "us-east-1") -> None:
        self._region_name = region_name

    @cached_property
    def _client(self):
        import boto3

        return boto3.client("s3", region_name=self._region_name)

    def get_paginator(self, operation_name: str) -> Any:
        return self._client.get_paginator(operation_name)

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        return self._client.get_object(**kwargs)


@dataclass(frozen=True, slots=True)
class LibraryDocument:
    document_id: str
    title: str
    relative_path: str
    size_bytes: int


def _parse_allowed_prefixes(prefixes: tuple[str, ...]) -> dict[str, tuple[str, str]]:
    """Map ``library_key`` -> ``(bucket, prefix)`` from the approved S3 URIs.

    The library key is the path segment immediately after
    ``document-library/`` in each configured prefix, e.g. ``mindshare`` or
    ``centralsquare``. A prefix that doesn't match this shape is skipped
    rather than raising, so an unrelated future prefix can't break startup.
    """
    mapping: dict[str, tuple[str, str]] = {}
    marker = "document-library/"
    for uri in prefixes:
        parsed = urlsplit(uri)
        if parsed.scheme != "s3" or not parsed.netloc:
            continue
        path = parsed.path.lstrip("/")
        marker_index = path.find(marker)
        if marker_index < 0:
            continue
        remainder = path[marker_index + len(marker):]
        library_key = remainder.split("/", 1)[0]
        if not library_key:
            continue
        mapping[library_key] = (parsed.netloc, path)
    return mapping


def _encode_document_id(relative_path: str) -> str:
    return base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")


def _decode_document_id(document_id: str) -> str | None:
    padded = document_id + "=" * (-len(document_id) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def content_disposition_header(filename: str, *, download: bool) -> str:
    """Build a Content-Disposition value safe against header injection.

    ``filename`` ultimately derives from an S3 object key. Even though that
    key currently always comes from the reviewed 164-document set, this
    still strips CR/LF and escapes quotes defensively rather than trusting
    the source, since this is the first place in the app that interpolates
    a data-derived value into a raw header string.
    """
    disposition = "attachment" if download else "inline"
    safe = "".join(char for char in filename if char not in "\r\n\"").strip() or "document.pdf"
    return f'{disposition}; filename="{safe}"'


def _title_from_relative_path(relative_path: str) -> str:
    filename = relative_path.rsplit("/", 1)[-1]
    if filename.lower().endswith(".pdf"):
        filename = filename[:-4]
    return filename


class CloudDocumentLibraryUnavailable(RuntimeError):
    """Sanitized failure category; never carries a provider payload."""


class CloudDocumentLibrary:
    """List and fetch approved PDFs directly from the reviewed S3 prefixes."""

    def __init__(self, *, client: S3DocumentClient, settings: Settings) -> None:
        self._client = client
        self._libraries = _parse_allowed_prefixes(settings.cloud_ai_allowed_s3_prefixes)

    def available(self, library_key: str) -> bool:
        return library_key in self._libraries

    def list_documents(self, library_key: str) -> tuple[LibraryDocument, ...]:
        target = self._libraries.get(library_key)
        if target is None:
            return ()
        bucket, prefix = target
        documents: list[LibraryDocument] = []
        try:
            for key, size in self._iter_pdf_keys(bucket, prefix):
                relative_path = key[len(prefix):]
                if not relative_path:
                    continue
                documents.append(
                    LibraryDocument(
                        document_id=_encode_document_id(relative_path),
                        title=_title_from_relative_path(relative_path),
                        relative_path=relative_path,
                        size_bytes=size,
                    )
                )
        except Exception as exc:  # provider payloads never leave this frame
            raise CloudDocumentLibraryUnavailable("document_library_list_failed") from exc
        documents.sort(key=lambda document: document.relative_path.lower())
        return tuple(documents)

    def _iter_pdf_keys(self, bucket: str, prefix: str) -> Iterator[tuple[str, int]]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", ()):
                key = str(item.get("Key") or "")
                if key.lower().endswith(".pdf"):
                    yield key, int(item.get("Size") or 0)

    def fetch_document(
        self, library_key: str, document_id: str
    ) -> tuple[bytes, str] | None:
        """Return ``(pdf_bytes, filename)``, or None if not found/invalid.

        The document_id is decoded back to a path relative to the library's
        fixed, reviewed prefix and nowhere else -- a malformed or crafted
        document_id can only ever resolve within that one prefix, never
        outside it, because the full key is always
        ``prefix + relative_path`` and the IAM grant itself is prefix-scoped.
        """
        target = self._libraries.get(library_key)
        if target is None:
            return None
        relative_path = _decode_document_id(document_id)
        if not relative_path or relative_path.startswith("/") or ".." in relative_path.split("/"):
            return None
        bucket, prefix = target
        key = f"{prefix}{relative_path}"
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
        except Exception:
            return None
        body = response.get("Body")
        payload = body.read() if hasattr(body, "read") else body
        if not payload:
            return None
        filename = _title_from_relative_path(relative_path) + ".pdf"
        return bytes(payload), filename


def build_cloud_document_library(settings: Settings) -> CloudDocumentLibrary:
    """Construct the library with no provider call at import/startup time."""
    return CloudDocumentLibrary(client=LazyS3DocumentClient(), settings=settings)
