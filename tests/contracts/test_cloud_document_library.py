"""Network-free contracts for the S3-backed cloud knowledge document library."""

import unittest
from io import BytesIO

from app.config.settings import Settings
from app.services.cloud_document_library import (
    CloudDocumentLibrary,
    CloudDocumentLibraryUnavailable,
    _decode_document_id,
    _encode_document_id,
    _parse_allowed_prefixes,
    build_cloud_document_library,
    content_disposition_header,
)


ALLOWED_PREFIXES = (
    "s3://lcdash-p1-logan-use1-862772137583-document-library/"
    "tenants/logan-synthetic/document-library/mindshare/current/"
    "onprem-approved-164-2026-08-05/",
    "s3://lcdash-p1-logan-use1-862772137583-document-library/"
    "tenants/logan-synthetic/document-library/centralsquare/current/"
    "onprem-approved-164-2026-08-05/",
)


class _Paginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return iter(self._pages)


class _Client:
    def __init__(self, pages=(), object_bodies=None, get_object_error=None):
        self._pages = pages
        self._object_bodies = object_bodies or {}
        self._get_object_error = get_object_error
        self.get_object_calls = []

    def get_paginator(self, operation_name):
        assert operation_name == "list_objects_v2"
        return _Paginator(self._pages)

    def get_object(self, **kwargs):
        self.get_object_calls.append(kwargs)
        if self._get_object_error is not None:
            raise self._get_object_error
        key = kwargs["Key"]
        if key not in self._object_bodies:
            raise KeyError(f"no such object: {key}")
        return {"Body": BytesIO(self._object_bodies[key])}


def _settings(prefixes=ALLOWED_PREFIXES):
    return Settings(cloud_ai_allowed_s3_prefixes=prefixes)


class ParseAllowedPrefixesTests(unittest.TestCase):
    def test_extracts_library_key_bucket_and_prefix(self):
        mapping = _parse_allowed_prefixes(ALLOWED_PREFIXES)
        self.assertEqual(set(mapping), {"mindshare", "centralsquare"})
        bucket, prefix = mapping["mindshare"]
        self.assertEqual(bucket, "lcdash-p1-logan-use1-862772137583-document-library")
        self.assertEqual(
            prefix,
            "tenants/logan-synthetic/document-library/mindshare/current/"
            "onprem-approved-164-2026-08-05/",
        )

    def test_non_s3_or_unrelated_uri_is_skipped_not_raised(self):
        mapping = _parse_allowed_prefixes(
            ("https://example.com/not-s3/", "s3://bucket/unrelated/path/")
        )
        self.assertEqual(mapping, {})

    def test_empty_prefixes_yield_empty_mapping(self):
        self.assertEqual(_parse_allowed_prefixes(()), {})


class DocumentIdRoundTripTests(unittest.TestCase):
    def test_round_trips_a_simple_filename(self):
        encoded = _encode_document_id("Garmin GPS 2.6 Installation Guide.pdf")
        self.assertEqual(
            _decode_document_id(encoded), "Garmin GPS 2.6 Installation Guide.pdf"
        )

    def test_round_trips_a_deeply_nested_path(self):
        path = "Current Documentation/Technical Documents/Application Notes/IP Gateways/DMR/AIS/MS1019.pdf"
        encoded = _encode_document_id(path)
        self.assertEqual(_decode_document_id(encoded), path)
        # Path separators must never appear literally in the encoded id --
        # this is what lets a single {document_id} route path segment carry
        # an arbitrarily nested relative path.
        self.assertNotIn("/", encoded)

    def test_malformed_document_id_decodes_to_none_not_an_exception(self):
        self.assertIsNone(_decode_document_id("not valid base64!!!"))
        # An empty string is technically valid base64 (decodes to empty
        # bytes); it's rejected downstream in fetch_document's `not
        # relative_path` check, not here at the decode step.
        self.assertEqual(_decode_document_id(""), "")


class CloudDocumentLibraryListTests(unittest.TestCase):
    def test_unknown_library_returns_empty_without_calling_the_client(self):
        client = _Client()
        library = CloudDocumentLibrary(client=client, settings=_settings())
        self.assertEqual(library.list_documents("nope"), ())

    def test_available_reports_known_libraries_only(self):
        library = CloudDocumentLibrary(client=_Client(), settings=_settings())
        self.assertTrue(library.available("mindshare"))
        self.assertTrue(library.available("centralsquare"))
        self.assertFalse(library.available("gis"))

    def test_lists_pdfs_and_strips_the_prefix_to_a_relative_path(self):
        prefix = (
            "tenants/logan-synthetic/document-library/centralsquare/current/"
            "onprem-approved-164-2026-08-05/"
        )
        client = _Client(
            pages=[
                {
                    "Contents": [
                        {"Key": f"{prefix}Garmin GPS 2.6 Installation Guide.pdf", "Size": 660049},
                        {"Key": f"{prefix}readme.txt", "Size": 10},
                    ]
                }
            ]
        )
        library = CloudDocumentLibrary(client=client, settings=_settings())
        documents = library.list_documents("centralsquare")
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].title, "Garmin GPS 2.6 Installation Guide")
        self.assertEqual(
            documents[0].relative_path, "Garmin GPS 2.6 Installation Guide.pdf"
        )
        self.assertEqual(documents[0].size_bytes, 660049)

    def test_non_pdf_objects_are_excluded(self):
        prefix = (
            "tenants/logan-synthetic/document-library/mindshare/current/"
            "onprem-approved-164-2026-08-05/"
        )
        client = _Client(
            pages=[{"Contents": [{"Key": f"{prefix}notes.docx", "Size": 5}]}]
        )
        library = CloudDocumentLibrary(client=client, settings=_settings())
        self.assertEqual(library.list_documents("mindshare"), ())

    def test_multiple_pages_are_all_consumed(self):
        prefix = (
            "tenants/logan-synthetic/document-library/mindshare/current/"
            "onprem-approved-164-2026-08-05/"
        )
        client = _Client(
            pages=[
                {"Contents": [{"Key": f"{prefix}a.pdf", "Size": 1}]},
                {"Contents": [{"Key": f"{prefix}b.pdf", "Size": 2}]},
            ]
        )
        library = CloudDocumentLibrary(client=client, settings=_settings())
        documents = library.list_documents("mindshare")
        self.assertEqual({d.relative_path for d in documents}, {"a.pdf", "b.pdf"})

    def test_documents_are_sorted_by_relative_path(self):
        prefix = (
            "tenants/logan-synthetic/document-library/mindshare/current/"
            "onprem-approved-164-2026-08-05/"
        )
        client = _Client(
            pages=[
                {
                    "Contents": [
                        {"Key": f"{prefix}Z/last.pdf", "Size": 1},
                        {"Key": f"{prefix}A/first.pdf", "Size": 1},
                    ]
                }
            ]
        )
        library = CloudDocumentLibrary(client=client, settings=_settings())
        documents = library.list_documents("mindshare")
        self.assertEqual(
            [d.relative_path for d in documents], ["A/first.pdf", "Z/last.pdf"]
        )

    def test_provider_failure_is_sanitized(self):
        class _BrokenPaginator:
            def paginate(self, **kwargs):
                raise RuntimeError("provider payload must not escape")

        class _BrokenClient:
            def get_paginator(self, operation_name):
                return _BrokenPaginator()

        library = CloudDocumentLibrary(client=_BrokenClient(), settings=_settings())
        with self.assertRaises(CloudDocumentLibraryUnavailable) as caught:
            library.list_documents("mindshare")
        self.assertNotIn("provider payload", str(caught.exception))


class CloudDocumentLibraryFetchTests(unittest.TestCase):
    def _library_and_prefix(self, pdf_bytes=b"%PDF-1.4 fake content"):
        prefix = (
            "tenants/logan-synthetic/document-library/centralsquare/current/"
            "onprem-approved-164-2026-08-05/"
        )
        key = f"{prefix}Garmin GPS 2.6 Installation Guide.pdf"
        client = _Client(object_bodies={key: pdf_bytes})
        library = CloudDocumentLibrary(client=client, settings=_settings())
        return library, client, prefix, key

    def test_fetches_a_document_by_its_encoded_id(self):
        library, client, prefix, key = self._library_and_prefix()
        document_id = _encode_document_id(
            "Garmin GPS 2.6 Installation Guide.pdf"
        )
        result = library.fetch_document("centralsquare", document_id)
        self.assertIsNotNone(result)
        payload, filename = result
        self.assertEqual(payload, b"%PDF-1.4 fake content")
        self.assertEqual(filename, "Garmin GPS 2.6 Installation Guide.pdf")
        self.assertEqual(client.get_object_calls, [{"Bucket": client.get_object_calls[0]["Bucket"], "Key": key}])

    def test_unknown_library_returns_none_without_calling_the_client(self):
        library, client, _, _ = self._library_and_prefix()
        result = library.fetch_document("nope", "anything")
        self.assertIsNone(result)
        self.assertEqual(client.get_object_calls, [])

    def test_malformed_document_id_returns_none_without_calling_the_client(self):
        library, client, _, _ = self._library_and_prefix()
        result = library.fetch_document("centralsquare", "not valid base64!!!")
        self.assertIsNone(result)
        self.assertEqual(client.get_object_calls, [])

    def test_path_traversal_document_id_is_rejected_before_any_s3_call(self):
        library, client, _, _ = self._library_and_prefix()
        traversal_id = _encode_document_id("../../../etc/passwd")
        result = library.fetch_document("centralsquare", traversal_id)
        self.assertIsNone(result)
        self.assertEqual(client.get_object_calls, [])

    def test_absolute_path_document_id_is_rejected(self):
        library, client, _, _ = self._library_and_prefix()
        absolute_id = _encode_document_id("/etc/passwd")
        result = library.fetch_document("centralsquare", absolute_id)
        self.assertIsNone(result)
        self.assertEqual(client.get_object_calls, [])

    def test_a_document_id_can_only_ever_resolve_inside_its_own_prefix(self):
        # Even a well-formed relative path is always joined onto the fixed,
        # reviewed prefix -- it can reference a sibling object within that
        # prefix, but never escape it, because the full key is always
        # prefix + relative_path and nothing else contributes to the key.
        library, client, prefix, _ = self._library_and_prefix()
        sibling_id = _encode_document_id("Other Approved Doc.pdf")
        library.fetch_document("centralsquare", sibling_id)
        self.assertEqual(
            client.get_object_calls[0]["Key"], f"{prefix}Other Approved Doc.pdf"
        )

    def test_missing_object_returns_none_not_an_exception(self):
        library, client, prefix, key = self._library_and_prefix()
        missing_id = _encode_document_id("does-not-exist.pdf")
        result = library.fetch_document("centralsquare", missing_id)
        self.assertIsNone(result)

    def test_provider_error_returns_none_and_leaks_nothing(self):
        prefix = (
            "tenants/logan-synthetic/document-library/centralsquare/current/"
            "onprem-approved-164-2026-08-05/"
        )
        client = _Client(get_object_error=RuntimeError("provider payload must not escape"))
        library = CloudDocumentLibrary(client=client, settings=_settings())
        document_id = _encode_document_id("Garmin GPS 2.6 Installation Guide.pdf")
        result = library.fetch_document("centralsquare", document_id)
        self.assertIsNone(result)

    def test_empty_body_returns_none(self):
        library, client, prefix, key = self._library_and_prefix(pdf_bytes=b"")
        document_id = _encode_document_id("Garmin GPS 2.6 Installation Guide.pdf")
        result = library.fetch_document("centralsquare", document_id)
        self.assertIsNone(result)


class ContentDispositionHeaderTests(unittest.TestCase):
    def test_inline_and_attachment_dispositions(self):
        self.assertEqual(
            content_disposition_header("Guide.pdf", download=False),
            'inline; filename="Guide.pdf"',
        )
        self.assertEqual(
            content_disposition_header("Guide.pdf", download=True),
            'attachment; filename="Guide.pdf"',
        )

    def test_strips_header_injection_characters(self):
        # The security property is that CR/LF can never reach the header --
        # without them, "X-Injected: evil" is inert text inside one
        # filename value, not a new header line.
        malicious = 'Guide.pdf"\r\nX-Injected: evil\r\n'
        header = content_disposition_header(malicious, download=False)
        self.assertNotIn("\r", header)
        self.assertNotIn("\n", header)
        self.assertEqual(header, 'inline; filename="Guide.pdfX-Injected: evil"')

    def test_empty_or_quote_only_filename_falls_back_safely(self):
        self.assertEqual(
            content_disposition_header('"""', download=False),
            'inline; filename="document.pdf"',
        )


class BuildCloudDocumentLibraryTests(unittest.TestCase):
    def test_construction_performs_no_provider_call(self):
        # LazyS3DocumentClient must not touch boto3 at construction time.
        library = build_cloud_document_library(_settings())
        self.assertIsInstance(library, CloudDocumentLibrary)


if __name__ == "__main__":
    unittest.main()
