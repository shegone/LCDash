"""Local-only, manifest-driven intake gate for the cloud document library."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping


BUCKET_NAME = "lcdash-p1-logan-use1-862772137583-document-library"
STAGING_ROOT = PurePosixPath("work/document-intake-staging")
APPROVED_PREFIXES = (
    "tenants/logan-synthetic/document-library/centralsquare/current/",
    "tenants/logan-synthetic/document-library/mindshare/current/",
    "tenants/logan-synthetic/document-library/mindshare/sanitized-system/",
    "tenants/logan-synthetic/document-library/mindshare/software-catalog/",
    "tenants/logan-synthetic/document-library/manifests/approved/",
)
ALLOWED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md", ".json", ".xml", ".csv", ".yaml", ".yml"})
PREFIX_SUFFIXES = {
    APPROVED_PREFIXES[0]: frozenset({".pdf"}),
    APPROVED_PREFIXES[1]: ALLOWED_SUFFIXES,
    APPROVED_PREFIXES[2]: ALLOWED_SUFFIXES,
    APPROVED_PREFIXES[3]: frozenset({".json", ".csv"}),
    APPROVED_PREFIXES[4]: frozenset({".json"}),
}
MAX_FILE_BYTES = 25 * 1024 * 1024
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{5,127}$")
FORBIDDEN_PATH_TERMS = (
    "credential",
    "password",
    "secret",
    "token",
    "api-key",
    "api_key",
    "private-key",
    "private_key",
    ".env",
    "backup",
    "database-dump",
    "recovery-bundle",
    "vendor-archive",
    "raw-cad",
    "cad-payload",
    "webhook-body",
    "incident-record",
    "unit-record",
    "recording",
    "transcript",
    "station-alert",
    "public-warning",
    "acknowledgement",
    "esinet",
    "firmware",
    "model-file",
    "operational-output",
)
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".exe", ".msi", ".dll", ".bin", ".iso", ".img", ".zip", ".7z",
        ".tar", ".gz", ".bak", ".sql", ".db", ".sqlite", ".pem", ".key",
        ".pfx", ".pt", ".pth", ".onnx", ".gguf", ".wav", ".mp3", ".m4a",
        ".ogg", ".webm", ".mp4", ".geojson",
    }
)
FORBIDDEN_CATEGORIES = frozenset(
    {
        "credentials",
        "secrets",
        "backups",
        "binaries",
        "models",
        "operational-output",
        "raw-cad",
        "recordings",
        "protected-data",
    }
)


def matches_forbidden_path_term(value: str) -> bool:
    """Match exclusion terms without treating text inside normal words as tokens."""
    lower = value.lower()
    for term in FORBIDDEN_PATH_TERMS:
        if term in {"backup", "firmware"}:
            if term in lower:
                return True
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lower):
            return True
    return False


class IntakeManifestError(ValueError):
    """Raised for an unreadable or structurally unsafe intake request."""


@dataclass(frozen=True, slots=True)
class DryRunUploadItem:
    staged_path: str
    bucket: str
    destination_key: str
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class IntakeReport:
    manifest_id: str
    valid: bool
    errors: tuple[str, ...]
    dry_run_upload_plan: tuple[DryRunUploadItem, ...]
    upload_authorized: bool
    later_ingestion_eligible: bool
    eligibility_reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["dry_run_upload_plan"] = [
            asdict(item) for item in self.dry_run_upload_plan
        ]
        return payload


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            if size > MAX_FILE_BYTES:
                raise IntakeManifestError("staged file exceeds the 25 MiB intake limit")
            digest.update(block)
    return digest.hexdigest(), size


def _load_manifest(manifest_path: Path, repository_root: Path) -> Mapping[str, Any]:
    resolved = manifest_path.resolve(strict=True)
    if not _within(repository_root, resolved):
        raise IntakeManifestError("manifest must be inside the LCDash-AWS repository")
    if resolved.suffix.lower() != ".json":
        raise IntakeManifestError("intake manifest must be JSON")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IntakeManifestError("intake manifest must be a JSON object")
    return payload


def evaluate_document_intake(
    manifest_path: str | Path,
    *,
    repository_root: str | Path,
) -> IntakeReport:
    """Validate only manifest-listed staged files and return a non-executing plan."""
    root = Path(repository_root).resolve(strict=True)
    payload = _load_manifest(Path(manifest_path), root)
    allowed_top = {"schema_version", "manifest_id", "package_root", "admission", "files"}
    unknown = set(payload) - allowed_top
    if unknown:
        raise IntakeManifestError("unknown manifest fields: " + ", ".join(sorted(unknown)))

    manifest_id = str(payload.get("manifest_id", ""))
    errors: list[str] = []
    if payload.get("schema_version") != "lcdash.document-intake.v1":
        errors.append("unsupported schema_version")
    if not SAFE_ID.fullmatch(manifest_id):
        errors.append("manifest_id is invalid")

    package_value = str(payload.get("package_root", ""))
    package_posix = PurePosixPath(package_value)
    if package_posix.is_absolute() or ".." in package_posix.parts or package_posix != STAGING_ROOT:
        errors.append("package_root must be work/document-intake-staging")
    package_root = (root / Path(*package_posix.parts)).resolve()
    if not _within(root, package_root):
        errors.append("package_root escapes the repository")

    admission = payload.get("admission")
    if not isinstance(admission, dict):
        admission = {}
        errors.append("signed admission decision is required")
    else:
        admission_allowed = {
            "decision", "approval_id", "signed_by", "signed_at", "signature",
            "protected_data_present", "classification_decision",
        }
        if set(admission) - admission_allowed:
            errors.append("unknown admission decision fields")
    if admission.get("decision") != "approved":
        errors.append("admission decision must be approved")
    for key in ("approval_id", "signed_by", "signature"):
        if not SAFE_ID.fullmatch(str(admission.get(key, ""))):
            errors.append(f"admission {key} is missing or invalid")
    try:
        signed_at = datetime.fromisoformat(str(admission.get("signed_at", "")))
        if signed_at.tzinfo is None:
            raise ValueError
    except ValueError:
        errors.append("admission signed_at must include a timezone")
    if admission.get("protected_data_present") is not False:
        errors.append("protected_data_present must be explicitly false")
    if admission.get("classification_decision") != "no-protected-data-approved":
        errors.append("protected-data classification approval is required")

    files = payload.get("files")
    if not isinstance(files, list) or not files:
        errors.append("at least one explicitly listed staged file is required")
        files = []

    plan: list[DryRunUploadItem] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(files):
        label = f"files[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        allowed_fields = {
            "staged_path", "destination_key", "bytes", "sha256", "category",
            "malware_scan", "secret_scan", "human_approved",
        }
        if set(item) - allowed_fields:
            errors.append(f"{label} contains unknown fields")
            continue
        staged_value = str(item.get("staged_path", ""))
        staged_posix = PurePosixPath(staged_value)
        if staged_posix.is_absolute() or ".." in staged_posix.parts or not staged_posix.parts:
            errors.append(f"{label} staged_path is unsafe")
            continue
        source = (package_root / Path(*staged_posix.parts)).resolve()
        if not _within(package_root, source):
            errors.append(f"{label} staged_path escapes package_root")
            continue
        destination = str(item.get("destination_key", ""))
        destination_path = PurePosixPath(destination)
        suffix = destination_path.suffix.lower()
        lower_path = f"{staged_value} {destination}".lower()
        category = str(item.get("category", "")).strip().lower()
        item_errors: list[str] = []
        if destination.startswith("/") or ".." in destination_path.parts:
            item_errors.append("destination key is unsafe")
        if destination in seen_keys:
            item_errors.append("destination key is duplicated")
        seen_keys.add(destination)
        matched_prefix = next(
            (prefix for prefix in APPROVED_PREFIXES if destination.startswith(prefix)),
            None,
        )
        if matched_prefix is None:
            item_errors.append("destination key is outside approved prefixes")
        elif suffix not in PREFIX_SUFFIXES[matched_prefix]:
            item_errors.append("file type is not approved for destination prefix")
        if suffix not in ALLOWED_SUFFIXES or suffix in FORBIDDEN_SUFFIXES:
            item_errors.append("file type is not approved")
        if matches_forbidden_path_term(lower_path):
            item_errors.append("path matches a hard exclusion")
        if category in FORBIDDEN_CATEGORIES or not category:
            item_errors.append("category is missing or excluded")
        if item.get("malware_scan") != "passed":
            item_errors.append("malware scan has not passed")
        if item.get("secret_scan") != "passed":
            item_errors.append("secret scan has not passed")
        if item.get("human_approved") is not True:
            item_errors.append("human file approval is required")
        expected_hash = str(item.get("sha256", ""))
        expected_size = item.get("bytes")
        if not SHA256.fullmatch(expected_hash):
            item_errors.append("sha256 is invalid")
        if not isinstance(expected_size, int) or isinstance(expected_size, bool) or not 0 < expected_size <= MAX_FILE_BYTES:
            item_errors.append("bytes must be between 1 and 25 MiB")
        if source.suffix.lower() != suffix:
            item_errors.append("staged and destination file types differ")
        if not source.is_file():
            item_errors.append("staged file is missing")
        elif not item_errors:
            actual_hash, actual_size = _sha256_and_size(source)
            if actual_size != expected_size:
                item_errors.append("staged file size does not match manifest")
            if actual_hash != expected_hash:
                item_errors.append("staged file sha256 does not match manifest")
        errors.extend(f"{label}: {message}" for message in item_errors)
        if not item_errors:
            plan.append(
                DryRunUploadItem(
                    staged_path=staged_value,
                    bucket=BUCKET_NAME,
                    destination_key=destination,
                    bytes=expected_size,
                    sha256=expected_hash,
                )
            )

    valid = not errors and len(plan) == len(files)
    return IntakeReport(
        manifest_id=manifest_id,
        valid=valid,
        errors=tuple(errors),
        dry_run_upload_plan=tuple(plan) if valid else (),
        upload_authorized=False,
        later_ingestion_eligible=valid,
        eligibility_reason=(
            "Eligible for a later separately authorized upload and ingestion review."
            if valid
            else "Not eligible; all intake errors must be resolved and re-reviewed."
        ),
    )
