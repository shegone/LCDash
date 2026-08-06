"""Plan-only Bedrock knowledge-search infrastructure for approved documents."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from constructs import Construct

from .config import APPROVED_REGION, NAME_PREFIX


class Phase1KnowledgeSearchStack(cdk.Stack):
    """Dedicated S3 Vectors/Bedrock resources; never referenced by live foundation."""

    ACCOUNT = "862772137583"
    SOURCE_BUCKET = f"{NAME_PREFIX}-{ACCOUNT}-document-library"
    SOURCE_PREFIXES = (
        "tenants/logan-synthetic/document-library/mindshare/current/onprem-approved-164-2026-08-05/",
        "tenants/logan-synthetic/document-library/centralsquare/current/onprem-approved-164-2026-08-05/",
    )
    EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
    EMBEDDING_DIMENSIONS = 1024

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if self.region != APPROVED_REGION or self.account != self.ACCOUNT:
            raise ValueError("Knowledge search may synthesize only for the approved account/us-east-1.")

        parser_model_arn = cdk.CfnParameter(
            self,
            "AdvancedParserModelArn",
            type="String",
            allowed_pattern=r"^arn:aws:bedrock:us-east-1::foundation-model/[A-Za-z0-9._:-]+$",
            description=(
                "Current Bedrock-supported advanced PDF parser model ARN; "
                "must be rechecked with pricing at the provisioning gate."
            ),
        )
        trust_resource_id = cdk.CfnParameter(
            self,
            "KnowledgeBaseTrustResourceId",
            type="String",
            default="*",
            allowed_pattern=r"^(\*|[A-Z0-9]{10})$",
            description=(
                "Use wildcard only during initial creation; update to the exact created "
                "knowledge-base ID before ingestion."
            ),
        )
        key = kms.Key(
            self,
            "KnowledgeSearchKey",
            alias=f"alias/{NAME_PREFIX}-knowledge-search",
            description="Private pilot vector, ingestion-transient, and retrieval-session encryption",
            enable_key_rotation=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            pending_window=cdk.Duration.days(30),
        )
        key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowS3VectorsAsynchronousIndexing",
                principals=[iam.ServicePrincipal("indexing.s3vectors.amazonaws.com")],
                actions=["kms:Decrypt"],
                resources=["*"],
                conditions={
                    "StringEquals": {"aws:SourceAccount": self.ACCOUNT},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:aws:s3vectors:{APPROVED_REGION}:{self.ACCOUNT}:"
                            f"bucket/{NAME_PREFIX}-knowledge-vectors*"
                        )
                    },
                },
            )
        )
        vector_bucket = cdk.CfnResource(
            self,
            "KnowledgeVectorBucket",
            type="AWS::S3Vectors::VectorBucket",
            properties={
                "VectorBucketName": f"{NAME_PREFIX}-knowledge-vectors",
                "EncryptionConfiguration": {"SseType": "aws:kms", "KmsKeyArn": key.key_arn},
                "Tags": self._tags("vector-bucket"),
            },
        )
        vector_index = cdk.CfnResource(
            self,
            "KnowledgeVectorIndex",
            type="AWS::S3Vectors::Index",
            properties={
                "VectorBucketArn": vector_bucket.get_att("VectorBucketArn"),
                "IndexName": f"{NAME_PREFIX}-logan-synthetic",
                "DataType": "float32",
                "Dimension": self.EMBEDDING_DIMENSIONS,
                "DistanceMetric": "cosine",
                "EncryptionConfiguration": {"SseType": "aws:kms", "KmsKeyArn": key.key_arn},
                "MetadataConfiguration": {
                    "NonFilterableMetadataKeys": [
                        "AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"
                    ]
                },
                "Tags": self._tags("vector-index"),
            },
        )
        vector_index.add_dependency(vector_bucket)

        role = iam.Role(
            self,
            "KnowledgeBaseServiceRole",
            role_name="AmazonBedrockExecutionRoleForKnowledgeBase_lcdash_p1_logan_use1",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com").with_conditions(
                {
                    "StringEquals": {"aws:SourceAccount": self.ACCOUNT},
                    "ArnLike": {
                        "aws:SourceArn": cdk.Fn.join(
                            "",
                            [
                                f"arn:aws:bedrock:{APPROVED_REGION}:{self.ACCOUNT}:knowledge-base/",
                                trust_resource_id.value_as_string,
                            ],
                        )
                    },
                }
            ),
            description="Least-privilege service role for the Logan synthetic approved-document KB",
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[f"arn:aws:s3:::{self.SOURCE_BUCKET}"],
                conditions={"StringLike": {"s3:prefix": [f"{p}*" for p in self.SOURCE_PREFIXES]}},
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[f"arn:aws:s3:::{self.SOURCE_BUCKET}/{p}*" for p in self.SOURCE_PREFIXES],
            )
        )
        embedding_arn = f"arn:aws:bedrock:{APPROVED_REGION}::foundation-model/{self.EMBEDDING_MODEL_ID}"
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[embedding_arn, parser_model_arn.value_as_string],
                conditions={"StringEquals": {"aws:RequestedRegion": APPROVED_REGION}},
            )
        )
        index_arn = cdk.Token.as_string(vector_index.get_att("IndexArn"))
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3vectors:PutVectors", "s3vectors:GetVectors", "s3vectors:DeleteVectors",
                    "s3vectors:QueryVectors", "s3vectors:GetIndex",
                ],
                resources=[index_arn],
            )
        )
        key.grant_encrypt_decrypt(role)

        cdk.CfnResource(
            self,
            "KnowledgeVectorBucketPolicy",
            type="AWS::S3Vectors::VectorBucketPolicy",
            properties={
                "VectorBucketArn": vector_bucket.get_att("VectorBucketArn"),
                "Policy": {
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Sid": "OnlyKnowledgeBaseRole",
                        "Effect": "Allow",
                        "Principal": {"AWS": role.role_arn},
                        "Action": [
                            "s3vectors:GetIndex", "s3vectors:GetVectors", "s3vectors:PutVectors",
                            "s3vectors:DeleteVectors", "s3vectors:QueryVectors",
                        ],
                        "Resource": index_arn,
                    }],
                },
            },
        ).add_dependency(vector_bucket)

        kb = cdk.CfnResource(
            self,
            "ApprovedDocumentsKnowledgeBase",
            type="AWS::Bedrock::KnowledgeBase",
            properties={
                "Name": "lcdash_p1_logan_use1_approved_documents",
                "Description": "Advisory-only private retrieval for 164 approved Logan synthetic documents",
                "RoleArn": role.role_arn,
                "KnowledgeBaseConfiguration": {
                    "Type": "VECTOR",
                    "VectorKnowledgeBaseConfiguration": {
                        "EmbeddingModelArn": embedding_arn,
                        "EmbeddingModelConfiguration": {
                            "BedrockEmbeddingModelConfiguration": {
                                "Dimensions": self.EMBEDDING_DIMENSIONS,
                                "EmbeddingDataType": "FLOAT32",
                            }
                        },
                    },
                },
                "StorageConfiguration": {
                    "Type": "S3_VECTORS",
                    "S3VectorsConfiguration": {"IndexArn": index_arn},
                },
                "Tags": {tag["Key"]: tag["Value"] for tag in self._tags("knowledge-base")},
            },
        )
        kb.add_dependency(vector_index)
        kb.add_dependency(role.node.default_child)
        kb.add_dependency(role.node.find_child("DefaultPolicy").node.default_child)

        for name, prefix in (("Mindshare", self.SOURCE_PREFIXES[0]), ("CentralSquare", self.SOURCE_PREFIXES[1])):
            data_source = cdk.CfnResource(
                self,
                f"{name}ApprovedDataSource",
                type="AWS::Bedrock::DataSource",
                properties={
                    "KnowledgeBaseId": kb.get_att("KnowledgeBaseId"),
                    "Name": f"lcdash_{name.lower()}_approved_20260805",
                    "Description": f"Exact approved {name} manifest-scoped source prefix",
                    "DataDeletionPolicy": "RETAIN",
                    "DataSourceConfiguration": {
                        "Type": "S3",
                        "S3Configuration": {
                            "BucketArn": f"arn:aws:s3:::{self.SOURCE_BUCKET}",
                            "BucketOwnerAccountId": self.ACCOUNT,
                            "InclusionPrefixes": [prefix],
                        },
                    },
                    "ServerSideEncryptionConfiguration": {"KmsKeyArn": key.key_arn},
                    "VectorIngestionConfiguration": {
                        "ChunkingConfiguration": {
                            "ChunkingStrategy": "SEMANTIC",
                            "SemanticChunkingConfiguration": {
                                "MaxTokens": 300,
                                "BufferSize": 1,
                                "BreakpointPercentileThreshold": 95,
                            },
                        },
                        "ParsingConfiguration": {
                            "ParsingStrategy": "BEDROCK_FOUNDATION_MODEL",
                            "BedrockFoundationModelConfiguration": {
                                "ModelArn": parser_model_arn.value_as_string,
                                "ParsingPrompt": {
                                    "ParsingPromptText": (
                                        "Preserve headings, numbered procedures, warnings, table headers and rows, "
                                        "captions, page references, revision identifiers, and supersession language."
                                    )
                                },
                            },
                        },
                    },
                },
            )
            data_source.add_dependency(kb)

        for resource in self.node.find_all():
            if isinstance(resource, cdk.CfnResource):
                for tag in self._tags("knowledge-search"):
                    cdk.Tags.of(resource).add(tag["Key"], tag["Value"])

        cdk.CfnOutput(
            self, "KnowledgeBaseId",
            value=cdk.Token.as_string(kb.get_att("KnowledgeBaseId")),
        )
        cdk.CfnOutput(self, "VectorIndexArn", value=index_arn)
        cdk.CfnOutput(self, "SessionKmsKeyArn", value=key.key_arn)

    @staticmethod
    def _tags(component: str) -> list[dict[str, str]]:
        return [
            {"Key": "Project", "Value": "LCDash-AWS"},
            {"Key": "Environment", "Value": "pilot"},
            {"Key": "Tenant", "Value": "logan-synthetic"},
            {"Key": "DataScope", "Value": "approved-documents-only"},
            {"Key": "ManagedBy", "Value": "CDK"},
            {"Key": "AuditScope", "Value": "CloudTrail-bedrock-knowledge-search"},
            {"Key": "Component", "Value": component},
        ]
