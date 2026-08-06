import base64
import hashlib
import json
from pathlib import Path
import sys
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.tools import phase2_analytics_import_runtime as runtime
from app.tools.phase2_analytics_import import TABLE_PLANS


class FakeKms:
    def __init__(self, key):
        self.key = key

    def decrypt(self, **kwargs):
        self.kwargs = kwargs
        return {"Plaintext": self.key, "KeyId": "test-key"}


class RecordingCursor:
    def __init__(self):
        self.commands = []

    def execute(self, sql, parameters=None):
        self.commands.append((sql, parameters))

    def executemany(self, sql, rows):
        self.commands.append((sql, rows))

    def close(self):
        pass


class RecordingConnection:
    def __init__(self):
        self.cursor_value = RecordingCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def empty_bundle():
    tables = {plan.name: [] for plan in TABLE_PLANS}
    manifest = {
        plan.name: {
            "row_count": 0,
            "source_snapshot_row_count": 0,
            "primary_key_distinct_count": 0,
            "checksum_sha256": hashlib.sha256(runtime.canonical([])).hexdigest(),
            "fields": list(plan.fields),
            "key_fields": list(plan.key_fields),
        }
        for plan in TABLE_PLANS
    }
    return {
        "schema_version": runtime.BUNDLE_SCHEMA,
        "source": {
            "authoritative": True,
            "identity_reference": "lcdash-server/lcdash/lcdash_analytics",
            "transaction": "repeatable-read-read-only",
        },
        "tables": tables,
        "manifest": manifest,
    }


class AnalyticsImportRuntimeTests(unittest.TestCase):
    def test_environment_requires_exact_bucket_object_database_and_checksum(self):
        values = {
            "LCDASH_IMPORT_STAGING_BUCKET": runtime.APPROVED_BUCKET,
            "LCDASH_IMPORT_OBJECT_KEY": runtime.APPROVED_PREFIX + "approved.json.enc",
            "LCDASH_IMPORT_PLAINTEXT_SHA256": "a" * 64,
            "LCDASH_TARGET_DATABASE_HOST": "target.internal",
            "LCDASH_TARGET_DATABASE_PORT": "5432",
            "LCDASH_TARGET_DATABASE_NAME": "lcdash",
            "LCDASH_TARGET_DATABASE_USERNAME": "runtime-user",
            "LCDASH_TARGET_DATABASE_PASSWORD": "not-logged",
        }
        self.assertEqual(runtime._required_environment(values), values)
        for key, unsafe in (
            ("LCDASH_IMPORT_STAGING_BUCKET", "other"),
            ("LCDASH_IMPORT_OBJECT_KEY", "other/file.enc"),
            ("LCDASH_TARGET_DATABASE_NAME", "postgres"),
            ("LCDASH_TARGET_DATABASE_PORT", "5433"),
        ):
            changed = dict(values)
            changed[key] = unsafe
            with self.assertRaises(runtime.ImportRuntimeError):
                runtime._required_environment(changed)

    def test_aes_gcm_envelope_authenticates_exact_identity(self):
        key = bytes(range(32))
        nonce = bytes(range(12))
        plaintext = runtime.canonical(empty_bundle())
        aad = {
            "schema": runtime.ENVELOPE_SCHEMA,
            "bucket": runtime.APPROVED_BUCKET,
            "prefix": runtime.APPROVED_PREFIX,
        }
        envelope = {
            "schema_version": runtime.ENVELOPE_SCHEMA,
            "encrypted_data_key": base64.b64encode(b"wrapped").decode(),
            "nonce": base64.b64encode(nonce).decode(),
            "aad": aad,
            "ciphertext": base64.b64encode(
                AESGCM(key).encrypt(nonce, plaintext, runtime.canonical(aad))
            ).decode(),
        }
        self.assertEqual(
            runtime.decrypt_envelope(runtime.canonical(envelope), FakeKms(key)),
            plaintext,
        )
        envelope["aad"]["prefix"] = "other/"
        with self.assertRaises(runtime.ImportRuntimeError):
            runtime.decrypt_envelope(runtime.canonical(envelope), FakeKms(key))

    def test_bundle_requires_exact_tables_fields_keys_counts_and_checksums(self):
        payload = runtime.canonical(empty_bundle())
        tables, counts = runtime.validate_bundle(
            payload, hashlib.sha256(payload).hexdigest()
        )
        self.assertEqual(set(tables), {plan.name for plan in TABLE_PLANS})
        self.assertTrue(all(value == 0 for value in counts.values()))
        changed = empty_bundle()
        changed["tables"]["calls"] = [{"raw_cad_payload": "prohibited"}]
        payload = runtime.canonical(changed)
        with self.assertRaises(ValueError):
            runtime.validate_bundle(payload, hashlib.sha256(payload).hexdigest())

    def test_target_load_is_one_transaction_and_uses_only_allowlisted_upserts(self):
        connection = RecordingConnection()
        tables = {plan.name: [] for plan in TABLE_PLANS}
        counts = runtime.import_tables(connection, tables)
        self.assertEqual(connection.cursor_value.commands[0][0], "BEGIN")
        self.assertEqual(
            connection.cursor_value.commands[1][0],
            "SET LOCAL statement_timeout = '10min'",
        )
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(set(counts), {plan.target for plan in TABLE_PLANS})


if __name__ == "__main__":
    unittest.main()
