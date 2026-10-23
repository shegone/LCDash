import json
from pathlib import Path
import unittest


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
IAM_ROOT = INFRASTRUCTURE_ROOT / "iam"
ACCOUNT = "862772137583"
REGION = "us-east-1"


def statements(document):
    return {statement["Sid"]: statement for statement in document["Statement"]}


def actions(statement):
    value = statement.get("Action", [])
    return {value} if isinstance(value, str) else set(value)


class IamPolicyTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.boundary = json.loads(
            (IAM_ROOT / "LCDashPhase1Boundary.json").read_text(encoding="utf-8")
        )
        cls.deployment = json.loads(
            (IAM_ROOT / "LCDashPhase1DeploymentRolePolicy.json").read_text(
                encoding="utf-8"
            )
        )
        cls.trust = json.loads(
            (IAM_ROOT / "LCDashPhase1DeploymentTrustModel.json").read_text(
                encoding="utf-8"
            )
        )
        cls.allowlist = json.loads(
            (INFRASTRUCTURE_ROOT / "phase1_deployment_allowlist.json").read_text(
                encoding="utf-8"
            )
        )

    def test_documents_use_exact_account_region_and_named_boundary(self):
        serialized = json.dumps(
            [self.boundary, self.deployment, self.trust], sort_keys=True
        )
        self.assertIn(ACCOUNT, serialized)
        self.assertIn(REGION, serialized)
        self.assertEqual(self.trust["account"], ACCOUNT)
        self.assertEqual(self.trust["region"], REGION)
        self.assertEqual(
            self.trust["permissions_boundary_name"],
            "LCDashPhase1Boundary",
        )
        self.assertEqual(
            self.allowlist["bootstrap"]["custom_permissions_boundary_policy_name"],
            "LCDashPhase1Boundary",
        )

    def test_identity_center_trust_model_is_temporary_and_unassigned(self):
        self.assertEqual(
            self.trust["principal_type"],
            "AWS_IAM_IDENTITY_CENTER_ASSIGNED_ROLE",
        )
        self.assertIsNone(self.trust["principal_arn"])
        self.assertFalse(self.trust["cross_account_trust_allowed"])
        self.assertEqual(self.trust["trusted_account_ids"], [ACCOUNT])
        self.assertTrue(self.trust["session"]["temporary_only"])
        self.assertTrue(self.trust["session"]["mfa_required"])
        self.assertEqual(self.trust["session"]["maximum_duration_hours"], 1)
        self.assertFalse(self.trust["session"]["long_lived_access_keys_allowed"])
        self.assertEqual(self.trust["service_principals_allowed_to_assume_operator_role"], [])

    def test_cloudformation_scope_is_exactly_the_four_reviewed_stacks(self):
        # The release-builder stack joined the scope 2026-09-14: the guarded
        # release path deploys it to publish the source asset before every
        # CodeBuild, and until then only the admin permission set could run
        # a release (seen during the identity-outage fix).
        statement = statements(self.deployment)["OperateOnlyApprovedCloudFormationStacks"]
        self.assertEqual(
            set(statement["Resource"]),
            {
                f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/CDKToolkit/*",
                f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/lcdash-p1-logan-use1-certificate/*",
                f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/lcdash-p1-logan-use1-foundation/*",
                f"arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/lcdash-p1-logan-use1-release-builder/*",
            },
        )

    def test_release_builds_are_scoped_to_the_one_project(self):
        statement = statements(self.deployment)["StartAndWatchReleaseBuildsOnly"]
        self.assertEqual(
            statement["Resource"],
            f"arn:aws:codebuild:{REGION}:{ACCOUNT}:project/lcdash-p1-logan-use1-release-builder",
        )
        self.assertNotIn("codebuild:CreateProject", actions(statement))
        self.assertNotIn("codebuild:UpdateProject", actions(statement))

    def test_runtime_verification_grants_are_read_only(self):
        # Rollback is a change set with an older digest, never a direct
        # service update, so the deployment set gets describe/list/read only.
        for sid in (
            "ReadPilotRuntimeStateForReleaseVerification",
            "ReadPilotImagesLogsAndAlertTopicOnly",
        ):
            for action in actions(statements(self.deployment)[sid]):
                verb = action.split(":", 1)[1]
                self.assertTrue(
                    verb.startswith(("Describe", "List", "Get", "Filter")), action
                )
        self.assertNotIn("ecs:UpdateService", json.dumps(self.deployment))

    def test_boundary_lets_the_deployment_set_assume_only_bootstrap_roles(self):
        # The deployment policy always allowed sts:AssumeRole on the CDK
        # bootstrap roles, but the boundary never did, so the effective
        # permission was nothing and every cdk deploy fell back to the
        # caller's own (insufficient) credentials. Both must agree.
        statement = statements(self.boundary)["AllowAssumingBootstrapRolesOnly"]
        self.assertEqual(statement["Resource"], f"arn:aws:iam::{ACCOUNT}:role/cdk-hnb659fds-*")
        self.assertEqual(set(actions(statement)), {"sts:AssumeRole", "sts:TagSession"})
        region_deny = statements(self.boundary)["DenyOutsideUsEast1ForRegionalActions"]
        for action in ("sts:AssumeRole", "sts:TagSession"):
            self.assertIn(action, region_deny["NotAction"])
        allowed = actions(statements(self.boundary)["AllowApprovedRegionalServicesWithinBoundary"])
        self.assertIn("codebuild:*", allowed)
        self.assertNotIn("sns:*", allowed)
        self.assertNotIn("sns:Publish", allowed)

    def test_boundary_uses_only_valid_budget_permission_families(self):
        statement = statements(self.boundary)["AllowPhase1BudgetManagement"]
        self.assertEqual(
            actions(statement),
            {"budgets:ViewBudget", "budgets:ModifyBudget"},
        )
        for invalid in (
            "budgets:DescribeBudget",
            "budgets:CreateBudget",
            "budgets:DeleteBudget",
        ):
            self.assertNotIn(invalid, actions(statement))

    def test_boundary_allows_cloudformation_bootstrap_version_check(self):
        statement = statements(self.boundary)[
            "AllowApprovedRegionalServicesWithinBoundary"
        ]
        self.assertIn("ssm:GetParameters", actions(statement))

    def test_passrole_is_allowlisted_and_arbitrary_passrole_is_denied(self):
        for document in (self.boundary, self.deployment):
            by_sid = statements(document)
            allow = next(
                statement
                for statement in document["Statement"]
                if statement["Effect"] == "Allow" and "iam:PassRole" in actions(statement)
            )
            self.assertNotEqual(allow["Resource"], "*")
            deny = by_sid["DenyArbitraryPassRole"]
            self.assertEqual(deny["Effect"], "Deny")
            self.assertIn("NotResource", deny)
            self.assertNotIn("*", deny["NotResource"])

    def test_boundary_requires_itself_only_where_bootstrap_template_supplies_it(self):
        statement = statements(self.boundary)["DenyRoleCreationWithoutThisBoundary"]
        self.assertEqual(
            set(statement["Resource"]),
            {
                f"arn:aws:iam::{ACCOUNT}:role/lcdash-p1-logan-use1-*",
                f"arn:aws:iam::{ACCOUNT}:role/cdk-hnb659fds-cfn-exec-role-{ACCOUNT}-{REGION}",
            },
        )
        serialized = json.dumps(statement)
        for helper in (
            "file-publishing-role",
            "image-publishing-role",
            "lookup-role",
            "deploy-role",
        ):
            self.assertNotIn(helper, serialized)

    def test_hard_denies_cover_dns_users_keys_account_and_operational_actions(self):
        required = {
            "route53:*",
            "route53domains:*",
            "iam:CreateUser",
            "iam:CreateAccessKey",
            "organizations:*",
            "account:*",
            "billing:*",
            "ssm:StartSession",
            "ssm:SendCommand",
            "sns:Publish",
            "sqs:SendMessage",
        }
        for document in (self.boundary, self.deployment):
            denied = set()
            for statement in document["Statement"]:
                if statement["Effect"] == "Deny":
                    denied.update(actions(statement))
            self.assertTrue(required.issubset(denied), required - denied)

    def test_every_policy_has_region_deny_and_no_allow_action_star(self):
        for document in (self.boundary, self.deployment):
            region_denies = [
                statement
                for statement in document["Statement"]
                if statement["Effect"] == "Deny"
                and statement.get("Condition", {}).get("StringNotEquals", {}).get(
                    "aws:RequestedRegion"
                )
                == REGION
            ]
            self.assertEqual(len(region_denies), 1)
            for statement in document["Statement"]:
                if statement["Effect"] == "Allow":
                    self.assertNotIn("*", actions(statement))

    def test_role_and_asset_patterns_use_only_phase1_or_default_qualifier(self):
        serialized = json.dumps([self.boundary, self.deployment])
        for marker in (
            "role/lcdash-p1-logan-use1-*",
            "role/cdk-hnb659fds-*",
            "cdk-hnb659fds-assets-862772137583-us-east-1",
            "cdk-hnb659fds-container-assets-862772137583-us-east-1",
            "parameter/cdk-bootstrap/hnb659fds/version",
        ):
            self.assertIn(marker, serialized)

    def test_boundary_denies_unrelated_secret_reads_and_buckets(self):
        by_sid = statements(self.boundary)
        secret_deny = by_sid["DenyReadingUnrelatedSecrets"]
        self.assertEqual(secret_deny["Effect"], "Deny")
        self.assertIn("secretsmanager:GetSecretValue", actions(secret_deny))
        self.assertEqual(
            secret_deny["Condition"]["StringNotEquals"]["aws:ResourceTag/Project"],
            "LCDash-AWS",
        )
        batch_deny = by_sid["DenyBatchSecretReads"]
        self.assertEqual(batch_deny["Effect"], "Deny")
        self.assertEqual(actions(batch_deny), {"secretsmanager:BatchGetSecretValue"})
        self.assertEqual(batch_deny["Resource"], "*")
        self.assertNotIn("Condition", batch_deny)
        bucket_deny = by_sid["DenyAccessToUnrelatedBuckets"]
        self.assertEqual(bucket_deny["Effect"], "Deny")
        for resource in bucket_deny["NotResource"]:
            self.assertTrue(
                "cdk-hnb659fds-assets-862772137583-us-east-1" in resource
                or "lcdash-p1-logan-use1-862772137583-" in resource
            )


if __name__ == "__main__":
    unittest.main()
