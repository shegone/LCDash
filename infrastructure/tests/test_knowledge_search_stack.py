import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import aws_cdk as cdk
from aws_cdk.assertions import Template
from lcdash_pilot.knowledge_search_stack import Phase1KnowledgeSearchStack


class KnowledgeSearchStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        stack = Phase1KnowledgeSearchStack(
            app, "TestKnowledgeSearch",
            env=cdk.Environment(account="862772137583", region="us-east-1"),
        )
        cls.template = Template.from_stack(stack).to_json()
        cls.serialized = json.dumps(cls.template)

    def test_exact_private_vector_and_kb_shape(self):
        types = [r["Type"] for r in self.template["Resources"].values()]
        self.assertEqual(types.count("AWS::S3Vectors::VectorBucket"), 1)
        self.assertEqual(types.count("AWS::S3Vectors::Index"), 1)
        self.assertEqual(types.count("AWS::Bedrock::KnowledgeBase"), 1)
        self.assertEqual(types.count("AWS::Bedrock::DataSource"), 2)
        self.assertIn('"Dimension": 1024', self.serialized)
        self.assertIn('"DistanceMetric": "cosine"', self.serialized)
        self.assertIn('"SseType": "aws:kms"', self.serialized)
        self.assertIn('"AMAZON_BEDROCK_TEXT"', self.serialized)
        self.assertIn('"AMAZON_BEDROCK_METADATA"', self.serialized)

    def test_data_sources_are_exact_semantic_advanced_prefixes(self):
        sources = [r["Properties"] for r in self.template["Resources"].values()
                   if r["Type"] == "AWS::Bedrock::DataSource"]
        prefixes = {p["DataSourceConfiguration"]["S3Configuration"]["InclusionPrefixes"][0]
                    for p in sources}
        self.assertEqual(prefixes, set(Phase1KnowledgeSearchStack.SOURCE_PREFIXES))
        for source in sources:
            ingestion = source["VectorIngestionConfiguration"]
            self.assertEqual(ingestion["ChunkingConfiguration"]["ChunkingStrategy"], "SEMANTIC")
            self.assertEqual(ingestion["ParsingConfiguration"]["ParsingStrategy"], "BEDROCK_FOUNDATION_MODEL")
            self.assertEqual(source["DataDeletionPolicy"], "RETAIN")

    def test_service_role_is_source_and_index_scoped(self):
        policies = [r for r in self.template["Resources"].values() if r["Type"] == "AWS::IAM::Policy"]
        text = json.dumps(policies)
        self.assertIn("amazon.titan-embed-text-v2:0", text)
        self.assertIn("s3vectors:QueryVectors", text)
        self.assertNotIn('"Action": "bedrock:*"', text)
        self.assertNotIn("s3:PutObject", text)
        self.assertNotIn("s3:DeleteObject", text)
        for prefix in Phase1KnowledgeSearchStack.SOURCE_PREFIXES:
            self.assertIn(prefix, text)

    def test_kms_policy_allows_only_scoped_s3_vectors_indexing_decrypt(self):
        keys = [r for r in self.template["Resources"].values() if r["Type"] == "AWS::KMS::Key"]
        policy = json.dumps(keys[0]["Properties"]["KeyPolicy"])
        self.assertIn("indexing.s3vectors.amazonaws.com", policy)
        self.assertIn('"Action": "kms:Decrypt"', policy)
        self.assertIn('"aws:SourceAccount": "862772137583"', policy)
        self.assertIn("bucket/lcdash-p1-logan-use1-knowledge-vectors*", policy)

    def test_no_live_service_or_ingestion_custom_resource(self):
        prohibited = {"AWS::ECS::Service", "AWS::Lambda::Function", "Custom::AWS"}
        self.assertFalse(prohibited & {r["Type"] for r in self.template["Resources"].values()})
        self.assertNotIn("StartIngestionJob", self.serialized)

    def test_kb_waits_for_role_policy_before_creation(self):
        resources = self.template["Resources"]
        kb = next(r for r in resources.values() if r["Type"] == "AWS::Bedrock::KnowledgeBase")
        policy_id = next(k for k, r in resources.items() if r["Type"] == "AWS::IAM::Policy")
        self.assertIn(policy_id, kb["DependsOn"])

    def test_trust_can_be_tightened_to_exact_created_kb_id(self):
        parameter = self.template["Parameters"]["KnowledgeBaseTrustResourceId"]
        self.assertEqual(parameter["Default"], "*")
        self.assertEqual(parameter["AllowedPattern"], r"^(\*|[A-Z0-9]{10})$")
        role = next(
            r for r in self.template["Resources"].values() if r["Type"] == "AWS::IAM::Role"
        )
        trust = json.dumps(role["Properties"]["AssumeRolePolicyDocument"])
        self.assertIn("KnowledgeBaseTrustResourceId", trust)


if __name__ == "__main__":
    unittest.main()
