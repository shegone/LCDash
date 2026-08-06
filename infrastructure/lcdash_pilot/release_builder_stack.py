"""Local-only CDK definition for the single-project pilot release builder."""

from __future__ import annotations

from pathlib import Path

import aws_cdk as cdk
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3_assets as s3_assets
from constructs import Construct

from .config import APPROVED_REGION, NAME_PREFIX


class Phase1ReleaseBuilderStack(cdk.Stack):
    """One scoped source asset, one role, and one Docker release project."""

    PROJECT_NAME = f"{NAME_PREFIX}-release-builder"
    REPOSITORY_NAME = f"{NAME_PREFIX}-web"

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if self.region != APPROVED_REGION:
            raise ValueError("Phase 1 release builder may synthesize only in us-east-1.")

        repository_root = Path(__file__).resolve().parents[2]
        source_asset = s3_assets.Asset(
            self,
            "ReleaseSourceAsset",
            path=str(repository_root),
            exclude=[
                ".git",
                ".git/**",
                ".github/**",
                ".venv/**",
                ".env",
                ".env.*",
                ".kiro/**",
                ".cptr/**",
                "**/__pycache__/**",
                "**/*.pyc",
                "**/*.log",
                ".pytest_cache/**",
                "cdk.out",
                "cdk.out/**",
                "cdk.out/**/*",
                "infrastructure/cdk.out",
                "infrastructure/cdk.out/**",
                "infrastructure/cdk.out/**/*",
                "infrastructure/work",
                "infrastructure/.venv/**",
                "infrastructure/work/**",
                "infrastructure/work/**/*",
                "work",
                "work/**",
                "work/**/*",
                "handoffs/**",
                "agent-skills/**",
                "deploy/**",
                "docs/**",
                "scripts/**",
                "tests/**",
                "infrastructure/iam/**",
                "infrastructure/lcdash_pilot/**",
                "infrastructure/tests/**",
                "infrastructure/tools/**",
                "infrastructure/*.py",
                "infrastructure/requirements.txt",
                "Dockerfile",
                "Dockerfile.aws-pilot-alpine-experimental",
            ],
            ignore_mode=cdk.IgnoreMode.GLOB,
        )
        repository = ecr.Repository.from_repository_name(
            self,
            "ExistingPilotRepository",
            repository_name=self.REPOSITORY_NAME,
        )
        role = iam.Role(
            self,
            "ReleaseBuilderRole",
            role_name=f"{NAME_PREFIX}-release-builder",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
            description=(
                "Builds the disconnected LCDash pilot image from one CDK source asset; "
                "no deployment or runtime-data authority"
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="ReadOnlyScopedSourceAsset",
                actions=["s3:GetObject", "s3:GetObjectVersion"],
                resources=[
                    source_asset.bucket.arn_for_objects(source_asset.s3_object_key)
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="EcrAuthentication",
                actions=["ecr:GetAuthorizationToken"],
                resources=["*"],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="PublishOnlyPilotRepository",
                actions=[
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:BatchGetImage",
                    "ecr:CompleteLayerUpload",
                    "ecr:DescribeImages",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:InitiateLayerUpload",
                    "ecr:PutImage",
                    "ecr:UploadLayerPart",
                ],
                resources=[repository.repository_arn],
            )
        )
        log_group_arn = cdk.Stack.of(self).format_arn(
            service="logs",
            resource="log-group",
            resource_name=f"/aws/codebuild/{self.PROJECT_NAME}:*",
            arn_format=cdk.ArnFormat.COLON_RESOURCE_NAME,
        )
        role.add_to_policy(
            iam.PolicyStatement(
                sid="WriteOnlyOwnBuildLogs",
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[log_group_arn],
            )
        )

        project = codebuild.CfnProject(
            self,
            "ReleaseBuilderProject",
            name=self.PROJECT_NAME,
            service_role=role.role_arn,
            source=codebuild.CfnProject.SourceProperty(
                type="S3",
                location=cdk.Fn.join(
                    "/",
                    [source_asset.bucket.bucket_name, source_asset.s3_object_key],
                ),
                build_spec="infrastructure/buildspecs/release-builder.yml",
            ),
            artifacts=codebuild.CfnProject.ArtifactsProperty(type="NO_ARTIFACTS"),
            cache=codebuild.CfnProject.ProjectCacheProperty(type="NO_CACHE"),
            environment=codebuild.CfnProject.EnvironmentProperty(
                image="aws/codebuild/standard:7.0",
                type="LINUX_CONTAINER",
                compute_type="BUILD_GENERAL1_SMALL",
                image_pull_credentials_type="CODEBUILD",
                privileged_mode=True,
                environment_variables=[
                    codebuild.CfnProject.EnvironmentVariableProperty(
                        name="AWS_ACCOUNT_ID",
                        type="PLAINTEXT",
                        value=self.account,
                    ),
                    codebuild.CfnProject.EnvironmentVariableProperty(
                        name="DOCKERFILE_PATH",
                        type="PLAINTEXT",
                        value="Dockerfile.aws-pilot",
                    ),
                    codebuild.CfnProject.EnvironmentVariableProperty(
                        name="ECR_REPOSITORY_NAME",
                        type="PLAINTEXT",
                        value=self.REPOSITORY_NAME,
                    ),
                    codebuild.CfnProject.EnvironmentVariableProperty(
                        name="REPOSITORY_URI",
                        type="PLAINTEXT",
                        value=repository.repository_uri,
                    ),
                    codebuild.CfnProject.EnvironmentVariableProperty(
                        name="SOURCE_ASSET_HASH",
                        type="PLAINTEXT",
                        value=source_asset.asset_hash,
                    ),
                ],
            ),
            logs_config=codebuild.CfnProject.LogsConfigProperty(
                cloud_watch_logs=codebuild.CfnProject.CloudWatchLogsConfigProperty(
                    status="ENABLED",
                    group_name=f"/aws/codebuild/{self.PROJECT_NAME}",
                )
            ),
            timeout_in_minutes=30,
            queued_timeout_in_minutes=30,
        )

        for resource in (role, project):
            cdk.Tags.of(resource).add("Project", "LCDash-AWS")
            cdk.Tags.of(resource).add("Environment", "pilot")
            cdk.Tags.of(resource).add("DataScope", "synthetic-disconnected")
            cdk.Tags.of(resource).add("ManagedBy", "CDK")

        cdk.CfnOutput(self, "ReleaseBuilderProjectName", value=project.ref)
        cdk.CfnOutput(self, "ReleaseSourceAssetHash", value=source_asset.asset_hash)
        cdk.CfnOutput(self, "ReleaseRepositoryName", value=self.REPOSITORY_NAME)
