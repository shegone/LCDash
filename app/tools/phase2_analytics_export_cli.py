"""Stage 2 of the analytics backfill: validate, encrypt, and stage the bundle.

Reads the canonical bundle produced by ``phase2_analytics_export_source`` from
stdin, runs the importer's OWN validator against it as a pre-flight, encrypts
it client-side, and uploads the envelope to the approved staging prefix.

Running the real ``validate_bundle`` locally means a contract violation is
caught here -- before any protected data is uploaded and before a Fargate task
is spent -- rather than surfacing as an opaque failure mid-import.

Protected data handling: the bundle is held in memory only, never written to
disk, and never echoed. Output is limited to counts and checksums.

The printed ``plaintext_sha256`` is the value the import stack requires as its
``ExpectedPlaintextSha256`` parameter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

from app.tools.phase2_analytics_export import (
    ExportError,
    encrypt_plaintext,
    stage_object,
)
from app.tools.phase2_analytics_import_runtime import (
    APPROVED_BUCKET,
    APPROVED_PREFIX,
    MAX_ENCRYPTED_BYTES,
    validate_bundle,
)


def preflight(plaintext: bytes) -> tuple[str, dict[str, int]]:
    """Run the importer's real validator before anything leaves this process."""
    plaintext_sha256 = hashlib.sha256(plaintext).hexdigest()
    _tables, counts = validate_bundle(plaintext, plaintext_sha256)
    return plaintext_sha256, counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--object-key",
        required=True,
        help=f"Staged key under {APPROVED_PREFIX} ending in .json.enc",
    )
    parser.add_argument(
        "--kms-key-id",
        required=True,
        help="Approved CMK id/ARN used to wrap the data key and for SSE-KMS.",
    )
    parser.add_argument(
        "--region", default="us-east-1", help="AWS region (default us-east-1)."
    )
    parser.add_argument(
        "--profile", default="", help="Optional AWS profile name."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report only; do not encrypt, contact AWS, or upload.",
    )
    args = parser.parse_args(argv)

    plaintext = sys.stdin.buffer.read()
    if not plaintext:
        print("No bundle was received on stdin.", file=sys.stderr)
        return 2

    try:
        plaintext_sha256, counts = preflight(plaintext)
    except Exception as error:  # validator raises without row content
        print(f"Bundle failed pre-flight validation: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "validated",
                    "uploaded": False,
                    "plaintext_sha256": plaintext_sha256,
                    "plaintext_bytes": len(plaintext),
                    "table_counts": counts,
                },
                sort_keys=True,
            )
        )
        return 0

    import boto3

    session = (
        boto3.session.Session(profile_name=args.profile, region_name=args.region)
        if args.profile
        else boto3.session.Session(region_name=args.region)
    )
    try:
        envelope, sealed_sha256 = encrypt_plaintext(
            plaintext, session.client("kms"), key_id=args.kms_key_id
        )
        if sealed_sha256 != plaintext_sha256:
            raise ExportError("Sealed checksum diverged from the validated bundle")
        if len(envelope) > MAX_ENCRYPTED_BYTES:
            raise ExportError("Encrypted bundle exceeds the approved size limit")
        stage_object(
            session.client("s3"),
            envelope,
            object_key=args.object_key,
            kms_key_id=args.kms_key_id,
        )
    except ExportError as error:
        print(f"Staging refused: {error}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": "staged",
                "uploaded": True,
                "bucket": APPROVED_BUCKET,
                "object_key": args.object_key,
                "plaintext_sha256": plaintext_sha256,
                "encrypted_bytes": len(envelope),
                "table_counts": counts,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
