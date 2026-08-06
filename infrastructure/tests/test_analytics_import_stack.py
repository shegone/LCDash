import json
from pathlib import Path
import sys
import unittest


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INFRASTRUCTURE_ROOT))

try:
    import aws_cdk as cdk
    from aws_cdk.assertions import Template
    from lcdash_pilot.analytics_import_stack import Phase2AnalyticsImportStack
except ImportError:
    cdk = None


@unittest.skipUnless(cdk is not None, "aws-cdk-lib is not installed")
class AnalyticsImportStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        stack = Phase2AnalyticsImportStack(
            app,
            "TestAnalyticsImport",
            env=cdk.Environment(account="111111111111", region="us-east-1"),
        )
        cls.template = Template.from_stack(stack).to_json()
        cls.resources = cls.template["Resources"]

    def test_staging_is_private_kms_encrypted_and_short_lived(self):
        bucket = next(v for v in self.resources.values() if v["Type"] == "AWS::S3::Bucket")
        props = bucket["Properties"]
        self.assertEqual(props["PublicAccessBlockConfiguration"]["BlockPublicAcls"], True)
        encryption = props["BucketEncryption"]["ServerSideEncryptionConfiguration"][0]
        self.assertEqual(encryption["ServerSideEncryptionByDefault"]["SSEAlgorithm"], "aws:kms")
        rules = {rule["Id"]: rule for rule in props["LifecycleConfiguration"]["Rules"]}
        self.assertEqual(rules["ExpireHistoricalAnalyticsStaging"]["ExpirationInDays"], 3)
        self.assertEqual(
            rules["ExpireHistoricalAnalyticsStaging"]["Prefix"],
            Phase2AnalyticsImportStack.STAGING_PREFIX,
        )
        self.assertEqual(rules["AbortIncompleteMultipartUploads"]["AbortIncompleteMultipartUpload"], {"DaysAfterInitiation": 1})
        self.assertIn("aws:SecureTransport", json.dumps(self.template))

    def test_stack_defines_task_but_never_runs_a_service(self):
        types = [resource["Type"] for resource in self.resources.values()]
        self.assertEqual(types.count("AWS::ECS::TaskDefinition"), 1)
        self.assertNotIn("AWS::ECS::Service", types)
        self.assertNotIn("AWS::StepFunctions::StateMachine", types)
        task = next(v for v in self.resources.values() if v["Type"] == "AWS::ECS::TaskDefinition")
        container = task["Properties"]["ContainerDefinitions"][0]
        self.assertEqual(
            container["Command"],
            ["python", "-m", "app.tools.phase2_analytics_import_runtime"],
        )
        self.assertTrue(container["ReadonlyRootFilesystem"])
        self.assertEqual(container["User"], "10001:10001")

    def test_task_role_is_exact_object_limited_and_has_no_cad_or_secret_access(self):
        task = next(v for v in self.resources.values() if v["Type"] == "AWS::ECS::TaskDefinition")
        task_role_ref = task["Properties"]["TaskRoleArn"]["Fn::GetAtt"][0]
        task_policy = next(
            value for value in self.resources.values()
            if value["Type"] == "AWS::IAM::Policy"
            and {role["Ref"] for role in value["Properties"]["Roles"]} == {task_role_ref}
        )
        serialized = json.dumps(task_policy)
        self.assertIn("StagedObjectKey", serialized)
        self.assertTrue(
            self.template["Parameters"]["StagedObjectKey"]["AllowedPattern"].startswith(
                "^tenants/logan-synthetic/historical-analytics/"
            )
        )
        self.assertIn("s3:GetObject", serialized)
        self.assertIn("kms:Decrypt", serialized)
        for prohibited in (
            "secretsmanager:GetSecretValue", "execute-api:", "sqs:", "sns:",
            "kinesis:", "dynamodb:", "lambda:", "events:", "PutObject",
            "DeleteObject", "s3:ListBucket",
        ):
            self.assertNotIn(prohibited, serialized)

    def test_execution_role_is_target_only_and_ecr_is_repository_scoped(self):
        task = next(v for v in self.resources.values() if v["Type"] == "AWS::ECS::TaskDefinition")
        execution_ref = task["Properties"]["ExecutionRoleArn"]["Fn::GetAtt"][0]
        policies = [
            value for value in self.resources.values()
            if value["Type"] == "AWS::IAM::Policy"
            and execution_ref in {role["Ref"] for role in value["Properties"]["Roles"]}
        ]
        serialized = json.dumps(policies)
        self.assertIn("TargetDatabaseSecretArn", serialized)
        self.assertNotIn("CloudCad", serialized)
        self.assertNotIn("SourceDatabase", serialized)
        self.assertIn("ImporterRepositoryArn", serialized)

    def test_container_has_exact_import_inputs_and_no_source_or_cad_secret(self):
        task = next(v for v in self.resources.values() if v["Type"] == "AWS::ECS::TaskDefinition")
        container = task["Properties"]["ContainerDefinitions"][0]
        environment = {item["Name"]: item["Value"] for item in container["Environment"]}
        secrets = {item["Name"] for item in container["Secrets"]}
        self.assertEqual(environment["LCDASH_CLOUD_CAD_ENABLED"], "false")
        self.assertEqual(environment["LCDASH_IMPORT_STAGING_PREFIX"], Phase2AnalyticsImportStack.STAGING_PREFIX)
        self.assertEqual(environment["LCDASH_IMPORT_OBJECT_KEY"], {"Ref": "StagedObjectKey"})
        self.assertEqual(
            environment["LCDASH_IMPORT_PLAINTEXT_SHA256"],
            {"Ref": "ExpectedPlaintextSha256"},
        )
        self.assertEqual(environment["LCDASH_TARGET_DATABASE_NAME"], "lcdash")
        self.assertEqual(
            secrets,
            {
                "LCDASH_TARGET_DATABASE_USERNAME", "LCDASH_TARGET_DATABASE_PASSWORD",
                "LCDASH_TARGET_DATABASE_HOST", "LCDASH_TARGET_DATABASE_PORT",
            },
        )
        self.assertFalse(any("SOURCE" in name or "CAD" in name for name in secrets))

    def test_importer_security_group_is_dns_https_and_target_rds_only(self):
        group = next(
            value for value in self.resources.values()
            if value["Type"] == "AWS::EC2::SecurityGroup"
        )
        egress = group["Properties"]["SecurityGroupEgress"]
        observed = {
            (
                rule["IpProtocol"], rule["FromPort"], rule["ToPort"],
                rule.get("CidrIp"), json.dumps(rule.get("DestinationSecurityGroupId"), sort_keys=True),
            )
            for rule in egress
        }
        self.assertEqual(
            observed,
            {
                ("udp", 53, 53, "10.42.0.2/32", "null"),
                ("tcp", 53, 53, "10.42.0.2/32", "null"),
                ("tcp", 443, 443, "0.0.0.0/0", "null"),
                ("tcp", 5432, 5432, None, '{"Ref": "TargetDatabaseSecurityGroupId"}'),
            },
        )
        ingress = next(
            value for value in self.resources.values()
            if value["Type"] == "AWS::EC2::SecurityGroupIngress"
        )
        self.assertEqual(ingress["Properties"]["FromPort"], 5432)
        self.assertEqual(ingress["Properties"]["ToPort"], 5432)
        self.assertEqual(
            ingress["Properties"]["GroupId"],
            {"Ref": "TargetDatabaseSecurityGroupId"},
        )


if __name__ == "__main__":
    unittest.main()
