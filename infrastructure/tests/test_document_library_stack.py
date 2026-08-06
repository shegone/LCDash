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
    from lcdash_pilot.document_library_stack import Phase1DocumentLibraryStack
except ImportError:
    cdk = None


@unittest.skipUnless(cdk is not None, "aws-cdk-lib is not installed")
class DocumentLibraryStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        stack = Phase1DocumentLibraryStack(
            app,
            "TestDocumentLibrary",
            env=cdk.Environment(account="111111111111", region="us-east-1"),
        )
        cls.template = Template.from_stack(stack).to_json()
        cls.bucket = next(
            value
            for value in cls.template["Resources"].values()
            if value["Type"] == "AWS::S3::Bucket"
        )
        cls.policies = [
            value
            for value in cls.template["Resources"].values()
            if value["Type"] == "AWS::IAM::Policy"
        ]

    def test_bucket_is_private_encrypted_tls_only_and_disposable(self):
        properties = self.bucket["Properties"]
        self.assertEqual(
            properties["PublicAccessBlockConfiguration"],
            {
                "BlockPublicAcls": True,
                "BlockPublicPolicy": True,
                "IgnorePublicAcls": True,
                "RestrictPublicBuckets": True,
            },
        )
        self.assertEqual(
            properties["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
                "ServerSideEncryptionByDefault"
            ]["SSEAlgorithm"],
            "AES256",
        )
        self.assertNotIn("VersioningConfiguration", properties)
        self.assertEqual(self.bucket["DeletionPolicy"], "Delete")
        self.assertEqual(self.bucket["UpdateReplacePolicy"], "Delete")
        serialized = json.dumps(self.template)
        self.assertIn("aws:SecureTransport", serialized)
        self.assertIn("s3:*", serialized)

    def test_exact_resource_inventory_has_no_auto_delete_provider(self):
        resources = self.template["Resources"]
        self.assertEqual(
            sorted(resource["Type"] for resource in resources.values()),
            [
                "AWS::IAM::Policy",
                "AWS::IAM::Role",
                "AWS::S3::Bucket",
                "AWS::S3::BucketPolicy",
            ],
        )
        serialized = json.dumps(resources)
        self.assertNotIn("Custom::S3AutoDeleteObjects", serialized)
        self.assertNotIn("AWS::Lambda::Function", serialized)
        self.assertNotIn("DeleteObject", serialized)
        self.assertNotIn("PutBucketPolicy", serialized)

    def test_lifecycle_controls_only_staging_and_incomplete_uploads(self):
        rules = self.bucket["Properties"]["LifecycleConfiguration"]["Rules"]
        by_id = {rule["Id"]: rule for rule in rules}
        self.assertEqual(
            by_id["AbortIncompleteMultipartUploads"]["AbortIncompleteMultipartUpload"],
            {"DaysAfterInitiation": 1},
        )
        self.assertEqual(by_id["ExpireUnapprovedStagingObjects"]["ExpirationInDays"], 7)
        self.assertEqual(
            by_id["ExpireUnapprovedStagingObjects"]["Prefix"],
            "tenants/logan-synthetic/document-library/staging/",
        )
        self.assertFalse(any("Transition" in rule for rule in rules))

    def test_application_role_is_read_only_for_exact_approved_prefixes(self):
        self.assertEqual(len(self.policies), 1)
        statements = self.policies[0]["Properties"]["PolicyDocument"]["Statement"]
        actions = {
            action
            for statement in statements
            for action in (
                [statement["Action"]]
                if isinstance(statement["Action"], str)
                else statement["Action"]
            )
        }
        self.assertEqual(actions, {"s3:ListBucket", "s3:GetObject"})
        serialized = json.dumps(statements)
        for prohibited in (
            "s3:PutObject",
            "s3:DeleteObject",
            "s3:GetObjectVersion",
            "document-library/staging",
            "vendor-archives",
            "gis",
        ):
            self.assertNotIn(prohibited, serialized)
        for prefix in Phase1DocumentLibraryStack.READ_PREFIXES:
            self.assertIn(prefix, serialized)

    def test_role_is_dormant_and_ecs_only(self):
        roles = [
            value
            for value in self.template["Resources"].values()
            if value["Type"] == "AWS::IAM::Role"
        ]
        self.assertEqual(len(roles), 1)
        role = next(
            value
            for logical_id, value in self.template["Resources"].items()
            if value["Type"] == "AWS::IAM::Role"
            and "DocumentLibraryReadRole" in logical_id
        )
        principal = role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0][
            "Principal"
        ]
        self.assertEqual(principal, {"Service": "ecs-tasks.amazonaws.com"})
        self.assertNotIn("AWS::ECS::TaskDefinition", {
            value["Type"] for value in self.template["Resources"].values()
        })

    def test_upload_plan_matches_stack_prefixes_and_manifest_exclusions(self):
        plan = json.loads(
            (REPOSITORY_ROOT / "docs/planning/DOCUMENT_LIBRARY_UPLOAD_PLAN_2026-08-05.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (REPOSITORY_ROOT / "docs/planning/ONPREM_DOCUMENT_LIBRARY_MANIFEST_2026-08-05.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plan["status"], "PLAN_ONLY_NOT_AUTHORIZED")
        self.assertEqual(
            set(plan["approved_destination_prefixes"]),
            {f"{prefix}/" for prefix in Phase1DocumentLibraryStack.READ_PREFIXES},
        )
        self.assertEqual(
            plan["staging_prefix"], f"{Phase1DocumentLibraryStack.STAGING_PREFIX}/"
        )
        self.assertFalse(plan["versioning"]["enabled"])
        self.assertEqual(plan["upload_execution"], "NOT_IMPLEMENTED_AND_NOT_AUTHORIZED")
        self.assertEqual(plan["explicit_exclusions"], manifest["explicit_exclusions"])
        dispositions = plan["source_dispositions"]
        self.assertIn("EXCLUDE", dispositions["gis_reference"])
        self.assertIn("DO_NOT_COPY", dispositions["mindshare_public_site"])
        self.assertIn("EXCLUDE_BY_DEFAULT", dispositions["mindshare_vendor_archives"])


if __name__ == "__main__":
    unittest.main()
