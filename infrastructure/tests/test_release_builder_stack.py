import sys
from pathlib import Path
import unittest


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INFRASTRUCTURE_ROOT))

try:
    import aws_cdk as cdk
    from aws_cdk.assertions import Template
    from lcdash_pilot.release_builder_stack import Phase1ReleaseBuilderStack
except ImportError:
    cdk = None


@unittest.skipUnless(cdk is not None, "aws-cdk-lib is not installed")
class ReleaseBuilderStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        stack = Phase1ReleaseBuilderStack(
            app,
            "TestReleaseBuilder",
            env=cdk.Environment(account="862772137583", region="us-east-1"),
        )
        cls.template = Template.from_stack(stack).to_json()
        cls.projects = [
            resource
            for resource in cls.template["Resources"].values()
            if resource["Type"] == "AWS::CodeBuild::Project"
        ]

    def test_exact_resource_shape_has_one_project_role_and_policy(self):
        counts = {}
        for resource in self.template["Resources"].values():
            counts[resource["Type"]] = counts.get(resource["Type"], 0) + 1
        self.assertEqual(counts.get("AWS::CodeBuild::Project"), 1)
        self.assertEqual(counts.get("AWS::IAM::Role"), 1)
        self.assertEqual(counts.get("AWS::IAM::Policy"), 1)
        for forbidden_type in (
            "AWS::Logs::LogGroup",
            "AWS::S3::Bucket",
            "AWS::EC2::SecurityGroup",
            "AWS::EC2::VPC",
            "AWS::ECS::Service",
        ):
            self.assertNotIn(forbidden_type, counts)

    def test_project_is_privileged_s3_asset_source_without_cache_or_artifacts(self):
        project = self.projects[0]["Properties"]
        self.assertTrue(project["Environment"]["PrivilegedMode"])
        self.assertEqual(project["Source"]["Type"], "S3")
        self.assertEqual(
            project["Source"]["BuildSpec"],
            "infrastructure/buildspecs/release-builder.yml",
        )
        self.assertEqual(project["Artifacts"]["Type"], "NO_ARTIFACTS")
        self.assertEqual(project["Cache"]["Type"], "NO_CACHE")
        self.assertNotIn("VpcConfig", project)

    def test_role_has_only_asset_ecr_and_own_log_actions(self):
        policies = [
            resource
            for resource in self.template["Resources"].values()
            if resource["Type"] == "AWS::IAM::Policy"
        ]
        serialized = str(policies)
        for required in (
            "s3:GetObject",
            "ecr:GetAuthorizationToken",
            "ecr:PutImage",
            "ecr:DescribeImages",
            "logs:PutLogEvents",
            "lcdash-p1-logan-use1-web",
        ):
            self.assertIn(required, serialized)
        for forbidden in (
            "ecs:",
            "rds:",
            "secretsmanager:",
            "ssm:",
            "cognito-idp:",
            "bedrock:",
            "dynamodb:",
            "s3:PutObject",
            "iam:PassRole",
            "codebuild:CreateReport",
            "s3:List",
            "s3:GetBucket",
        ):
            self.assertNotIn(forbidden, serialized.lower())
        self.assertNotIn("repository/*", serialized)

    def test_environment_contains_only_nonsecret_release_metadata(self):
        variables = {
            item["Name"]: item["Value"]
            for item in self.projects[0]["Properties"]["Environment"][
                "EnvironmentVariables"
            ]
        }
        self.assertEqual(
            set(variables),
            {
                "AWS_ACCOUNT_ID",
                "DOCKERFILE_PATH",
                "ECR_REPOSITORY_NAME",
                "REPOSITORY_URI",
                "SOURCE_ASSET_HASH",
            },
        )
        self.assertEqual(variables["AWS_ACCOUNT_ID"], "862772137583")
        serialized = str(self.template).lower()
        self.assertNotIn("centralsquare", serialized)
        self.assertNotIn("database_password", serialized)
        self.assertNotIn("secret_value", serialized)

    def test_buildspec_reports_digest_and_never_deploys(self):
        buildspec = (
            INFRASTRUCTURE_ROOT / "buildspecs" / "release-builder.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("Dockerfile.aws-pilot", buildspec)
        self.assertIn("docker push", buildspec)
        self.assertIn("image-digest-report.json", buildspec)
        self.assertIn("IMMUTABLE_IMAGE_REFERENCE", buildspec)
        self.assertIn("set +x", buildspec)
        for forbidden in (
            "ecs update-service",
            "cdk deploy",
            "cloudformation",
            "secretsmanager",
            "CENTRALSQUARE_",
            "LCDASH_DATABASE_",
        ):
            self.assertNotIn(forbidden.lower(), buildspec.lower())


if __name__ == "__main__":
    unittest.main()
