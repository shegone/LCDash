import sys
from pathlib import Path
import unittest


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INFRASTRUCTURE_ROOT))

try:
    import aws_cdk as cdk
    from aws_cdk.assertions import Match, Template
    from lcdash_pilot.image_build_stack import Phase1ImageBuildStack
except ImportError:
    cdk = None


@unittest.skipUnless(cdk is not None, "aws-cdk-lib is not installed")
class ImageBuildStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        stack = Phase1ImageBuildStack(
            app,
            "TestImageBuild",
            env=cdk.Environment(account="111111111111", region="us-east-1"),
        )
        cls.template = Template.from_stack(stack)

    def test_build_is_privileged_and_source_is_single_s3_object(self):
        self.template.has_resource_properties(
            "AWS::CodeBuild::Project",
            {
                "Environment": Match.object_like({"PrivilegedMode": True}),
                "Source": Match.object_like({"Type": "S3"}),
            },
        )
        project = next(
            resource
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::CodeBuild::Project"
        )
        self.assertIn(
            "source/lcdash-pilot.zip",
            str(project["Properties"]["Source"]["Location"]),
        )

    def test_role_can_push_only_to_pilot_repository(self):
        template = self.template.to_json()
        policies = [
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::IAM::Policy"
        ]
        serialized = str(policies)
        self.assertIn("lcdash-p1-logan-use1-web", serialized)
        self.assertNotIn("repository/*", serialized)
        self.assertNotIn("ecs:UpdateService", serialized)
        self.assertNotIn("cognito-idp:", serialized)

    def test_buildspec_runs_health_check_and_waits_for_scan(self):
        project = next(
            resource
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::CodeBuild::Project"
        )
        buildspec = project["Properties"]["Source"]["BuildSpec"]
        self.assertIn("/health", buildspec)
        self.assertIn("describe-image-scan-findings", buildspec)
        self.assertIn("test -f /tmp/lcdash-image-health-tested-and-pushed", buildspec)
        self.template.resource_count_is("AWS::ECS::Service", 0)

    def test_alpine_experiment_is_a_separate_fixed_project_path(self):
        projects = [
            resource
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::CodeBuild::Project"
        ]
        self.assertEqual(len(projects), 2)
        experimental = next(
            project
            for project in projects
            if project["Properties"]["Name"].endswith("alpine-experimental")
        )
        environment = {
            item["Name"]: item["Value"]
            for item in experimental["Properties"]["Environment"][
                "EnvironmentVariables"
            ]
        }
        self.assertEqual(
            environment["DOCKERFILE_PATH"],
            "Dockerfile.aws-pilot-alpine-experimental",
        )
        self.assertEqual(environment["IMAGE_TAG_PREFIX"], "alpine-source")
        self.assertNotIn("ecs:UpdateService", str(experimental))

    def test_health_container_uses_only_disconnected_non_secret_database_values(self):
        project = next(
            resource
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::CodeBuild::Project"
        )
        buildspec = project["Properties"]["Source"]["BuildSpec"]
        self.assertIn("LCDASH_DATABASE_HOST=healthcheck.invalid", buildspec)
        self.assertIn("LCDASH_DATABASE_NAME=synthetic_healthcheck", buildspec)
        self.assertIn("LCDASH_DATABASE_USERNAME=synthetic_healthcheck", buildspec)
        self.assertIn(
            "LCDASH_DATABASE_PASSWORD=synthetic-placeholder-not-a-secret",
            buildspec,
        )
        self.assertNotIn("CENTRALSQUARE_", buildspec)
        self.assertNotIn("secretsmanager", buildspec.lower())
        self.assertNotIn("LCDASH_DATABASE_HOST=127.", buildspec)
        self.assertNotIn("LCDASH_DATABASE_HOST=10.", buildspec)
        self.assertNotIn("LCDASH_DATABASE_HOST=14.", buildspec)


if __name__ == "__main__":
    unittest.main()
