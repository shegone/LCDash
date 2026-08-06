"""Dormant infrastructure for a one-way historical analytics import."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from .config import APPROVED_REGION, NAME_PREFIX


class Phase2AnalyticsImportStack(cdk.Stack):
    """Private staging and an inert ECS task definition; never starts a task."""

    STAGING_PREFIX = "tenants/logan-synthetic/historical-analytics/"
    RETENTION_DAYS = 3

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if self.region != APPROVED_REGION:
            raise ValueError("Analytics import may synthesize only in us-east-1.")

        image_uri = cdk.CfnParameter(
            self,
            "ImporterImageUri",
            type="String",
            allowed_pattern=(
                r"^[0-9]{12}\.dkr\.ecr\.us-east-1\.amazonaws\.com/"
                r"[a-z0-9._/-]+@sha256:[a-f0-9]{64}$"
            ),
            description="Approved immutable ECR image URI for the importer",
        )
        repository_arn = cdk.CfnParameter(
            self,
            "ImporterRepositoryArn",
            type="String",
            allowed_pattern=(
                r"^arn:aws(-us-gov)?:ecr:us-east-1:[0-9]{12}:repository/"
                r"[a-z0-9._/-]+$"
            ),
            description="Exact ECR repository ARN containing the approved image",
        )
        target_secret_arn = cdk.CfnParameter(
            self,
            "TargetDatabaseSecretArn",
            type="String",
            allowed_pattern=(
                r"^arn:aws(-us-gov)?:secretsmanager:us-east-1:[0-9]{12}:"
                r"secret:[A-Za-z0-9/_+=.@-]+$"
            ),
            description="Target-only database secret ARN; never a source or CAD secret",
        )
        staged_object_key = cdk.CfnParameter(
            self,
            "StagedObjectKey",
            type="String",
            allowed_pattern=(
                r"^tenants/logan-synthetic/historical-analytics/"
                r"[A-Za-z0-9._-]+\.json\.enc$"
            ),
            description="Exact approved encrypted historical analytics object key",
        )
        plaintext_sha256 = cdk.CfnParameter(
            self,
            "ExpectedPlaintextSha256",
            type="String",
            allowed_pattern=r"^[a-f0-9]{64}$",
            description="Approved decrypted bundle SHA-256 from transfer evidence",
        )
        vpc_id = cdk.CfnParameter(
            self,
            "FoundationVpcId",
            type="AWS::EC2::VPC::Id",
            description="Existing foundation VPC; no VPC is created by this stack",
        )
        target_database_security_group_id = cdk.CfnParameter(
            self,
            "TargetDatabaseSecurityGroupId",
            type="AWS::EC2::SecurityGroup::Id",
            description="Exact target RDS security group for PostgreSQL-only egress",
        )

        key = kms.Key(
            self,
            "StagingKey",
            alias=f"alias/{NAME_PREFIX}-analytics-import-staging",
            description="KMS key for short-lived historical analytics staging only",
            enable_key_rotation=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        bucket = s3.Bucket(
            self,
            "StagingBucket",
            bucket_name=cdk.Fn.sub(
                f"{NAME_PREFIX}-${{AWS::AccountId}}-analytics-import-staging"
            ),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=key,
            bucket_key_enabled=True,
            enforce_ssl=True,
            versioned=False,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireHistoricalAnalyticsStaging",
                    prefix=self.STAGING_PREFIX,
                    expiration=cdk.Duration.days(self.RETENTION_DAYS),
                ),
                s3.LifecycleRule(
                    id="AbortIncompleteMultipartUploads",
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(1),
                ),
            ],
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )

        task_role = iam.Role(
            self,
            "ImporterTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description=(
                "One-way historical analytics importer; staging prefix and KMS only, "
                "with no CAD or source-system permissions"
            ),
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[bucket.arn_for_objects(staged_object_key.value_as_string)],
            )
        )
        key.grant_decrypt(task_role)

        execution_role = iam.Role(
            self,
            "ImporterExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Pulls the approved image and injects the target-only secret",
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                ],
                resources=[repository_arn.value_as_string],
            )
        )
        execution_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[target_secret_arn.value_as_string],
            )
        )

        log_group = logs.LogGroup(
            self,
            "ImporterLogs",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        log_group.grant_write(execution_role)

        importer_security_group = ec2.CfnSecurityGroup(
            self,
            "ImporterSecurityGroup",
            group_description=(
                "One-off analytics importer: DNS, HTTPS, and target PostgreSQL only"
            ),
            vpc_id=vpc_id.value_as_string,
            security_group_egress=[
                ec2.CfnSecurityGroup.EgressProperty(
                    ip_protocol="udp",
                    from_port=53,
                    to_port=53,
                    cidr_ip="10.42.0.2/32",
                    description="VPC resolver DNS",
                ),
                ec2.CfnSecurityGroup.EgressProperty(
                    ip_protocol="tcp",
                    from_port=53,
                    to_port=53,
                    cidr_ip="10.42.0.2/32",
                    description="VPC resolver DNS fallback",
                ),
                ec2.CfnSecurityGroup.EgressProperty(
                    ip_protocol="tcp",
                    from_port=443,
                    to_port=443,
                    cidr_ip="0.0.0.0/0",
                    description="AWS APIs over HTTPS",
                ),
                ec2.CfnSecurityGroup.EgressProperty(
                    ip_protocol="tcp",
                    from_port=5432,
                    to_port=5432,
                    destination_security_group_id=(
                        target_database_security_group_id.value_as_string
                    ),
                    description="Target RDS PostgreSQL only",
                ),
            ],
        )
        ec2.CfnSecurityGroupIngress(
            self,
            "TargetDatabaseIngress",
            group_id=target_database_security_group_id.value_as_string,
            ip_protocol="tcp",
            from_port=5432,
            to_port=5432,
            source_security_group_id=importer_security_group.attr_group_id,
            description="One-off analytics importer PostgreSQL access",
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "ImporterTaskDefinition",
            family=f"{NAME_PREFIX}-historical-analytics-import",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task_role,
            execution_role=execution_role,
        )
        target_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "TargetDatabaseSecret", target_secret_arn.value_as_string
        )
        container = task_definition.add_container(
            "Importer",
            image=ecs.ContainerImage.from_registry(image_uri.value_as_string),
            readonly_root_filesystem=True,
            user="10001:10001",
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="historical-analytics-import", log_group=log_group
            ),
            command=["python", "-m", "app.tools.phase2_analytics_import_runtime"],
            environment={
                "LCDASH_IMPORT_MODE": "historical-staged-one-way",
                "LCDASH_IMPORT_STAGING_BUCKET": bucket.bucket_name,
                "LCDASH_IMPORT_STAGING_PREFIX": self.STAGING_PREFIX,
                "LCDASH_IMPORT_OBJECT_KEY": staged_object_key.value_as_string,
                "LCDASH_IMPORT_PLAINTEXT_SHA256": plaintext_sha256.value_as_string,
                "LCDASH_TENANT": "logan-synthetic",
                "LCDASH_CLOUD_CAD_ENABLED": "false",
                "LCDASH_TARGET_DATABASE_NAME": "lcdash",
            },
            secrets={
                "LCDASH_TARGET_DATABASE_USERNAME": ecs.Secret.from_secrets_manager(
                    target_secret, "username"
                ),
                "LCDASH_TARGET_DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(
                    target_secret, "password"
                ),
                "LCDASH_TARGET_DATABASE_HOST": ecs.Secret.from_secrets_manager(
                    target_secret, "host"
                ),
                "LCDASH_TARGET_DATABASE_PORT": ecs.Secret.from_secrets_manager(
                    target_secret, "port"
                ),
            },
        )
        task_definition.add_volume(name="ImporterTemp")
        container.add_mount_points(
            ecs.MountPoint(
                container_path="/tmp", source_volume="ImporterTemp", read_only=False
            )
        )

        for resource in (
            key,
            bucket,
            task_role,
            execution_role,
            log_group,
            task_definition,
            importer_security_group,
        ):
            cdk.Tags.of(resource).add("Project", "LCDash-AWS")
            cdk.Tags.of(resource).add("Environment", "pilot")
            cdk.Tags.of(resource).add("Phase", "2-preactivation")
            cdk.Tags.of(resource).add("Tenant", "logan-synthetic")
            cdk.Tags.of(resource).add("DataScope", "approved-historical-analytics")
            cdk.Tags.of(resource).add("ManagedBy", "CDK")

        cdk.CfnOutput(self, "StagingBucketName", value=bucket.bucket_name)
        cdk.CfnOutput(self, "StagingPrefix", value=self.STAGING_PREFIX)
        cdk.CfnOutput(self, "ImporterTaskDefinitionArn", value=task_definition.task_definition_arn)
        cdk.CfnOutput(
            self,
            "ImporterSecurityGroupId",
            value=importer_security_group.attr_group_id,
        )
