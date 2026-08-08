"""Shared, dependency-free constants for the Phase 2 analytics transfer.

Stage 1 of the export runs inside the on-prem application container, which has
psycopg and the stdlib but no boto3 or cryptography. Importing
``phase2_analytics_import_runtime`` there would fail at module load, so the
values both sides must agree on live here instead, with no third-party imports.

``phase2_analytics_import_runtime`` deliberately keeps its own copies: it is the
reviewed, fail-closed side of the transfer and is not edited to support
tooling. ``test_phase2_analytics_export`` asserts the two definitions stay
identical, so drift fails in CI rather than at a live import.
"""

from __future__ import annotations

import json

ENVELOPE_SCHEMA = "lcdash.analytics-history.envelope.v1"
BUNDLE_SCHEMA = "lcdash.analytics-history.bundle.v1"
APPROVED_BUCKET = "lcdash-p1-logan-use1-862772137583-analytics-import-staging"
APPROVED_PREFIX = "tenants/logan-synthetic/historical-analytics/"
MAX_ENCRYPTED_BYTES = 16 * 1024 * 1024


def canonical(value: object) -> bytes:
    """Deterministic JSON bytes: sorted keys, no whitespace, UTF-8.

    Both the AES-GCM associated data and every checksum are computed over this
    encoding, so it must match the importer's byte for byte.
    """
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
