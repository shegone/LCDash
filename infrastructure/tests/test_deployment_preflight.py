from collections import Counter
import json
from pathlib import Path
import sys
import unittest


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = INFRASTRUCTURE_ROOT.parent
sys.path.insert(0, str(INFRASTRUCTURE_ROOT))

try:
    import aws_cdk as cdk
    from aws_cdk.assertions import Template
    from lcdash_pilot.certificate_stack import Phase1CertificateStack
    from lcdash_pilot.foundation_stack import Phase1FoundationStack
except ImportError:
    cdk = None


@unittest.skipUnless(cdk is not None, "aws-cdk-lib is not installed")
class DeploymentPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.allowlist = json.loads(
            (INFRASTRUCTURE_ROOT / "phase1_deployment_allowlist.json").read_text(
                encoding="utf-8"
            )
        )
        app = cdk.App()
        environment = cdk.Environment(
            account=cls.allowlist["account"],
            region=cls.allowlist["region"],
        )
        certificate = Phase1CertificateStack(
            app,
            "lcdash-p1-logan-use1-certificate",
            env=environment,
        )
        foundation = Phase1FoundationStack(
            app,
            "lcdash-p1-logan-use1-foundation",
            env=environment,
        )
        cls.templates = {
            "lcdash-p1-logan-use1-certificate": Template.from_stack(
                certificate
            ).to_json(),
            "lcdash-p1-logan-use1-foundation": Template.from_stack(
                foundation
            ).to_json(),
        }

    def test_cdk_version_reporting_metadata_is_disabled(self):
        cdk_config = json.loads(
            (INFRASTRUCTURE_ROOT / "cdk.json").read_text(encoding="utf-8")
        )
        self.assertIs(cdk_config.get("versionReporting"), False)
        for template in self.templates.values():
            self.assertNotIn(
                "AWS::CDK::Metadata",
                {
                    resource["Type"]
                    for resource in template["Resources"].values()
                },
            )

    def test_target_and_two_stack_order_are_exact(self):
        self.assertEqual(self.allowlist["account"], "862772137583")
        self.assertEqual(self.allowlist["partition"], "aws")
        self.assertEqual(self.allowlist["region"], "us-east-1")
        self.assertEqual(
            [item["stack"] for item in self.allowlist["deployment_sequence"]],
            [
                "lcdash-p1-logan-use1-certificate",
                "lcdash-p1-logan-use1-foundation",
            ],
        )

    def test_synthesized_resource_inventory_matches_allowlist_exactly(self):
        for stack_name, template in self.templates.items():
            actual = Counter(
                resource["Type"]
                for resource in template["Resources"].values()
            )
            self.assertEqual(
                dict(sorted(actual.items())),
                self.allowlist["resource_type_counts"][stack_name],
                stack_name,
            )

    def test_prohibited_types_are_absent_from_both_stacks(self):
        actual_types = {
            resource["Type"]
            for template in self.templates.values()
            for resource in template["Resources"].values()
        }
        self.assertTrue(
            actual_types.isdisjoint(self.allowlist["prohibited_resource_types"])
        )

    def test_fixed_foundation_tags_match_stack_source(self):
        source = (
            INFRASTRUCTURE_ROOT / "lcdash_pilot" / "foundation_stack.py"
        ).read_text(encoding="utf-8")
        for key, value in self.allowlist["required_foundation_tags"].items():
            expected_source = (
                '"Region": APPROVED_REGION'
                if key == "Region"
                else f'"{key}": "{value}"'
            )
            self.assertIn(expected_source, source)
        for key in self.allowlist["required_parameter_tags"]:
            self.assertIn(f'"{key}"', source)
        self.assertIn(
            "self._apply_parameter_tags(parameters)",
            source,
        )
        self.assertIn("cdk.Tags.of(construct).add(key, value)", source)
        for key in self.allowlist["required_parameter_tags"]:
            self.assertNotIn(f'cdk.Tags.of(self).add("{key}"', source)

        foundation = self.templates["lcdash-p1-logan-use1-foundation"]
        parameter_refs = {
            "Owner": "Owner",
            "BudgetOwner": "BudgetOwner",
            "CostCenter": "CostCenter",
            "Expiration": "Expiration",
        }
        serialized_resources = json.dumps(foundation["Resources"])
        for tag_key, parameter_name in parameter_refs.items():
            self.assertIn(f'"Key": "{tag_key}"', serialized_resources)
            self.assertIn(f'"Ref": "{parameter_name}"', serialized_resources)

    def test_budget_control_matches_template_and_math(self):
        budget = self.allowlist["budget"]
        self.assertEqual(
            budget["forecast_alert_usd"],
            budget["monthly_limit_usd"] * budget["forecast_alert_percent"] // 100,
        )
        self.assertEqual(
            budget["actual_stop_alert_usd"],
            budget["monthly_limit_usd"] * budget["actual_stop_alert_percent"] // 100,
        )
        self.assertEqual(
            budget["human_review_usd"],
            budget["monthly_limit_usd"] * budget["human_review_percent"] // 100,
        )
        foundation = self.templates["lcdash-p1-logan-use1-foundation"]
        budget_resource = next(
            resource["Properties"]
            for resource in foundation["Resources"].values()
            if resource["Type"] == "AWS::Budgets::Budget"
        )
        self.assertEqual(
            budget_resource["Budget"]["BudgetLimit"],
            {"Amount": 200, "Unit": "USD"},
        )

    def test_bootstrap_keeps_boundary_and_separate_human_controls(self):
        bootstrap = self.allowlist["bootstrap"]
        self.assertTrue(bootstrap["custom_permissions_boundary_required"])
        self.assertEqual(
            bootstrap["custom_permissions_boundary_policy_name"],
            "LCDashPhase1Boundary",
        )
        self.assertEqual(
            bootstrap["boundary_name_status"],
            "LOCAL_TEMPLATE_ONLY_HUMAN_REVIEW_REQUIRED",
        )
        self.assertTrue(bootstrap["separate_human_write_approval_required"])
        self.assertFalse(bootstrap["cross_account_trust_allowed"])
        self.assertFalse(bootstrap["bootstrap_deletion_allowed"])
        self.assertEqual(
            self.allowlist["gate_status"],
            "AUTHORIZED_TO_BEGIN_PHASE1_PREWRITE",
        )

    def test_first_foundation_step_is_inactive_and_activation_is_separate(self):
        activation = self.allowlist["service_activation"]
        self.assertEqual(activation["parameter"], "PilotServiceDesiredCount")
        self.assertEqual(activation["image_digest_parameter"], "PilotImageDigest")
        self.assertEqual(activation["initial_image_digest_value"], "NOT_PUBLISHED")
        self.assertEqual(
            activation["activation_image_digest_pattern"],
            "^sha256:[a-f0-9]{64}$",
        )
        self.assertEqual(activation["initial_foundation_value"], 0)
        self.assertEqual(activation["later_reviewed_value"], 1)
        self.assertTrue(activation["separate_authorization_required"])
        foundation = self.templates["lcdash-p1-logan-use1-foundation"]
        self.assertEqual(
            foundation["Parameters"]["PilotServiceDesiredCount"]["Default"], 0
        )
        service = next(
            resource["Properties"]
            for resource in foundation["Resources"].values()
            if resource["Type"] == "AWS::ECS::Service"
        )
        self.assertEqual(service["DesiredCount"], {"Ref": "PilotServiceDesiredCount"})
        self.assertIn("PilotImageRequiredForActivation", foundation["Rules"])


if __name__ == "__main__":
    unittest.main()
