"""Dormant private document-library storage for the Phase 1 pilot."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

from .config import APPROVED_REGION, NAME_PREFIX


class Phase1DocumentLibraryStack(cdk.Stack):
    """Private, read-only application library with no upload automation."""

    TENANT_ROOT = "tenants/logan-synthetic/document-library"
    READ_PREFIXES = (
        f"{TENANT_ROOT}/centralsquare/current",
        f"{TENANT_ROOT}/mindshare/current",
        f"{TENANT_ROOT}/mindshare/sanitized-system",
        f"{TENANT_ROOT}/mindshare/software-catalog",
        f"{TENANT_ROOT}/manifests/approved",
    )
    STAGING_PREFIX = f"{TENANT_ROOT}/staging"

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if self.region != APPROVED_REGION:
            raise ValueError(
                "Phase 1 document library may synthesize only in us-east-1."
            )

        bucket = s3.Bucket(
            self,
            "DocumentLibraryBucket",
            bucket_name=cdk.Fn.sub(
                f"{NAME_PREFIX}-${{AWS::AccountId}}-document-library"
            ),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="AbortIncompleteMultipartUploads",
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(1),
                ),
                s3.LifecycleRule(
                    id="ExpireUnapprovedStagingObjects",
                    prefix=f"{self.STAGING_PREFIX}/",
                    expiration=cdk.Duration.days(7),
                ),
            ],
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        read_role = iam.Role(
            self,
            "DocumentLibraryReadRole",
            role_name=f"{NAME_PREFIX}-document-library-read",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description=(
                "Read-only access to approved Logan synthetic document-library "
                "prefixes; not an upload or administration role"
            ),
        )
        read_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[bucket.bucket_arn],
                conditions={
                    "StringLike": {
                        "s3:prefix": [f"{prefix}/*" for prefix in self.READ_PREFIXES]
                    }
                },
            )
        )
        read_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[
                    bucket.arn_for_objects(f"{prefix}/*")
                    for prefix in self.READ_PREFIXES
                ],
            )
        )

        for resource in (bucket, read_role):
            cdk.Tags.of(resource).add("Project", "LCDash-AWS")
            cdk.Tags.of(resource).add("Environment", "pilot")
            cdk.Tags.of(resource).add("Phase", "1")
            cdk.Tags.of(resource).add("Tenant", "logan-synthetic")
            cdk.Tags.of(resource).add("DataScope", "approved-documents-only")
            cdk.Tags.of(resource).add("ManagedBy", "CDK")

        cdk.CfnOutput(
            self,
            "DocumentLibraryBucketName",
            value=bucket.bucket_name,
            description="Private Phase 1 document-library bucket name",
        )
        cdk.CfnOutput(
            self,
            "DocumentLibraryReadRoleArn",
            value=read_role.role_arn,
            description="Dormant least-privilege application read role ARN",
        )
