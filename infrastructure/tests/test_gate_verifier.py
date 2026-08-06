import json
from pathlib import Path
import tempfile
import unittest

from infrastructure.tools.verify_phase1_gate import (
    EXPECTED_ACCOUNT,
    EXPECTED_REGION,
    EXPECTED_STACKS,
    PREWRITE_DECISION,
    PREWRITE_EXCLUSIONS,
    PREWRITE_SCOPE,
    REQUIRED_REFERENCES,
    verify_gate,
)


class Phase1GateVerifierTests(unittest.TestCase):
    def _fixture(self, root: Path) -> dict[str, Path]:
        paths = {
            "gate": root / "gate.md",
            "allowlist": root / "allowlist.json",
            "preflight": root / "preflight.md",
            "evidence": root / "evidence.json",
            "boundary": root / "boundary.json",
            "deployment_policy": root / "deployment.json",
            "trust": root / "trust.json",
        }
        paths["gate"].write_text("- [x] **PHASE 1 AUTHORIZED**\n", encoding="utf-8")
        paths["preflight"].write_text("Status: **AUTHORIZED**\n", encoding="utf-8")
        self._write(paths["allowlist"], {
            "gate_status": "AUTHORIZED",
            "account": EXPECTED_ACCOUNT,
            "region": EXPECTED_REGION,
            "deployment_sequence": [{"stack": stack} for stack in EXPECTED_STACKS],
        })
        evidence = {
            "decision": "AUTHORIZED",
            "verification_time": "2026-08-05T12:00:00-04:00",
            "approval_window_start": "2026-08-04T00:00:00-04:00",
            "approval_window_expiration": "2026-08-06T00:00:00-04:00",
            "account": EXPECTED_ACCOUNT,
            "region": EXPECTED_REGION,
            "stacks": EXPECTED_STACKS,
        }
        evidence.update({field: f"evidence://review/{field}" for field in REQUIRED_REFERENCES})
        self._write(paths["evidence"], evidence)
        policy = {"Version": "2012-10-17", "Statement": []}
        self._write(paths["boundary"], policy)
        self._write(paths["deployment_policy"], policy)
        self._write(paths["trust"], {
            "status": "HUMAN_REVIEWED_ATTACHED",
            "account": EXPECTED_ACCOUNT,
            "region": EXPECTED_REGION,
            "principal_arn": "arn:aws:iam::862772137583:role/example-reviewed-role",
            "assignment": {"status": "HUMAN_REVIEWED"},
        })
        return paths

    @staticmethod
    def _write(path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_current_repository_is_authorized_to_begin_with_evidence_pending(self) -> None:
        report = verify_gate()
        self.assertEqual("AUTHORIZED_TO_BEGIN", report.status)
        self.assertEqual((), report.reasons)
        self.assertIn("source_commit_reference", report.pending_evidence)
        self.assertIn("identity_center_assignment_reference", report.pending_evidence)

    def test_complete_synthetic_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = verify_gate(self._fixture(Path(temporary)))
        self.assertEqual("PASS", report.status)
        self.assertEqual((), report.reasons)
        self.assertEqual((), report.pending_evidence)

    def test_prewrite_authorization_requires_explicit_checkbox_and_exact_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            paths["gate"].write_text(
                "- [ ] **PHASE 1 PRE-WRITE SEQUENCE AUTHORIZED**\n",
                encoding="utf-8",
            )
            paths["preflight"].write_text(
                f"Status: **{PREWRITE_DECISION}**\n", encoding="utf-8"
            )
            allowlist = json.loads(paths["allowlist"].read_text(encoding="utf-8"))
            allowlist["gate_status"] = PREWRITE_DECISION
            allowlist["service_activation"] = {
                "initial_foundation_value": 0,
                "initial_image_digest_value": "NOT_PUBLISHED",
            }
            self._write(paths["allowlist"], allowlist)
            evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
            evidence.update(
                {
                    "decision": PREWRITE_DECISION,
                    "authorization_scope": PREWRITE_SCOPE,
                    "explicitly_not_authorized": PREWRITE_EXCLUSIONS,
                    "post_action_status": "PENDING",
                }
            )
            for field in REQUIRED_REFERENCES:
                if field not in {"approver_reference", "approval_decision_reference"}:
                    evidence[field] = None
            self._write(paths["evidence"], evidence)
            report = verify_gate(paths)
        self.assertEqual("BLOCKED", report.status)
        self.assertIn("pre-write authorization checkbox", "\n".join(report.reasons))

    def test_prewrite_authorization_does_not_require_post_action_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            paths["gate"].write_text(
                "- [x] **PHASE 1 PRE-WRITE SEQUENCE AUTHORIZED**\n",
                encoding="utf-8",
            )
            paths["preflight"].write_text(
                f"Status: **{PREWRITE_DECISION}**\n", encoding="utf-8"
            )
            allowlist = json.loads(paths["allowlist"].read_text(encoding="utf-8"))
            allowlist["gate_status"] = PREWRITE_DECISION
            allowlist["service_activation"] = {
                "initial_foundation_value": 0,
                "initial_image_digest_value": "NOT_PUBLISHED",
            }
            self._write(paths["allowlist"], allowlist)
            evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
            evidence.update(
                {
                    "decision": PREWRITE_DECISION,
                    "authorization_scope": PREWRITE_SCOPE,
                    "explicitly_not_authorized": PREWRITE_EXCLUSIONS,
                    "post_action_status": "PENDING",
                }
            )
            for field in REQUIRED_REFERENCES:
                if field not in {"approver_reference", "approval_decision_reference"}:
                    evidence[field] = None
            self._write(paths["evidence"], evidence)
            trust = json.loads(paths["trust"].read_text(encoding="utf-8"))
            trust.update({"status": "HUMAN_REVIEW_REQUIRED_NOT_ATTACHED", "principal_arn": None})
            trust["assignment"] = {"status": "HUMAN_REQUIRED"}
            self._write(paths["trust"], trust)
            report = verify_gate(paths)
        self.assertEqual("AUTHORIZED_TO_BEGIN", report.status)
        self.assertEqual((), report.reasons)
        self.assertIn("deployment_role_reference", report.pending_evidence)

    def test_account_region_and_stack_mismatches_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
            evidence.update({"account": "000000000000", "region": "us-west-2", "stacks": list(reversed(EXPECTED_STACKS))})
            self._write(paths["evidence"], evidence)
            report = verify_gate(paths)
        joined = "\n".join(report.reasons)
        self.assertEqual("BLOCKED", report.status)
        self.assertIn("gate evidence account", joined)
        self.assertIn("gate evidence region", joined)
        self.assertIn("gate evidence stack order", joined)

    def test_secret_looking_reference_is_rejected_without_echo(self) -> None:
        leaked = "password=UltraSecret123"
        with tempfile.TemporaryDirectory() as temporary:
            paths = self._fixture(Path(temporary))
            evidence = json.loads(paths["evidence"].read_text(encoding="utf-8"))
            evidence["approver_reference"] = leaked
            self._write(paths["evidence"], evidence)
            report = verify_gate(paths)
        serialized = json.dumps(report.as_dict())
        self.assertEqual("BLOCKED", report.status)
        self.assertIn("approver_reference contains prohibited secret-looking material", serialized)
        self.assertNotIn(leaked, serialized)
        self.assertNotIn("UltraSecret123", serialized)


if __name__ == "__main__":
    unittest.main()
