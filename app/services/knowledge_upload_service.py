"""Admin PDF uploads into the Bedrock Knowledge Base's S3 prefixes.

Exactly two upload destinations exist, and their prefix shapes are load-bearing
rather than aesthetic:

- ``mae``  -> ``tenants/<tenant>/document-library/mae-uploads/current/``
- ``jack`` -> ``tenants/<tenant>/document-library/jack-uploads/mindshare/current/``

(The concrete per-county values live in ``settings.knowledge_upload_*`` --
settings is the one sanctioned home for tenant-specific strings.)

Two constraints forced these shapes. First, the persona filter in
``app/integrations/cloud_ai/bedrock_retrieval.py`` (``_persona_prefixes``,
lines 81-84) confines JACK to prefixes containing ``/mindshare/`` -- so JACK's
upload prefix must carry that segment or JACK would never retrieve from it,
while MAE is allowed everything and sees both destinations. Second, the
library-key parser in ``cloud_document_library._parse_allowed_prefixes`` keys
each library on the path segment immediately after ``document-library/``. A
tempting ``document-library/mindshare/cloud-uploads/`` prefix would therefore
produce the key ``mindshare`` and silently clobber the approved 131-document
mindshare library mapping. Putting ``jack-uploads`` (not ``mindshare``)
directly after ``document-library/`` keeps the upload libraries' keys disjoint
from the reviewed ones.

Removal caveat, stated once here and honored by every caller: the
document-library bucket is UNVERSIONED (``document_library_stack.py`` sets
``versioned=False``), so ``remove_document`` is permanent. The Knowledge
Base's vectors for a removed PDF only disappear at the next ingestion sync;
until that sync runs, the assistant can still cite a document that no longer
exists, and callers must surface that honestly instead of implying instant
forgetting.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from app.services.cloud_document_library import _decode_document_id, _encode_document_id
from app.tools.document_intake_gate import MAX_FILE_BYTES

logger = logging.getLogger(__name__)

# No bucket/prefix defaults here: county-specific values are settings.py's
# job (the county-profile contract test forbids hardcoding a tenant in app
# code), so callers pass settings.knowledge_upload_bucket and the two
# settings.knowledge_upload_*_prefix values explicitly.

# The 25 MB cap is inherited from the historical intake gate
# (app/tools/document_intake_gate.py MAX_FILE_BYTES) rather than restated,
# so the in-app upload path and the local gate can never drift apart.
_MAX_UPLOAD_BYTES = MAX_FILE_BYTES

# PDF magic bytes. Extension checks trust the sender's naming; the file's own
# first bytes do not. Every real PDF version starts "%PDF-".
_PDF_MAGIC = b"%PDF-"

_MAX_FILENAME_LENGTH = 120
_DISALLOWED_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class KnowledgeUploadError(ValueError):
    """A refused upload/removal/sync, with a message safe to show the admin."""


def _aws_error_code(error: Exception) -> str:
    """Read the AWS error code off a client exception, if it has one.

    Duck-typed on ``.response["Error"]["Code"]`` instead of importing
    botocore's ClientError, so tests can raise a plain stub exception with the
    same shape (same rationale as pilot_access_service._aws_error_code).
    """
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code", "")
        return str(code or "")
    return ""


def _ascii_only(value: str) -> str:
    # S3 object metadata values must survive an HTTP header round-trip;
    # non-ASCII characters get mangled or rejected, so strip them here rather
    # than letting put_object fail on an accented display name.
    return "".join(char for char in str(value or "") if char.isascii()).strip()


def _sanitize_filename(filename: str) -> str:
    """Reduce an admin-supplied filename to a safe S3 basename, or raise.

    Path components (both separators -- admins upload from Windows) are
    stripped first so ``..\\evil.pdf`` can never influence the object key;
    the key is always ``prefix + basename`` and nothing else. Whitespace runs
    collapse to ``-``, everything outside [A-Za-z0-9._-] is dropped, and the
    result must keep a non-empty stem under a normalized ``.pdf`` suffix.
    """
    basename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    collapsed = re.sub(r"\s+", "-", basename)
    cleaned = _DISALLOWED_CHARS.sub("", collapsed)
    if cleaned.lower().endswith(".pdf"):
        stem = cleaned[:-4]
    else:
        stem = cleaned
    stem = stem.strip("-. ")
    if not stem:
        raise KnowledgeUploadError(
            "That filename has no usable characters after sanitization. "
            "Rename the file and try again."
        )
    name = stem + ".pdf"
    if len(name) > _MAX_FILENAME_LENGTH:
        name = stem[: _MAX_FILENAME_LENGTH - 4] + ".pdf"
    return name


class KnowledgeUploadService:
    """Upload, list, and remove admin PDFs, and drive Knowledge Base syncs.

    Authorization happens before this class -- only a pilot admin reaches the
    endpoints that call it. What lives here are the content guardrails: real
    PDFs only, the historical size cap, sanitized names, and no silent
    overwrites of a document another admin already placed.
    """

    def __init__(
        self,
        bucket: str,
        destinations: Mapping[str, str],
        *,
        region: str = "us-east-1",
        s3_client=None,
        bedrock_client=None,
        knowledge_base_id: str = "",
        data_source_id: str = "",
    ) -> None:
        if not bucket.strip() or not destinations:
            raise ValueError("Knowledge uploads require a bucket and destinations.")
        self._bucket = bucket
        self._destinations = dict(destinations)
        self._region = region
        # Injected in tests; built lazily otherwise (same rationale as
        # LazyS3DocumentClient in cloud_document_library.py: boto3 clients
        # resolve credentials on construction, and requests that never touch
        # uploads should never pay that cost).
        self._s3 = s3_client
        self._bedrock = bedrock_client
        self._knowledge_base_id = knowledge_base_id
        self._data_source_id = data_source_id

    @property
    def s3(self):
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3", region_name=self._region)
        return self._s3

    @property
    def bedrock(self):
        if self._bedrock is None:
            import boto3

            self._bedrock = boto3.client("bedrock-agent", region_name=self._region)
        return self._bedrock

    # ---------------------------------------------------------------- uploads

    def upload_document(
        self, destination: str, filename: str, content: bytes, *, uploaded_by: str
    ) -> dict:
        prefix = self._prefix_for(destination)
        if not content:
            raise KnowledgeUploadError("The uploaded file is empty.")
        if len(content) > _MAX_UPLOAD_BYTES:
            raise KnowledgeUploadError(
                "The file exceeds the 25 MB document limit "
                "(the intake gate's historical cap)."
            )
        if not content.startswith(_PDF_MAGIC):
            # The magic-byte check, not the extension, decides: a renamed
            # .exe or .docx must not enter a prefix the KB ingests from.
            raise KnowledgeUploadError(
                "That file is not a PDF. Only PDF documents can be uploaded "
                "to the knowledge library."
            )
        safe_name = _sanitize_filename(filename)
        key = prefix + safe_name

        # No silent overwrite: another admin's document with the same name is
        # a document, not a slot. head_object failure means the name is free.
        if self._object_exists(key):
            raise KnowledgeUploadError(
                f"A document named {safe_name} already exists in this "
                "destination. Remove the old one first, or rename yours."
            )

        self.s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content,
            ContentType="application/pdf",
            ServerSideEncryption="AES256",
            Metadata={
                "uploaded-by": _ascii_only(uploaded_by),
                "destination": destination,
            },
        )
        logger.info(
            "knowledge-upload upload_document destination=%s key=%s "
            "size_bytes=%d uploaded_by=%s",
            destination, key, len(content), uploaded_by,
        )
        return {
            "document_id": _encode_document_id(safe_name),
            "filename": safe_name,
            "destination": destination,
            "size_bytes": len(content),
            "uploaded_by": uploaded_by,
        }

    def _object_exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self._bucket, Key=key)
        except Exception as error:
            code = _aws_error_code(error)
            if code not in ("404", "NoSuchKey", "NotFound", ""):
                # An unexpected head failure is not proof of absence, but the
                # put itself will surface any real access problem; log it and
                # let the upload proceed rather than blocking on a read quirk.
                logger.warning("knowledge-upload head_object failed key=%s: %s", key, error)
            return False
        return True

    # ---------------------------------------------------------------- listing

    def list_documents(self, destination: str | None = None) -> list[dict]:
        if destination is not None:
            targets = [(destination, self._prefix_for(destination))]
        else:
            targets = list(self._destinations.items())
        records: list[tuple[Any, dict]] = []
        try:
            paginator = self.s3.get_paginator("list_objects_v2")
            for name, prefix in targets:
                for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                    for item in page.get("Contents", ()):
                        key = str(item.get("Key") or "")
                        if not key.lower().endswith(".pdf"):
                            continue
                        relative = key[len(prefix):]
                        if not relative:
                            continue
                        last_modified = item.get("LastModified")
                        records.append((
                            last_modified,
                            {
                                "document_id": _encode_document_id(relative),
                                "filename": relative.rsplit("/", 1)[-1],
                                "destination": name,
                                "size_bytes": int(item.get("Size") or 0),
                                "last_modified": (
                                    last_modified.isoformat()
                                    if last_modified is not None else ""
                                ),
                                "uploaded_by": self._uploaded_by(key),
                            },
                        ))
        except Exception as error:
            # Sanitized: boto error payloads never reach the admin page, but
            # the real cause must land in the logs or nobody can debug it.
            logger.warning("knowledge-upload list failed: %s", error)
            raise KnowledgeUploadError("Uploaded document listing failed.") from error
        records.sort(key=lambda pair: pair[1]["last_modified"], reverse=True)
        return [record for _stamp, record in records]

    def _uploaded_by(self, key: str) -> str:
        # Head only the objects actually being returned, and tolerate a head
        # failure as an empty attribution -- a missing name must not take the
        # whole listing down with it.
        try:
            head = self.s3.head_object(Bucket=self._bucket, Key=key)
        except Exception:
            return ""
        metadata = head.get("Metadata") or {}
        return str(metadata.get("uploaded-by") or "")

    # ---------------------------------------------------------------- removal

    def remove_document(
        self, destination: str, document_id: str, *, removed_by: str
    ) -> dict:
        """Permanently delete an uploaded PDF from its destination prefix.

        Permanent because the bucket is unversioned (document_library_stack.py
        ``versioned=False``): there is no recycle bin. The KB keeps serving
        the document's vectors until the next ingestion sync, so callers must
        tell the admin the assistant may still cite it in the meantime.
        """
        prefix = self._prefix_for(destination)
        relative = _decode_document_id(document_id)
        # Same defense as cloud_document_library.fetch_document: a crafted id
        # is rejected on shape alone, before any S3 call, so the resolved key
        # can only ever be prefix + a plain relative path.
        if (
            relative is None
            or not relative
            or relative.startswith("/")
            or ".." in relative
        ):
            raise KnowledgeUploadError("That document id is not valid.")
        if not relative.lower().endswith(".pdf"):
            raise KnowledgeUploadError("That document id is not valid.")
        key = prefix + relative
        try:
            self.s3.head_object(Bucket=self._bucket, Key=key)
        except Exception as error:
            # Removing a document that does not exist is an error, not a
            # silent success -- the admin needs to know their mental model of
            # the library is stale before they trust the removal happened.
            raise KnowledgeUploadError(
                "That document was not found; it may already have been removed."
            ) from error
        self.s3.delete_object(Bucket=self._bucket, Key=key)
        filename = relative.rsplit("/", 1)[-1]
        logger.info(
            "knowledge-upload remove_document destination=%s key=%s removed_by=%s",
            destination, key, removed_by,
        )
        return {"removed": True, "filename": filename}

    # -------------------------------------------------------------- ingestion

    def start_ingestion(self) -> dict:
        """Start a Knowledge Base ingestion sync over the data source."""
        self._require_ingestion_config()
        try:
            response = self.bedrock.start_ingestion_job(
                knowledgeBaseId=self._knowledge_base_id,
                dataSourceId=self._data_source_id,
            )
        except Exception as error:
            if _aws_error_code(error) == "ConflictException":
                # Bedrock allows one running job per data source; surface
                # that as a plain sentence instead of a stack trace.
                raise KnowledgeUploadError(
                    "An ingestion sync is already running; wait for it to finish."
                ) from error
            raise
        job = response.get("ingestionJob", {})
        job_id = str(job.get("ingestionJobId") or "")
        status = str(job.get("status") or "")
        logger.info("knowledge-upload start_ingestion job_id=%s status=%s", job_id, status)
        return {"job_id": job_id, "status": status}

    def ingestion_status(self) -> dict:
        """Status of the most recent ingestion job, or NEVER_RUN when none."""
        self._require_ingestion_config()
        response = self.bedrock.list_ingestion_jobs(
            knowledgeBaseId=self._knowledge_base_id,
            dataSourceId=self._data_source_id,
            maxResults=1,
            sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        )
        summaries = response.get("ingestionJobSummaries") or []
        if not summaries:
            return {"job_id": "", "status": "NEVER_RUN"}
        newest = summaries[0]
        started = newest.get("startedAt")
        statistics = newest.get("statistics")
        return {
            "job_id": str(newest.get("ingestionJobId") or ""),
            "status": str(newest.get("status") or ""),
            "started_at": started.isoformat() if hasattr(started, "isoformat") else (started or ""),
            "statistics": dict(statistics) if isinstance(statistics, Mapping) else {},
        }

    # ----------------------------------------------------------------- guards

    def _prefix_for(self, destination: str) -> str:
        prefix = self._destinations.get(str(destination or ""))
        if prefix is None:
            known = ", ".join(sorted(self._destinations))
            raise KnowledgeUploadError(
                f"Unknown upload destination. Choose one of: {known}."
            )
        return prefix

    def _require_ingestion_config(self) -> None:
        if not self._knowledge_base_id.strip() or not self._data_source_id.strip():
            raise KnowledgeUploadError(
                "Knowledge Base ingestion is not configured "
                "(missing knowledge base or data source id)."
            )
