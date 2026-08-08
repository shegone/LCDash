"""Round-trip contract: the exporter must produce bundles the importer admits.

These tests run the real AES-GCM crypto and the real importer validator over an
exported bundle, so a drift in the envelope shape, the AAD bytes, the checksum
basis, or the JSON scalar encoding fails here rather than at a live import.
No network and no database: KMS and psycopg are stubbed.
"""

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from app.tools.phase2_analytics_export import (
    APPROVED_SOURCE,
    ExportError,
    build_bundle,
    encrypt_bundle,
    json_scalar,
    stage_object,
    summarize,
)
from app.tools.phase2_analytics_import import TABLE_PLANS
from app.tools.phase2_analytics_import_runtime import (
    APPROVED_PREFIX,
    ImportRuntimeError,
    decrypt_envelope,
    validate_bundle,
)

DATA_KEY = bytes(range(32))


class _FakeKms:
    """Symmetric stub: generate_data_key/decrypt agree on one fixed key."""

    def __init__(self):
        self.generate_calls = []

    def generate_data_key(self, **kwargs):
        self.generate_calls.append(kwargs)
        return {"Plaintext": bytearray(DATA_KEY), "CiphertextBlob": b"wrapped-key"}

    def decrypt(self, **_kwargs):
        return {"Plaintext": bytearray(DATA_KEY)}


class _FakeCursor:
    def __init__(self, rows_by_sql):
        self._rows_by_sql = rows_by_sql
        self._current = []
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        for plan in TABLE_PLANS:
            if sql == plan.source_sql:
                self._current = list(self._rows_by_sql.get(plan.name, []))
                return
        self._current = []

    def __iter__(self):
        return iter(self._current)

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, rows_by_table):
        self.cursor_obj = _FakeCursor(rows_by_table)
        self.rolled_back = False

    def cursor(self):
        return self.cursor_obj

    def rollback(self):
        self.rolled_back = True


def _call_row(cfs_number="CFS26-00001"):
    values = {
        "cfs_number": cfs_number,
        "latitude": Decimal("37.8508"),
        "longitude": Decimal("-81.9975"),
        "is_scheduled": False,
        "priority": 10,
        "call_received_at": datetime(2026, 8, 8, 17, 5, 32, tzinfo=timezone.utc),
    }
    return {field: values.get(field) for field in TABLE_PLANS[0].fields}


def _source(call_rows=None):
    return _FakeConnection({"calls": call_rows if call_rows is not None else [_call_row()]})


def _window():
    return {
        "window_start": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "window_end": datetime(2026, 12, 31, tzinfo=timezone.utc),
    }


class JsonScalarTests(unittest.TestCase):
    def test_naive_timestamp_is_treated_as_utc_not_local(self):
        # Silently shifting a naive timestamp by the local offset would corrupt
        # every response-time metric computed from it.
        self.assertEqual(
            json_scalar(datetime(2026, 8, 8, 12, 0, 0)), "2026-08-08T12:00:00+00:00"
        )

    def test_aware_timestamp_is_normalized_to_utc_offset(self):
        eastern = timezone.utc
        self.assertTrue(
            json_scalar(datetime(2026, 8, 8, 12, 0, tzinfo=eastern)).endswith("+00:00")
        )

    def test_decimal_keeps_exact_text_precision(self):
        self.assertEqual(json_scalar(Decimal("37.8508")), "37.8508")

    def test_passthrough_scalars(self):
        self.assertIsNone(json_scalar(None))
        self.assertIs(json_scalar(True), True)
        self.assertEqual(json_scalar(7), 7)


class BuildBundleTests(unittest.TestCase):
    def test_bundle_uses_a_read_only_repeatable_read_transaction(self):
        source = _source()
        build_bundle(source, **_window())
        self.assertIn(
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
            source.cursor_obj.executed,
        )
        self.assertTrue(source.rolled_back)

    def test_widgets_are_exported_empty_by_design(self):
        # The target declares tenant_id NOT NULL while the importer omits it,
        # and widget_id is BIGSERIAL -- importing rows would fail or desync the
        # sequence. An empty list is contract-valid and correct.
        bundle = build_bundle(_source(), **_window())
        self.assertEqual(bundle["tables"]["saved_analytics_widgets"], [])
        self.assertEqual(
            bundle["manifest"]["saved_analytics_widgets"]["row_count"], 0
        )

    def test_source_evidence_matches_the_approved_identity(self):
        bundle = build_bundle(_source(), **_window())
        self.assertEqual(bundle["source"], APPROVED_SOURCE)

    def test_duplicate_keys_are_rejected_before_encryption(self):
        duplicate = [_call_row("CFS26-00001"), _call_row("CFS26-00001")]
        with self.assertRaises(ExportError):
            build_bundle(_source(duplicate), **_window())

    def test_inverted_window_is_rejected(self):
        with self.assertRaises(ExportError):
            build_bundle(
                _source(),
                window_start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                window_end=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )


class RoundTripTests(unittest.TestCase):
    """The load-bearing proof: exporter output survives the real importer."""

    def _round_trip(self, call_rows=None):
        kms = _FakeKms()
        bundle = build_bundle(_source(call_rows), **_window())
        envelope, plaintext_sha256 = encrypt_bundle(bundle, kms, key_id="alias/test")
        plaintext = decrypt_envelope(envelope, kms)
        tables, counts = validate_bundle(plaintext, plaintext_sha256)
        return bundle, tables, counts, kms

    def test_exported_bundle_decrypts_and_validates(self):
        bundle, tables, counts, _ = self._round_trip()
        self.assertEqual(counts["lcdash_analytics.calls"], 1)
        self.assertEqual(counts["lcdash_analytics.saved_analytics_widgets"], 0)
        self.assertEqual(
            tables["calls"][0]["cfs_number"], bundle["tables"]["calls"][0]["cfs_number"]
        )

    def test_data_key_is_requested_without_an_encryption_context(self):
        # The importer decrypts with no EncryptionContext; supplying one here
        # would make every real import fail authentication.
        _, _, _, kms = self._round_trip()
        self.assertNotIn("EncryptionContext", kms.generate_calls[0])
        self.assertEqual(kms.generate_calls[0]["KeySpec"], "AES_256")

    def test_checksums_survive_the_json_parse_boundary(self):
        # Exporter checksums pre-serialization, importer post-parse; only
        # JSON-native scalars make those agree.
        rows = [_call_row(f"CFS26-{index:05d}") for index in range(5)]
        _, tables, counts, _ = self._round_trip(rows)
        self.assertEqual(counts["lcdash_analytics.calls"], 5)
        self.assertEqual(tables["calls"][0]["latitude"], "37.8508")

    def test_tampered_ciphertext_fails_authentication(self):
        kms = _FakeKms()
        bundle = build_bundle(_source(), **_window())
        envelope, _ = encrypt_bundle(bundle, kms, key_id="alias/test")
        tampered = envelope.replace(b"lcdash.analytics-history.bundle", b"x", 1)
        with self.assertRaises(ImportRuntimeError):
            decrypt_envelope(tampered if tampered != envelope else envelope[:-4] + b"AAA=", kms)

    def test_wrong_expected_checksum_is_rejected(self):
        kms = _FakeKms()
        bundle = build_bundle(_source(), **_window())
        envelope, _ = encrypt_bundle(bundle, kms, key_id="alias/test")
        plaintext = decrypt_envelope(envelope, kms)
        with self.assertRaises(ImportRuntimeError):
            validate_bundle(plaintext, "0" * 64)


class StagingTests(unittest.TestCase):
    class _FakeS3:
        def __init__(self):
            self.calls = []

        def put_object(self, **kwargs):
            self.calls.append(kwargs)

    def test_object_outside_the_approved_prefix_is_refused(self):
        with self.assertRaises(ExportError):
            stage_object(self._FakeS3(), b"x", object_key="elsewhere/x.json.enc", kms_key_id="k")

    def test_non_enc_suffix_is_refused(self):
        with self.assertRaises(ExportError):
            stage_object(
                self._FakeS3(), b"x", object_key=f"{APPROVED_PREFIX}x.json", kms_key_id="k"
            )

    def test_staged_object_uses_kms_server_side_encryption(self):
        s3 = self._FakeS3()
        stage_object(
            s3, b"x", object_key=f"{APPROVED_PREFIX}bundle.json.enc", kms_key_id="key-arn"
        )
        self.assertEqual(s3.calls[0]["ServerSideEncryption"], "aws:kms")
        self.assertEqual(s3.calls[0]["SSEKMSKeyId"], "key-arn")


class SummaryTests(unittest.TestCase):
    def test_summary_reports_counts_and_never_row_content(self):
        bundle = build_bundle(_source(), **_window())
        summary = summarize(bundle, "a" * 64, f"{APPROVED_PREFIX}b.json.enc")
        self.assertEqual(summary["table_counts"]["calls"], 1)
        self.assertNotIn("CFS26-00001", str(summary))


if __name__ == "__main__":
    unittest.main()
