"""Deterministic, read-only verification of the Phase 1 authorization gate."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = INFRASTRUCTURE_ROOT.parent
DEFAULT_PATHS = {
    "gate": REPOSITORY_ROOT / "docs" / "planning" / "PACKAGE_5A_AUTHORIZATION_GATE.md",
    "allowlist": INFRASTRUCTURE_ROOT / "phase1_deployment_allowlist.json",
    "preflight": REPOSITORY_ROOT / "docs" / "planning" / "PHASE1_DEPLOYMENT_PREFLIGHT.md",
    "evidence": INFRASTRUCTURE_ROOT / "phase1_gate_evidence.json",
    "boundary": INFRASTRUCTURE_ROOT / "iam" / "LCDashPhase1Boundary.json",
    "deployment_policy": INFRASTRUCTURE_ROOT / "iam" / "LCDashPhase1DeploymentRolePolicy.json",
    "trust": INFRASTRUCTURE_ROOT / "iam" / "LCDashPhase1DeploymentTrustModel.json",
}
EXPECTED_ACCOUNT = "862772137583"
EXPECTED_REGION = "us-east-1"
EXPECTED_STACKS = [
    "lcdash-p1-logan-use1-certificate",
    "lcdash-p1-logan-use1-foundation",
]
REQUIRED_REFERENCES = (
    "approver_reference",
    "approval_decision_reference",
    "source_commit_reference",
    "offline_test_reference",
    "identity_center_assignment_reference",
    "mfa_reference",
    "deployment_role_reference",
    "permissions_boundary_reference",
    "iam_access_analyzer_reference",
    "billing_visibility_reference",
    "budget_subscription_reference",
    "pricing_estimate_reference",
    "quota_review_reference",
    "cloudtrail_decision_reference",
    "authentication_review_reference",
    "hostinger_dns_operator_reference",
    "foundation_parameters_reference",
    "teardown_review_reference",
    "limitations_acceptance_reference",
)
PREWRITE_DECISION = "AUTHORIZED_TO_BEGIN_PHASE1_PREWRITE"
PREWRITE_SCOPE = [
    "reviewed_iam_boundary_and_deployment_access_setup",
    "usd_200_budget_alerts",
    "cdk_bootstrap",
    "certificate_request_stack",
    "manual_hostinger_certificate_validation_hold",
    "foundation_stack_desired_count_0_not_published",
]
PREWRITE_EXCLUSIONS = [
    "image_build_or_push",
    "pilot_service_desired_count_1",
    "live_cad_or_phase_2",
    "production_227_or_pc_15",
    "credentials_or_protected_operational_outputs",
]
PREWRITE_AUTHORIZATION_REFERENCES = (
    "approver_reference",
    "approval_decision_reference",
)
SECRET_PATTERNS = (
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:password|passwd|secret|token|client_secret)\s*[:=]", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@", re.I),
)


@dataclass(frozen=True)
class GateReport:
    status: str
    reasons: tuple[str, ...]
    pending_evidence: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "pending_evidence": list(self.pending_evidence),
        }


def _load_json(path: Path, label: str, reasons: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reasons.append(f"{label} is missing or invalid JSON")
        return {}
    if not isinstance(value, dict):
        reasons.append(f"{label} must be a JSON object")
        return {}
    return value


def _load_text(path: Path, label: str, reasons: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        reasons.append(f"{label} is missing or unreadable")
        return ""


def _secret_looking(value: str) -> bool:
    return any(pattern.search(value) for pattern in SECRET_PATTERNS)


def _iso_datetime(value: Any, field: str, reasons: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        reasons.append(f"evidence field {field} is missing")
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        reasons.append(f"evidence field {field} is not ISO-8601")
        return None
    if parsed.tzinfo is None:
        reasons.append(f"evidence field {field} must include a time-zone offset")
        return None
    return parsed


def verify_gate(paths: dict[str, Path] | None = None) -> GateReport:
    """Verify only recorded files; never infer approval or contact external systems."""
    selected = {key: Path(value) for key, value in (paths or DEFAULT_PATHS).items()}
    reasons: list[str] = []
    gate = _load_text(selected["gate"], "authorization gate", reasons)
    preflight = _load_text(selected["preflight"], "deployment preflight", reasons)
    allowlist = _load_json(selected["allowlist"], "deployment allowlist", reasons)
    evidence = _load_json(selected["evidence"], "gate evidence", reasons)
    boundary = _load_json(selected["boundary"], "permissions boundary", reasons)
    deployment = _load_json(
        selected["deployment_policy"], "deployment role policy", reasons
    )
    trust = _load_json(selected["trust"], "deployment trust model", reasons)

    prewrite_mode = evidence.get("decision") == PREWRITE_DECISION
    if prewrite_mode:
        if "- [x] **PHASE 1 PRE-WRITE SEQUENCE AUTHORIZED**" not in gate:
            reasons.append("pre-write authorization checkbox is not explicitly checked")
        if "- [x] **PHASE 1 PRE-WRITE SEQUENCE NOT AUTHORIZED**" in gate:
            reasons.append("authorization gate explicitly blocks the pre-write sequence")
        if allowlist.get("gate_status") != PREWRITE_DECISION:
            reasons.append("deployment allowlist does not authorize the pre-write sequence")
        if f"Status: **{PREWRITE_DECISION}**" not in preflight:
            reasons.append("deployment preflight does not authorize the pre-write sequence")
        if evidence.get("authorization_scope") != PREWRITE_SCOPE:
            reasons.append("gate evidence pre-write scope does not match the exact approved sequence")
        if evidence.get("explicitly_not_authorized") != PREWRITE_EXCLUSIONS:
            reasons.append("gate evidence does not preserve the exact pre-write exclusions")
        activation = allowlist.get("service_activation", {})
        if activation.get("initial_foundation_value") != 0:
            reasons.append("pre-write scope does not keep initial desired count at zero")
        if activation.get("initial_image_digest_value") != "NOT_PUBLISHED":
            reasons.append("pre-write scope does not keep the image dormant")
        for field in PREWRITE_AUTHORIZATION_REFERENCES:
            value = evidence.get(field)
            if not isinstance(value, str) or not value.strip():
                reasons.append(f"authorization reference {field} is missing")
            elif _secret_looking(value):
                reasons.append(
                    f"authorization reference {field} contains prohibited secret-looking material"
                )
        if evidence.get("post_action_status") != "PENDING":
            reasons.append("post-action evidence status must remain PENDING at authorization-to-begin")
    else:
        if "- [x] **PHASE 1 AUTHORIZED**" not in gate:
            reasons.append("authorization gate decision checkbox is not explicitly authorized")
        if "- [x] **PHASE 1 NOT AUTHORIZED**" in gate:
            reasons.append("authorization gate explicitly records not authorized")
        if allowlist.get("gate_status") != "AUTHORIZED":
            reasons.append("deployment allowlist gate_status is not AUTHORIZED")
        if "Status: **AUTHORIZED**" not in preflight:
            reasons.append("deployment preflight status is not explicitly AUTHORIZED")
        if evidence.get("decision") != "AUTHORIZED":
            reasons.append("gate evidence decision is not AUTHORIZED")

    for label, document in (
        ("permissions boundary", boundary),
        ("deployment role policy", deployment),
    ):
        if document.get("Version") != "2012-10-17" or not isinstance(
            document.get("Statement"), list
        ):
            reasons.append(f"{label} metadata is incomplete")

    if not prewrite_mode:
        if trust.get("status") != "HUMAN_REVIEWED_ATTACHED":
            reasons.append("deployment trust model is not HUMAN_REVIEWED_ATTACHED")
        if not trust.get("principal_arn"):
            reasons.append("deployment trust model principal_arn is missing")
        if trust.get("assignment", {}).get("status") != "HUMAN_REVIEWED":
            reasons.append("deployment trust model assignment is not HUMAN_REVIEWED")

    for label, document in (
        ("deployment allowlist", allowlist),
        ("gate evidence", evidence),
        ("deployment trust model", trust),
    ):
        if document.get("account") != EXPECTED_ACCOUNT:
            reasons.append(f"{label} account does not match {EXPECTED_ACCOUNT}")
        if document.get("region") != EXPECTED_REGION:
            reasons.append(f"{label} region does not match {EXPECTED_REGION}")

    allowlist_stacks = [
        item.get("stack")
        for item in allowlist.get("deployment_sequence", [])
        if isinstance(item, dict)
    ]
    if allowlist_stacks != EXPECTED_STACKS:
        reasons.append("deployment allowlist stack order does not match approved sequence")
    if evidence.get("stacks") != EXPECTED_STACKS:
        reasons.append("gate evidence stack order does not match approved sequence")

    pending_evidence: list[str] = []
    for field in REQUIRED_REFERENCES:
        value = evidence.get(field)
        if prewrite_mode and field not in PREWRITE_AUTHORIZATION_REFERENCES:
            if not isinstance(value, str) or not value.strip():
                pending_evidence.append(field)
            elif _secret_looking(value):
                reasons.append(f"evidence reference {field} contains prohibited secret-looking material")
        elif not isinstance(value, str) or not value.strip():
            reasons.append(f"evidence reference {field} is missing")
        elif _secret_looking(value):
            reasons.append(f"evidence reference {field} contains prohibited secret-looking material")

    verification_time = _iso_datetime(
        evidence.get("verification_time"), "verification_time", reasons
    )
    window_start = _iso_datetime(
        evidence.get("approval_window_start"), "approval_window_start", reasons
    )
    window_expiration = _iso_datetime(
        evidence.get("approval_window_expiration"),
        "approval_window_expiration",
        reasons,
    )
    if verification_time and window_start and window_expiration:
        if not window_start <= verification_time <= window_expiration:
            reasons.append("verification_time is outside the recorded approval window")

    unique_reasons = tuple(dict.fromkeys(reasons))
    status = "BLOCKED"
    if not unique_reasons:
        status = "AUTHORIZED_TO_BEGIN" if prewrite_mode else "PASS"
    return GateReport(status, unique_reasons, tuple(pending_evidence))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify recorded non-secret Phase 1 gate evidence locally."
    )
    parser.add_argument("--json", action="store_true", help="emit sanitized JSON")
    args = parser.parse_args(argv)
    report = verify_gate()
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(f"Phase 1 gate: {report.status}")
        for reason in report.reasons:
            print(f"- {reason}")
    return 0 if report.status in {"PASS", "AUTHORIZED_TO_BEGIN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
