"""Produce one encrypted historical-analytics bundle for the Phase 2 importer.

This is the authorized export counterpart to
``phase2_analytics_import_runtime``. It reads the on-prem analytics schema in a
single repeatable-read read-only transaction, builds the exact bundle the
importer admits, and encrypts it client-side with an AES-256-GCM data key
wrapped by the approved KMS CMK.

Automating bundle production does not relax any import-side control: the
envelope encryption, the AAD identity binding, the per-table checksums, the
field/key allowlists, and the importer's fail-closed validation all still
apply, byte for byte. What is automated is the labor of assembling a
conforming bundle, not the safeguards around it.

Protected-data handling: plaintext exists only in memory, is never written to
disk, and no row content is ever logged. The data key is zeroed after use.

Contract details that MUST match ``phase2_analytics_import_runtime``:
- KMS ``generate_data_key`` is called with NO encryption context, because the
  importer calls ``kms.decrypt`` without one.
- The AES-GCM associated data is ``canonical(aad)`` -- sorted-key, whitespace-
  free JSON -- not the raw mapping.
- Per-table ``checksum_sha256`` is taken over the JSON-native validated rows,
  so the exporter emits only JSON-native scalars and the importer's post-parse
  checksum is identical.
- ``saved_analytics_widgets`` is exported EMPTY on purpose: the target declares
  ``tenant_id NOT NULL`` while the importer's field list omits it (a fresh
  target would reject every widget row), and ``widget_id`` is ``BIGSERIAL`` so
  importing explicit ids would leave the target sequence behind and collide
  with later inserts. Saved widgets are per-environment UI state, not call
  history, so an empty list is both contract-valid and the correct outcome.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from app.tools.phase2_analytics_contract import (
    APPROVED_BUCKET,
    APPROVED_PREFIX,
    BUNDLE_SCHEMA,
    ENVELOPE_SCHEMA,
    MAX_ENCRYPTED_BYTES,
    canonical,
)
from app.tools.phase2_analytics_import import TABLE_PLANS, validate_row

APPROVED_SOURCE = {
    "authoritative": True,
    "identity_reference": "lcdash-server/lcdash/lcdash_analytics",
    "transaction": "repeatable-read-read-only",
}
# Widgets are deliberately not migrated; see the module docstring.
EMPTY_TABLES = frozenset({"saved_analytics_widgets"})
AES_GCM_NONCE_BYTES = 12


class ExportError(RuntimeError):
    """Raised without including protected row content."""


def json_scalar(value: Any) -> Any:
    """Convert one database value to a JSON-native scalar.

    Timestamps become explicit-offset UTC ISO-8601 and numerics keep their
    exact decimal text; both round-trip through JSON unchanged, so the
    checksum the importer recomputes after parsing matches this export.
    """
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return value
    return str(value)


def _rows_for_plan(cursor: Any, plan: Any, *, window_start: datetime, window_end: datetime) -> list[dict]:
    if plan.name in EMPTY_TABLES:
        return []
    cursor.execute(
        plan.source_sql,
        {"window_start": window_start, "window_end": window_end},
    )
    rows: list[dict] = []
    for record in cursor:
        if isinstance(record, Mapping):
            raw = {field: record[field] for field in plan.fields}
        else:
            raw = dict(zip(plan.fields, record))
        rows.append(validate_row(plan, {key: json_scalar(value) for key, value in raw.items()}))
    return rows


def build_bundle(
    source_connection: Any,
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict:
    """Read the approved tables in one repeatable-read read-only transaction."""
    if window_start >= window_end:
        raise ExportError("Export window must have a positive duration.")
    tables: dict[str, list[dict]] = {}
    manifest: dict[str, dict] = {}
    cursor = source_connection.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for plan in TABLE_PLANS:
            rows = _rows_for_plan(
                cursor, plan, window_start=window_start, window_end=window_end
            )
            identities = {tuple(row[field] for field in plan.key_fields) for row in rows}
            if len(identities) != len(rows):
                raise ExportError(f"{plan.name} snapshot contains duplicate approved keys")
            tables[plan.name] = rows
            manifest[plan.name] = {
                "row_count": len(rows),
                "source_snapshot_row_count": len(rows),
                "primary_key_distinct_count": len(identities),
                "checksum_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
                "fields": list(plan.fields),
                "key_fields": list(plan.key_fields),
            }
        source_connection.rollback()
    except Exception:
        source_connection.rollback()
        raise
    finally:
        cursor.close()
    return {
        "schema_version": BUNDLE_SCHEMA,
        "source": dict(APPROVED_SOURCE),
        "tables": tables,
        "manifest": manifest,
    }


def encrypt_bundle(bundle: Mapping[str, Any], kms_client: Any, *, key_id: str) -> tuple[bytes, str]:
    """Encrypt a bundle mapping. See ``encrypt_plaintext`` for the contract."""
    return encrypt_plaintext(canonical(bundle), kms_client, key_id=key_id)


def encrypt_plaintext(plaintext: bytes, kms_client: Any, *, key_id: str) -> tuple[bytes, str]:
    """Encrypt exact plaintext bytes, returning the envelope and its sha256.

    Operates on bytes rather than re-serializing so the checksum published as
    the importer's ``ExpectedPlaintextSha256`` covers precisely what was
    sealed. The data key is requested WITHOUT an encryption context because
    the importer decrypts without one; adding a context here would fail
    decrypt.
    """
    plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
    aad = {
        "schema": ENVELOPE_SCHEMA,
        "bucket": APPROVED_BUCKET,
        "prefix": APPROVED_PREFIX,
    }
    key_response = kms_client.generate_data_key(KeyId=key_id, KeySpec="AES_256")
    key = bytearray(key_response["Plaintext"])
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(AES_GCM_NONCE_BYTES)
        ciphertext = AESGCM(bytes(key)).encrypt(nonce, plaintext, canonical(aad))
    finally:
        for index in range(len(key)):
            key[index] = 0
        key_response["Plaintext"] = b""
    envelope = canonical(
        {
            "schema_version": ENVELOPE_SCHEMA,
            "aad": aad,
            "encrypted_data_key": base64.b64encode(key_response["CiphertextBlob"]).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }
    )
    if len(envelope) > MAX_ENCRYPTED_BYTES:
        raise ExportError("Encrypted bundle exceeds the approved size limit")
    return envelope, plaintext_sha256


def stage_object(
    s3_client: Any,
    envelope: bytes,
    *,
    object_key: str,
    kms_key_id: str,
) -> None:
    if not object_key.startswith(APPROVED_PREFIX) or not object_key.endswith(".json.enc"):
        raise ExportError("Staged object key is outside the approved prefix")
    s3_client.put_object(
        Bucket=APPROVED_BUCKET,
        Key=object_key,
        Body=envelope,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=kms_key_id,
        ContentType="application/octet-stream",
    )


def summarize(bundle: Mapping[str, Any], plaintext_sha256: str, object_key: str) -> dict:
    """Operator evidence. Counts only -- never row content."""
    return {
        "status": "staged",
        "object_key": object_key,
        "plaintext_sha256": plaintext_sha256,
        "table_counts": {
            name: entry["row_count"] for name, entry in bundle["manifest"].items()
        },
    }
