"""Fail-closed runtime for one approved encrypted historical analytics object."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Any, Mapping

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import psycopg

from app.tools.phase2_analytics_import import TABLE_PLANS, validate_row


ENVELOPE_SCHEMA = "lcdash.analytics-history.envelope.v1"
BUNDLE_SCHEMA = "lcdash.analytics-history.bundle.v1"
APPROVED_BUCKET = "lcdash-p1-logan-use1-862772137583-analytics-import-staging"
APPROVED_PREFIX = "tenants/logan-synthetic/historical-analytics/"
MAX_ENCRYPTED_BYTES = 16 * 1024 * 1024
SHA256 = re.compile(r"^[a-f0-9]{64}$")


class ImportRuntimeError(RuntimeError):
    """Raised without including protected row content."""


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _required_environment(environment: Mapping[str, str]) -> dict[str, str]:
    required = (
        "LCDASH_IMPORT_STAGING_BUCKET",
        "LCDASH_IMPORT_OBJECT_KEY",
        "LCDASH_IMPORT_PLAINTEXT_SHA256",
        "LCDASH_TARGET_DATABASE_HOST",
        "LCDASH_TARGET_DATABASE_PORT",
        "LCDASH_TARGET_DATABASE_NAME",
        "LCDASH_TARGET_DATABASE_USERNAME",
        "LCDASH_TARGET_DATABASE_PASSWORD",
    )
    values = {key: str(environment.get(key, "")).strip() for key in required}
    if any(not value for value in values.values()):
        raise ImportRuntimeError("Required import configuration is incomplete")
    if values["LCDASH_IMPORT_STAGING_BUCKET"] != APPROVED_BUCKET:
        raise ImportRuntimeError("Staging bucket is outside the approved identity")
    object_key = values["LCDASH_IMPORT_OBJECT_KEY"]
    if (
        not object_key.startswith(APPROVED_PREFIX)
        or not object_key.endswith(".json.enc")
        or ".." in object_key
    ):
        raise ImportRuntimeError("Staged object key is outside the approved prefix")
    if not SHA256.fullmatch(values["LCDASH_IMPORT_PLAINTEXT_SHA256"]):
        raise ImportRuntimeError("Expected plaintext checksum is invalid")
    if values["LCDASH_TARGET_DATABASE_NAME"] != "lcdash":
        raise ImportRuntimeError("Target database is outside the approved identity")
    try:
        port = int(values["LCDASH_TARGET_DATABASE_PORT"])
    except ValueError as error:
        raise ImportRuntimeError("Target database port is invalid") from error
    if port != 5432:
        raise ImportRuntimeError("Target database port is outside the approved identity")
    return values


def download_exact_object(s3_client: Any, *, bucket: str, key: str) -> bytes:
    response = s3_client.get_object(Bucket=bucket, Key=key, ChecksumMode="ENABLED")
    length = int(response.get("ContentLength", -1))
    if length < 1 or length > MAX_ENCRYPTED_BYTES:
        raise ImportRuntimeError("Encrypted object size is outside the approved limit")
    body = response["Body"].read(MAX_ENCRYPTED_BYTES + 1)
    if len(body) != length or len(body) > MAX_ENCRYPTED_BYTES:
        raise ImportRuntimeError("Encrypted object length validation failed")
    return body


def decrypt_envelope(encrypted: bytes, kms_client: Any) -> bytes:
    try:
        envelope = json.loads(encrypted)
        if envelope.get("schema_version") != ENVELOPE_SCHEMA:
            raise ImportRuntimeError("Encrypted envelope schema is invalid")
        aad = envelope["aad"]
        if aad != {
            "schema": ENVELOPE_SCHEMA,
            "bucket": APPROVED_BUCKET,
            "prefix": APPROVED_PREFIX,
        }:
            raise ImportRuntimeError("Encrypted envelope identity is invalid")
        key_response = kms_client.decrypt(
            CiphertextBlob=base64.b64decode(envelope["encrypted_data_key"]),
            EncryptionAlgorithm="SYMMETRIC_DEFAULT",
        )
        key = bytearray(key_response["Plaintext"])
        try:
            return AESGCM(bytes(key)).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                canonical(aad),
            )
        finally:
            for index in range(len(key)):
                key[index] = 0
            key_response["Plaintext"] = b""
    except ImportRuntimeError:
        raise
    except Exception as error:
        raise ImportRuntimeError("Encrypted object authentication failed") from error


def validate_bundle(plaintext: bytes, expected_sha256: str) -> tuple[dict, dict[str, int]]:
    if hashlib.sha256(plaintext).hexdigest() != expected_sha256:
        raise ImportRuntimeError("Plaintext checksum does not match approved evidence")
    try:
        bundle = json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ImportRuntimeError("Decrypted bundle is not valid JSON") from error
    if bundle.get("schema_version") != BUNDLE_SCHEMA:
        raise ImportRuntimeError("Decrypted bundle schema is invalid")
    source = bundle.get("source", {})
    if source != {
        "authoritative": True,
        "identity_reference": "lcdash-server/lcdash/lcdash_analytics",
        "transaction": "repeatable-read-read-only",
    }:
        raise ImportRuntimeError("Source evidence is outside the approved identity")
    tables = bundle.get("tables")
    manifest = bundle.get("manifest")
    if not isinstance(tables, dict) or not isinstance(manifest, dict):
        raise ImportRuntimeError("Bundle tables or manifest are invalid")
    expected_names = {plan.name for plan in TABLE_PLANS}
    if set(tables) != expected_names or set(manifest) != expected_names:
        raise ImportRuntimeError("Bundle table set differs from the exact allowlist")
    counts: dict[str, int] = {}
    for plan in TABLE_PLANS:
        rows = tables[plan.name]
        evidence = manifest[plan.name]
        if not isinstance(rows, list) or not isinstance(evidence, dict):
            raise ImportRuntimeError(f"{plan.name} payload structure is invalid")
        validated = [validate_row(plan, row) for row in rows]
        identities = [tuple(row[field] for field in plan.key_fields) for row in validated]
        if len(set(identities)) != len(validated):
            raise ImportRuntimeError(f"{plan.name} contains duplicate approved keys")
        checksum = hashlib.sha256(canonical(validated)).hexdigest()
        if evidence.get("row_count") != len(validated):
            raise ImportRuntimeError(f"{plan.name} row count differs from its manifest")
        if evidence.get("source_snapshot_row_count") != len(validated):
            raise ImportRuntimeError(f"{plan.name} source count differs from its manifest")
        if evidence.get("primary_key_distinct_count") != len(validated):
            raise ImportRuntimeError(f"{plan.name} distinct count differs from its manifest")
        if evidence.get("checksum_sha256") != checksum:
            raise ImportRuntimeError(f"{plan.name} checksum differs from its manifest")
        if tuple(evidence.get("fields", ())) != plan.fields:
            raise ImportRuntimeError(f"{plan.name} field order differs from the allowlist")
        if tuple(evidence.get("key_fields", ())) != plan.key_fields:
            raise ImportRuntimeError(f"{plan.name} key fields differ from the allowlist")
        counts[plan.target] = len(validated)
    return tables, counts


def import_tables(connection: Any, tables: Mapping[str, list[dict]]) -> dict[str, int]:
    cursor = connection.cursor()
    counts: dict[str, int] = {}
    try:
        cursor.execute("BEGIN")
        cursor.execute("SET LOCAL statement_timeout = '10min'")
        for plan in TABLE_PLANS:
            rows = [validate_row(plan, row) for row in tables[plan.name]]
            if rows:
                cursor.executemany(plan.upsert_sql, rows)
            counts[plan.target] = len(rows)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
    return counts


def main(environment: Mapping[str, str] | None = None) -> int:
    values = _required_environment(environment or os.environ)
    session = boto3.session.Session(region_name="us-east-1")
    encrypted = download_exact_object(
        session.client("s3"),
        bucket=values["LCDASH_IMPORT_STAGING_BUCKET"],
        key=values["LCDASH_IMPORT_OBJECT_KEY"],
    )
    plaintext = decrypt_envelope(encrypted, session.client("kms"))
    del encrypted
    tables, admitted_counts = validate_bundle(
        plaintext, values["LCDASH_IMPORT_PLAINTEXT_SHA256"]
    )
    del plaintext
    connection = psycopg.connect(
        host=values["LCDASH_TARGET_DATABASE_HOST"],
        port=int(values["LCDASH_TARGET_DATABASE_PORT"]),
        dbname=values["LCDASH_TARGET_DATABASE_NAME"],
        user=values["LCDASH_TARGET_DATABASE_USERNAME"],
        password=values["LCDASH_TARGET_DATABASE_PASSWORD"],
        sslmode="require",
        connect_timeout=15,
        autocommit=False,
    )
    try:
        imported_counts = import_tables(connection, tables)
    finally:
        connection.close()
    if imported_counts != admitted_counts:
        raise ImportRuntimeError("Imported counts differ from admitted counts")
    print(json.dumps({"status": "complete", "table_counts": imported_counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
