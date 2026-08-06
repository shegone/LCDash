"""Cloud-native image builder for the dormant Phase 1 pilot."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_codebuild as codebuild
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct

from .config import APPROVED_REGION, NAME_PREFIX


class Phase1ImageBuildStack(cdk.Stack):
    """A least-scope CodeBuild project that can publish only the pilot image."""

    SOURCE_OBJECT_KEY = "source/lcdash-pilot.zip"

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if self.region != APPROVED_REGION:
            raise ValueError("Phase 1 image build may synthesize only in us-east-1.")

        source_bucket = s3.Bucket(
            self,
            "SourceBucket",
            bucket_name=cdk.Fn.sub(
                f"{NAME_PREFIX}-${{AWS::AccountId}}-image-source"
            ),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        repository = ecr.Repository.from_repository_name(
            self,
            "PilotRepository",
            repository_name=f"{NAME_PREFIX}-web",
        )
        build_role = iam.Role(
            self,
            "BuildRole",
            role_name=f"{NAME_PREFIX}-image-build",
            assumed_by=iam.ServicePrincipal("codebuild.amazonaws.com"),
        )
        source_bucket.grant_read(build_role, self.SOURCE_OBJECT_KEY)
        repository.grant_pull_push(build_role)
        build_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:DescribeImages",
                    "ecr:DescribeImageScanFindings",
                ],
                resources=[repository.repository_arn],
            )
        )

        build_spec = codebuild.BuildSpec.from_object(
                {
                    "version": "0.2",
                    "env": {"shell": "bash"},
                    "phases": {
                        "pre_build": {
                            "commands": [
                                "set -euo pipefail",
                                'test "${IMAGE_TAG:-}" != ""',
                                'test "${SOURCE_MANIFEST_SHA256:-}" != ""',
                                '[[ "$IMAGE_TAG" =~ ^${IMAGE_TAG_PREFIX}-[a-f0-9]{12}$ ]]',
                                '[[ "$SOURCE_MANIFEST_SHA256" =~ ^[a-f0-9]{64}$ ]]',
                                (
                                    "aws ecr get-login-password --region "
                                    '"$AWS_DEFAULT_REGION" | docker login '
                                    '--username AWS --password-stdin '
                                    '"$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.'
                                    'amazonaws.com"'
                                ),
                            ]
                        },
                        "build": {
                            "commands": [
                                'docker build --pull --file "$DOCKERFILE_PATH" '
                                '--tag "$REPOSITORY_URI:$IMAGE_TAG" .',
                                'docker run --detach --name lcdash-health '
                                '--read-only --tmpfs /tmp:rw,nosuid,nodev,size=64m '
                                '-p 127.0.0.1:8000:8000 '
                                '-e LCDASH_DEPLOYMENT_MODE=synthetic-disconnected '
                                '-e LCDASH_TENANT=logan-synthetic '
                                '-e LCDASH_DATABASE_HOST=healthcheck.invalid '
                                '-e LCDASH_DATABASE_PORT=5432 '
                                '-e LCDASH_DATABASE_NAME=synthetic_healthcheck '
                                '-e LCDASH_DATABASE_USERNAME=synthetic_healthcheck '
                                '-e LCDASH_DATABASE_PASSWORD=synthetic-placeholder-not-a-secret '
                                '"$REPOSITORY_URI:$IMAGE_TAG"',
                                (
                                    "for attempt in $(seq 1 30); do "
                                    "python -c \"import urllib.request; "
                                    "urllib.request.urlopen('http://127.0.0.1:8000/health', "
                                    "timeout=2)\" && break; "
                                    "if [ \"$attempt\" -eq 30 ]; then docker logs lcdash-health; exit 1; fi; "
                                    "sleep 2; done"
                                ),
                                "docker stop lcdash-health",
                                'docker push "$REPOSITORY_URI:$IMAGE_TAG"',
                                "touch /tmp/lcdash-image-health-tested-and-pushed",
                            ]
                        },
                        "post_build": {
                            "commands": [
                                "test -f /tmp/lcdash-image-health-tested-and-pushed",
                                (
                                    'IMAGE_DIGEST=$(aws ecr describe-images '
                                    '--repository-name "$ECR_REPOSITORY_NAME" '
                                    '--image-ids imageTag="$IMAGE_TAG" '
                                    '--query "imageDetails[0].imageDigest" --output text)'
                                ),
                                '[[ "$IMAGE_DIGEST" =~ ^sha256:[a-f0-9]{64}$ ]]',
                                (
                                    "for attempt in $(seq 1 40); do "
                                    "SCAN_STATUS=$(aws ecr describe-image-scan-findings "
                                    '--repository-name "$ECR_REPOSITORY_NAME" '
                                    '--image-id imageDigest="$IMAGE_DIGEST" '
                                    '--query "imageScanStatus.status" --output text 2>/dev/null || true); '
                                    'if [ "$SCAN_STATUS" = "COMPLETE" ]; then break; fi; '
                                    'if [ "$SCAN_STATUS" = "FAILED" ] || [ "$SCAN_STATUS" = "UNSUPPORTED_IMAGE" ]; then exit 1; fi; '
                                    'if [ "$attempt" -eq 40 ]; then exit 1; fi; sleep 15; done'
                                ),
                                (
                                    'SCAN_COUNTS=$(aws ecr describe-image-scan-findings '
                                    '--repository-name "$ECR_REPOSITORY_NAME" '
                                    '--image-id imageDigest="$IMAGE_DIGEST" '
                                    '--query "imageScanFindings.findingSeverityCounts" --output json)'
                                ),
                                'printf "IMAGE_DIGEST=%s\\nSCAN_STATUS=%s\\nSCAN_COUNTS=%s\\nSOURCE_MANIFEST_SHA256=%s\\n" '
                                '"$IMAGE_DIGEST" "$SCAN_STATUS" "$SCAN_COUNTS" "$SOURCE_MANIFEST_SHA256"',
                            ]
                        },
                    },
                }
            )
        common_environment_variables = {
            "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(value=self.account),
            "ECR_REPOSITORY_NAME": codebuild.BuildEnvironmentVariable(
                value=f"{NAME_PREFIX}-web"
            ),
            "REPOSITORY_URI": codebuild.BuildEnvironmentVariable(
                value=repository.repository_uri
            ),
        }
        common_project_arguments = {
            "role": build_role,
            "source": codebuild.Source.s3(
                bucket=source_bucket,
                path=self.SOURCE_OBJECT_KEY,
            ),
            "environment": codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.SMALL,
                privileged=True,
            ),
            "timeout": cdk.Duration.minutes(30),
            "build_spec": build_spec,
        }
        project = codebuild.Project(
            self,
            "Project",
            project_name=f"{NAME_PREFIX}-image-build",
            environment_variables={
                **common_environment_variables,
                "DOCKERFILE_PATH": codebuild.BuildEnvironmentVariable(
                    value="Dockerfile.aws-pilot"
                ),
                "IMAGE_TAG_PREFIX": codebuild.BuildEnvironmentVariable(value="source"),
            },
            **common_project_arguments,
        )
        experimental_project = codebuild.Project(
            self,
            "AlpineExperimentalProject",
            project_name=f"{NAME_PREFIX}-image-build-alpine-experimental",
            environment_variables={
                **common_environment_variables,
                "DOCKERFILE_PATH": codebuild.BuildEnvironmentVariable(
                    value="Dockerfile.aws-pilot-alpine-experimental"
                ),
                "IMAGE_TAG_PREFIX": codebuild.BuildEnvironmentVariable(
                    value="alpine-source"
                ),
            },
            **common_project_arguments,
        )

        for resource in (source_bucket, build_role, project, experimental_project):
            cdk.Tags.of(resource).add("Project", "LCDash-AWS")
            cdk.Tags.of(resource).add("Environment", "pilot")
            cdk.Tags.of(resource).add("DataScope", "synthetic-disconnected")
            cdk.Tags.of(resource).add("ManagedBy", "CDK")

        cdk.CfnOutput(self, "SourceBucketName", value=source_bucket.bucket_name)
        cdk.CfnOutput(self, "SourceObjectKey", value=self.SOURCE_OBJECT_KEY)
        cdk.CfnOutput(self, "ProjectName", value=project.project_name)
        cdk.CfnOutput(
            self,
            "AlpineExperimentalProjectName",
            value=experimental_project.project_name,
        )
