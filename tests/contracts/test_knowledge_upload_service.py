"""Contract tests for admin PDF uploads into the Knowledge Base prefixes.

The dangerous states these tests exist to prevent: a renamed executable
entering an S3 prefix the Knowledge Base ingests from, a crafted filename or
document id escaping the destination prefix, one admin's upload silently
clobbering another's, and a removal that reports success for a document that
was never there. The destination prefixes themselves are load-bearing (JACK's
persona filter and the library-key parser both constrain their shape -- see
the service module docstring), so the defaults are pinned here too.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.services.cloud_document_library import _encode_document_id
from app.config.settings import settings
from app.services.knowledge_upload_service import (
    KnowledgeUploadError,
    KnowledgeUploadService,
)

_PDF = b"%PDF-1.7 minimal body"
_BUCKET = "lcdash-p1-logan-use1-862772137583-document-library"
_MAE_PREFIX = "tenants/logan-synthetic/document-library/mae-uploads/current/"
_JACK_PREFIX = "tenants/logan-synthetic/document-library/jack-uploads/mindshare/current/"
_DESTINATIONS = {"mae": _MAE_PREFIX, "jack": _JACK_PREFIX}


class _StubClientError(Exception):
    """Shaped like botocore's ClientError -- .response["Error"]["Code"] --
    without importing botocore, proving the service duck-types the check."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakePaginator:
    def __init__(self, s3: "_FakeS3Client") -> None:
        self._s3 = s3

    def paginate(self, *, Bucket, Prefix):
        if self._s3.list_error is not None:
            raise self._s3.list_error
        contents = [
            {
                "Key": key,
                "Size": len(spec["body"]),
                "LastModified": spec["last_modified"],
            }
            for key, spec in sorted(self._s3.objects.items())
            if key.startswith(Prefix)
        ]
        # Two pages, to prove pagination is actually consumed.
        midpoint = max(len(contents) // 2, 1)
        yield {"Contents": contents[:midpoint]}
        yield {"Contents": contents[midpoint:]}


class _FakeS3Client:
    """In-memory S3 double recording every call it receives."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self.list_error: Exception | None = None
        self.head_fail_keys: set[str] = set()

    def seed(self, key: str, *, uploaded_by: str = "", last_modified=None) -> None:
        self.objects[key] = {
            "body": _PDF,
            "metadata": {"uploaded-by": uploaded_by} if uploaded_by else {},
            "last_modified": last_modified
            or datetime(2026, 8, 1, tzinfo=timezone.utc),
        }

    def head_object(self, *, Bucket, Key):
        self.calls.append(("head_object", Key))
        if Key in self.head_fail_keys:
            raise _StubClientError("AccessDenied")
        if Key not in self.objects:
            raise _StubClientError("404")
        return {"Metadata": dict(self.objects[Key]["metadata"])}

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        self.objects[kwargs["Key"]] = {
            "body": kwargs["Body"],
            "metadata": dict(kwargs.get("Metadata") or {}),
            "last_modified": datetime(2026, 8, 9, tzinfo=timezone.utc),
        }
        return {}

    def delete_object(self, *, Bucket, Key):
        self.calls.append(("delete_object", Key))
        self.objects.pop(Key, None)
        return {}

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2"
        return _FakePaginator(self)


class _FakeBedrockClient:
    """In-memory bedrock-agent double recording every call it receives."""

    def __init__(self, *, start_error=None, summaries=None) -> None:
        self.start_error = start_error
        self.summaries = summaries if summaries is not None else []
        self.calls: list[tuple] = []

    def start_ingestion_job(self, **kwargs):
        self.calls.append(("start_ingestion_job", kwargs))
        if self.start_error is not None:
            raise self.start_error
        return {"ingestionJob": {"ingestionJobId": "job-42", "status": "STARTING"}}

    def list_ingestion_jobs(self, **kwargs):
        self.calls.append(("list_ingestion_jobs", kwargs))
        return {"ingestionJobSummaries": list(self.summaries)}


def _service(s3=None, bedrock=None, **kwargs) -> KnowledgeUploadService:
    return KnowledgeUploadService(
        kwargs.pop("bucket", _BUCKET),
        kwargs.pop("destinations", _DESTINATIONS),
        s3_client=s3 or _FakeS3Client(),
        bedrock_client=bedrock or _FakeBedrockClient(),
        knowledge_base_id=kwargs.pop("knowledge_base_id", "KB123"),
        data_source_id=kwargs.pop("data_source_id", "DS456"),
        **kwargs,
    )


class DestinationDefaultsTests(unittest.TestCase):
    def test_settings_prefixes_are_pinned(self):
        """The prefix shapes carry JACK's /mindshare/ persona-filter requirement
        and must not collide with the approved library keys; a casual rename
        would silently break retrieval or clobber the mindshare mapping. The
        values live in settings (the one sanctioned home for county-specific
        strings -- see test_county_profiles' no-forks guard), so the pin
        targets settings, not service defaults."""
        self.assertEqual(settings.knowledge_upload_mae_prefix, _MAE_PREFIX)
        self.assertEqual(settings.knowledge_upload_jack_prefix, _JACK_PREFIX)
        self.assertIn("/mindshare/", settings.knowledge_upload_jack_prefix)
        self.assertEqual(settings.knowledge_upload_bucket, _BUCKET)
        # Library keys (segment after document-library/) stay clear of the
        # reviewed 'mindshare' and 'centralsquare' keys.
        for prefix in (_MAE_PREFIX, _JACK_PREFIX):
            key = prefix.split("document-library/", 1)[1].split("/", 1)[0]
            self.assertNotIn(key, {"mindshare", "centralsquare"})

    def test_service_refuses_blank_bucket_or_empty_destinations(self):
        with self.assertRaises(ValueError):
            _service(bucket="  ")
        with self.assertRaises(ValueError):
            _service(destinations={})


class UploadDocumentTests(unittest.TestCase):
    def setUp(self):
        self.s3 = _FakeS3Client()
        self.service = _service(s3=self.s3)

    def test_happy_upload_writes_encrypted_pdf_with_attribution(self):
        """The put must land at exactly prefix+basename with AES256, the PDF
        content type, and attribution metadata -- the KB ingests whatever sits
        under these prefixes, so key and encryption discipline is the gate."""
        result = self.service.upload_document(
            "mae", "SOP Manual.pdf", _PDF, uploaded_by="ted@911logan.com"
        )
        put = next(call for call in self.s3.calls if call[0] == "put_object")[1]
        self.assertEqual(put["Key"], _MAE_PREFIX + "SOP-Manual.pdf")
        self.assertEqual(put["Bucket"], _BUCKET)
        self.assertEqual(put["ContentType"], "application/pdf")
        self.assertEqual(put["ServerSideEncryption"], "AES256")
        self.assertEqual(
            put["Metadata"],
            {"uploaded-by": "ted@911logan.com", "destination": "mae"},
        )
        self.assertEqual(
            result,
            {
                "document_id": _encode_document_id("SOP-Manual.pdf"),
                "filename": "SOP-Manual.pdf",
                "destination": "mae",
                "size_bytes": len(_PDF),
                "uploaded_by": "ted@911logan.com",
            },
        )

    def test_non_ascii_uploader_is_stripped_from_metadata(self):
        """S3 metadata rides HTTP headers; a non-ASCII display name must not
        make the whole upload fail at the provider."""
        self.service.upload_document("mae", "a.pdf", _PDF, uploaded_by="Tęd Spärks")
        put = next(call for call in self.s3.calls if call[0] == "put_object")[1]
        self.assertEqual(put["Metadata"]["uploaded-by"], "Td Sprks")

    def test_size_cap_matches_intake_gate(self):
        """25 MB is the historical intake-gate cap; anything over is refused
        before any S3 call so a huge body never leaves the app."""
        oversized = _PDF + b"0" * (25 * 1024 * 1024)
        with self.assertRaises(KnowledgeUploadError):
            self.service.upload_document("mae", "big.pdf", oversized, uploaded_by="x")
        self.assertEqual(self.s3.calls, [])

    def test_empty_content_refused(self):
        with self.assertRaises(KnowledgeUploadError):
            self.service.upload_document("mae", "a.pdf", b"", uploaded_by="x")

    def test_non_pdf_magic_bytes_refused(self):
        """A renamed executable must never enter a KB-ingested prefix; the
        file's own first bytes decide, not the extension it claims."""
        with self.assertRaises(KnowledgeUploadError):
            self.service.upload_document(
                "mae", "totally-a.pdf", b"MZ\x90\x00fake-exe", uploaded_by="x"
            )
        self.assertEqual(self.s3.calls, [])

    def test_filename_sanitization_table(self):
        """Path traversal, unicode junk, missing suffixes, and overlong names
        all reduce to a safe basename; the object key is always exactly
        prefix + that basename, never anything path-shaped."""
        cases = [
            ("..\\evil.pdf", "evil.pdf"),
            ("../../x.pdf", "x.pdf"),
            ("rëpørt fïle.pdf", "rprt-fle.pdf"),
            ("notes", "notes.pdf"),
            ("Weekly Report.PDF", "Weekly-Report.pdf"),
            ("a" * 200 + ".pdf", "a" * 116 + ".pdf"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                s3 = _FakeS3Client()
                service = _service(s3=s3)
                result = service.upload_document("mae", raw, _PDF, uploaded_by="x")
                self.assertEqual(result["filename"], expected)
                self.assertLessEqual(len(result["filename"]), 120)
                put = next(call for call in s3.calls if call[0] == "put_object")[1]
                self.assertEqual(put["Key"], _MAE_PREFIX + expected)

    def test_filename_that_sanitizes_to_nothing_refused(self):
        for raw in ("øøø.pdf", "   .pdf", "///", ""):
            with self.subTest(raw=raw):
                with self.assertRaises(KnowledgeUploadError):
                    self.service.upload_document("mae", raw, _PDF, uploaded_by="x")

    def test_duplicate_key_refused_without_overwrite(self):
        """An existing object with the same name belongs to someone; the
        upload must refuse rather than silently replace it."""
        self.s3.seed(_MAE_PREFIX + "dup.pdf")
        with self.assertRaises(KnowledgeUploadError) as ctx:
            self.service.upload_document("mae", "dup.pdf", _PDF, uploaded_by="x")
        self.assertIn("already exists", str(ctx.exception))
        self.assertFalse(any(call[0] == "put_object" for call in self.s3.calls))

    def test_unknown_destination_refused(self):
        with self.assertRaises(KnowledgeUploadError):
            self.service.upload_document("nova", "a.pdf", _PDF, uploaded_by="x")
        self.assertEqual(self.s3.calls, [])


class ListDocumentsTests(unittest.TestCase):
    def setUp(self):
        self.s3 = _FakeS3Client()
        self.service = _service(s3=self.s3)

    def test_merges_destinations_and_sorts_newest_first(self):
        self.s3.seed(
            _MAE_PREFIX + "old.pdf",
            uploaded_by="alice",
            last_modified=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        self.s3.seed(
            _JACK_PREFIX + "new.pdf",
            uploaded_by="bob",
            last_modified=datetime(2026, 8, 8, tzinfo=timezone.utc),
        )
        self.s3.seed(
            _MAE_PREFIX + "mid.pdf",
            uploaded_by="carol",
            last_modified=datetime(2026, 8, 5, tzinfo=timezone.utc),
        )
        # A non-PDF stray must be ignored, not listed.
        self.s3.objects[_MAE_PREFIX + "stray.txt"] = {
            "body": b"x",
            "metadata": {},
            "last_modified": datetime(2026, 8, 9, tzinfo=timezone.utc),
        }
        records = self.service.list_documents()
        self.assertEqual(
            [record["filename"] for record in records],
            ["new.pdf", "mid.pdf", "old.pdf"],
        )
        newest = records[0]
        self.assertEqual(newest["destination"], "jack")
        self.assertEqual(newest["uploaded_by"], "bob")
        self.assertEqual(newest["document_id"], _encode_document_id("new.pdf"))
        self.assertEqual(newest["last_modified"], "2026-08-08T00:00:00+00:00")
        self.assertEqual(newest["size_bytes"], len(_PDF))

    def test_single_destination_filter(self):
        self.s3.seed(_MAE_PREFIX + "a.pdf")
        self.s3.seed(_JACK_PREFIX + "b.pdf")
        records = self.service.list_documents("jack")
        self.assertEqual([record["filename"] for record in records], ["b.pdf"])

    def test_head_failure_degrades_to_blank_attribution(self):
        """A metadata read failure on one object must cost only its
        uploaded_by field, never the whole listing."""
        self.s3.seed(_MAE_PREFIX + "a.pdf", uploaded_by="alice")
        self.s3.head_fail_keys.add(_MAE_PREFIX + "a.pdf")
        records = self.service.list_documents("mae")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["uploaded_by"], "")

    def test_listing_failure_is_sanitized(self):
        """The admin page gets one plain sentence; the boto payload stays in
        the logs, never in the response."""
        self.s3.list_error = _StubClientError("AccessDenied")
        with self.assertRaises(KnowledgeUploadError) as ctx:
            self.service.list_documents()
        self.assertEqual(str(ctx.exception), "Uploaded document listing failed.")
        self.assertNotIn("AccessDenied", str(ctx.exception))


class RemoveDocumentTests(unittest.TestCase):
    def setUp(self):
        self.s3 = _FakeS3Client()
        self.service = _service(s3=self.s3)

    def test_undecodable_id_rejected_before_any_s3_call(self):
        with self.assertRaises(KnowledgeUploadError):
            self.service.remove_document("mae", "!!!not-base64!!!", removed_by="x")
        self.assertEqual(self.s3.calls, [])

    def test_traversal_id_rejected_before_any_s3_call(self):
        """A crafted id must fail on shape alone -- if it ever reached S3 the
        delete could land outside the destination prefix."""
        for path in ("../mindshare/current/approved.pdf", "/absolute.pdf", "no-suffix"):
            with self.subTest(path=path):
                with self.assertRaises(KnowledgeUploadError):
                    self.service.remove_document(
                        "mae", _encode_document_id(path), removed_by="x"
                    )
        self.assertEqual(self.s3.calls, [])

    def test_removing_missing_document_is_an_error(self):
        """Deleting nothing must not report success; the admin's view of the
        library is stale and they need to know."""
        with self.assertRaises(KnowledgeUploadError):
            self.service.remove_document(
                "mae", _encode_document_id("ghost.pdf"), removed_by="x"
            )
        self.assertFalse(any(call[0] == "delete_object" for call in self.s3.calls))

    def test_happy_path_deletes_exact_key(self):
        self.s3.seed(_JACK_PREFIX + "obsolete.pdf")
        result = self.service.remove_document(
            "jack", _encode_document_id("obsolete.pdf"), removed_by="ted"
        )
        self.assertEqual(result, {"removed": True, "filename": "obsolete.pdf"})
        self.assertIn(("delete_object", _JACK_PREFIX + "obsolete.pdf"), self.s3.calls)
        self.assertNotIn(_JACK_PREFIX + "obsolete.pdf", self.s3.objects)


class IngestionTests(unittest.TestCase):
    def test_start_ingestion_happy_path(self):
        bedrock = _FakeBedrockClient()
        service = _service(bedrock=bedrock)
        result = service.start_ingestion()
        self.assertEqual(result, {"job_id": "job-42", "status": "STARTING"})
        call = bedrock.calls[0]
        self.assertEqual(
            call[1], {"knowledgeBaseId": "KB123", "dataSourceId": "DS456"}
        )

    def test_conflict_translated_to_plain_english(self):
        """Bedrock allows one running job per data source; the raw
        ConflictException must become a sentence, not a stack trace."""
        bedrock = _FakeBedrockClient(start_error=_StubClientError("ConflictException"))
        service = _service(bedrock=bedrock)
        with self.assertRaises(KnowledgeUploadError) as ctx:
            service.start_ingestion()
        self.assertEqual(
            str(ctx.exception),
            "An ingestion sync is already running; wait for it to finish.",
        )

    def test_unconfigured_ingestion_refused(self):
        service = _service(knowledge_base_id="", data_source_id="")
        with self.assertRaises(KnowledgeUploadError) as ctx:
            service.start_ingestion()
        self.assertIn("not configured", str(ctx.exception))

    def test_status_returns_newest_job_with_statistics(self):
        started = datetime(2026, 8, 9, 6, 0, tzinfo=timezone.utc)
        bedrock = _FakeBedrockClient(
            summaries=[
                {
                    "ingestionJobId": "job-9",
                    "status": "COMPLETE",
                    "startedAt": started,
                    "statistics": {"numberOfDocumentsScanned": 12},
                }
            ]
        )
        service = _service(bedrock=bedrock)
        result = service.ingestion_status()
        self.assertEqual(result["job_id"], "job-9")
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["started_at"], started.isoformat())
        self.assertEqual(result["statistics"], {"numberOfDocumentsScanned": 12})
        call = bedrock.calls[0][1]
        self.assertEqual(call["maxResults"], 1)
        self.assertEqual(
            call["sortBy"], {"attribute": "STARTED_AT", "order": "DESCENDING"}
        )

    def test_status_never_run_when_no_jobs(self):
        service = _service(bedrock=_FakeBedrockClient(summaries=[]))
        self.assertEqual(
            service.ingestion_status(), {"job_id": "", "status": "NEVER_RUN"}
        )


if __name__ == "__main__":
    unittest.main()
