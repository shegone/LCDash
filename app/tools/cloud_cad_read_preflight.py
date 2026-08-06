"""Local-only preflight for the dormant CentralSquare cloud read contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from app.integrations.cad.cloud_read_config import (
    CENTRALSQUARE_SECRET_ARN_PREFIX,
    CloudCadReadConfig,
)


ACCOUNT_ID = "862772137583"
REGION = "us-east-1"
TENANT_ID = "logan-synthetic"
SECRET_ARN = re.compile(re.escape(CENTRALSQUARE_SECRET_ARN_PREFIX) + r"-[A-Za-z0-9]{6}$")
PUBLIC_HOST = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{5,255}$")
class CloudCadPreflightError(ValueError):
    """Raised for unsafe manifest structure or forbidden configuration."""


@dataclass(frozen=True, slots=True)
class CloudCadReadinessReport:
    manifest_id: str
    ready_for_activation_review: bool
    activation_authorized: bool
    errors: tuple[str, ...]
    tenant_id: str
    account_id: str
    region: str
    secret_reference: str
    approved_hostnames: tuple[str, ...]
    poll_seconds: int | None
    reconciliation_overlap_seconds: int | None
    webhooks_enabled: bool
    verified_requirements: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _closed(
    value: object,
    *,
    name: str,
    allowed: set[str],
    errors: list[str],
) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{name} must be an object")
        return {}
    unknown = set(value) - allowed
    if unknown:
        raise CloudCadPreflightError(f"{name} contains forbidden or unknown fields")
    return value


def _utc_timestamp(value: object, name: str, errors: list[str]) -> None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            raise ValueError
    except ValueError:
        errors.append(f"{name} must be an ISO-8601 UTC timestamp")


def _public_endpoint(
    endpoint: Mapping[str, Any],
    *,
    name: str,
    errors: list[str],
) -> tuple[str, str]:
    url = str(endpoint.get("url", ""))
    declared = str(endpoint.get("approved_hostname", "")).lower()
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
    ):
        errors.append(f"{name} must be credential-free HTTPS on port 443 without query or fragment")
    if host != declared:
        errors.append(f"{name} hostname does not match its approved hostname")
    if not PUBLIC_HOST.fullmatch(host) or host.endswith((".local", ".internal", ".lan", ".home", ".invalid", ".test", ".example")):
        errors.append(f"{name} must use a syntactically public DNS hostname")
    if not SAFE_REFERENCE.fullmatch(str(endpoint.get("tls_review_reference", ""))):
        errors.append(f"{name} requires a non-secret TLS review reference")
    return url, host


def evaluate_cloud_cad_read_preflight(
    manifest_path: str | Path,
    *,
    repository_root: str | Path,
) -> CloudCadReadinessReport:
    """Validate one explicit nonsecret manifest; never probe or activate."""
    root = Path(repository_root).resolve(strict=True)
    path = Path(manifest_path).resolve(strict=True)
    if not _inside(root, path) or path.suffix.lower() != ".json":
        raise CloudCadPreflightError("preflight manifest must be JSON inside LCDash-AWS")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CloudCadPreflightError("preflight manifest must be a JSON object")
    allowed_top = {
        "schema_version", "manifest_id", "binding", "secret_reference",
        "endpoints", "polling", "vendor_evidence",
    }
    if set(payload) - allowed_top:
        raise CloudCadPreflightError("preflight contains forbidden or unknown fields")

    errors: list[str] = []
    manifest_id = str(payload.get("manifest_id", ""))
    if payload.get("schema_version") != "lcdash.cloud-cad-read-preflight.v1":
        errors.append("unsupported schema_version")
    if not SAFE_REFERENCE.fullmatch(manifest_id):
        errors.append("manifest_id is invalid")

    binding = _closed(
        payload.get("binding"),
        name="binding",
        allowed={"account_id", "region", "tenant_id", "provider", "mode"},
        errors=errors,
    )
    expected_binding = {
        "account_id": ACCOUNT_ID,
        "region": REGION,
        "tenant_id": TENANT_ID,
        "provider": "centralsquare",
        "mode": "centralsquare-read-poll",
    }
    for key, expected in expected_binding.items():
        if binding.get(key) != expected:
            errors.append(f"binding {key} does not match the reviewed cloud read path")

    secret_reference = str(payload.get("secret_reference", ""))
    if not SECRET_ARN.fullmatch(secret_reference):
        errors.append("secret_reference must be the exact approved account/region/name ARN with AWS suffix")

    endpoints = _closed(
        payload.get("endpoints"),
        name="endpoints",
        allowed={"token", "cad", "system"},
        errors=errors,
    )
    endpoint_urls: dict[str, str] = {}
    endpoint_hosts: list[str] = []
    for key in ("token", "cad", "system"):
        endpoint = _closed(
            endpoints.get(key),
            name=f"endpoints.{key}",
            allowed={"url", "approved_hostname", "tls_review_reference"},
            errors=errors,
        )
        url, host = _public_endpoint(endpoint, name=f"endpoints.{key}", errors=errors)
        endpoint_urls[key] = url
        if host:
            endpoint_hosts.append(host)

    polling = _closed(
        payload.get("polling"),
        name="polling",
        allowed={"poll_seconds", "reconciliation_overlap_seconds", "webhooks_enabled"},
        errors=errors,
    )
    poll_seconds = polling.get("poll_seconds")
    overlap = polling.get("reconciliation_overlap_seconds")
    webhooks = polling.get("webhooks_enabled")
    if webhooks is not False:
        errors.append("webhooks_enabled must be false")

    vendor = _closed(
        payload.get("vendor_evidence"),
        name="vendor_evidence",
        allowed={
            "approval_reference", "approved_at", "approved_hostnames",
            "commercial_aws_access_allowed", "concurrent_use_allowed",
            "polling_allowed", "source_ip_requirement",
            "rate_limit_requests_per_minute", "token_lifetime_seconds",
            "maximum_page_size", "evidence_owner",
        },
        errors=errors,
    )
    for key in ("approval_reference", "evidence_owner"):
        if not SAFE_REFERENCE.fullmatch(str(vendor.get(key, ""))):
            errors.append(f"vendor {key} is missing or invalid")
    _utc_timestamp(vendor.get("approved_at"), "vendor approved_at", errors)
    for key in ("commercial_aws_access_allowed", "concurrent_use_allowed", "polling_allowed"):
        if vendor.get(key) is not True:
            errors.append(f"vendor {key} evidence must be true")
    if vendor.get("source_ip_requirement") not in {"none", "stable-egress-required"}:
        errors.append("vendor source_ip_requirement must be explicit")
    for key in ("rate_limit_requests_per_minute", "token_lifetime_seconds", "maximum_page_size"):
        value = vendor.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(f"vendor {key} must be a positive integer")
    allowed_hosts = vendor.get("approved_hostnames")
    if not isinstance(allowed_hosts, list) or any(not isinstance(item, str) for item in allowed_hosts):
        errors.append("vendor approved_hostnames must be a list of DNS names")
        allowed_hosts = []
    normalized_hosts = tuple(sorted({item.lower() for item in allowed_hosts}))
    if normalized_hosts != tuple(sorted(set(endpoint_hosts))):
        errors.append("vendor approved_hostnames must exactly match all endpoint hosts")

    try:
        config = CloudCadReadConfig.from_mapping(
            {
                "mode": binding.get("mode", ""),
                "tenant_id": binding.get("tenant_id", ""),
                "secret_reference": secret_reference,
                "token_url": endpoint_urls.get("token", ""),
                "cad_base_url": endpoint_urls.get("cad", ""),
                "system_base_url": endpoint_urls.get("system", ""),
                "poll_seconds": poll_seconds,
                "reconciliation_overlap_seconds": overlap,
                "webhooks_enabled": webhooks,
            }
        )
    except (TypeError, ValueError) as exc:
        errors.append(f"dormant cloud read configuration rejected: {exc}")
        config = None

    ready = not errors and config is not None and config.activation_ready
    requirements = (
        "Exact Logan tenant, AWS account, us-east-1, and CentralSquare read-poll binding.",
        "Exact approved Secrets Manager ARN reference metadata; no value retrieval.",
        "Three vendor-approved public HTTPS hostnames with reviewed TLS metadata.",
        "Polling between 15-300 seconds with overlap from one poll through 900 seconds.",
        "Webhooks and all write, acknowledgement, dispatch, alert, page, and tone paths absent.",
        "Vendor evidence for commercial AWS, concurrent use, polling, rate, token, page, and source-IP rules.",
    )
    return CloudCadReadinessReport(
        manifest_id=manifest_id,
        ready_for_activation_review=ready,
        activation_authorized=False,
        errors=tuple(errors),
        tenant_id=TENANT_ID,
        account_id=ACCOUNT_ID,
        region=REGION,
        secret_reference=secret_reference if SECRET_ARN.fullmatch(secret_reference) else "",
        approved_hostnames=normalized_hosts if ready else (),
        poll_seconds=poll_seconds if isinstance(poll_seconds, int) else None,
        reconciliation_overlap_seconds=overlap if isinstance(overlap, int) else None,
        webhooks_enabled=False,
        verified_requirements=requirements,
    )
