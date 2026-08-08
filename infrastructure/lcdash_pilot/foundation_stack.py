"""Lean Phase 1 synthetic/disconnected pilot foundation."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    aws_budgets as budgets,
    aws_certificatemanager as acm,
    aws_cloudtrail as cloudtrail,
    aws_cognito as cognito,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_elasticloadbalancingv2_actions as elbv2_actions,
    aws_iam as iam,
    aws_logs as logs,
    aws_rds as rds,
    aws_s3 as s3,
)
from constructs import Construct

from .config import APPROVED_REGION, NAME_PREFIX, PILOT_DOMAIN_NAME


class Phase1FoundationStack(cdk.Stack):
    """One non-authoritative pilot cell with no operational integrations."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if self.region != APPROVED_REGION:
            raise ValueError("Phase 1 foundation may synthesize only in us-east-1.")

        parameters = self._parameters()
        self._apply_tags()

        vpc = ec2.Vpc(
            self,
            "Vpc",
            vpc_name=f"{NAME_PREFIX}-vpc",
            ip_addresses=ec2.IpAddresses.cidr("10.42.0.0/20"),
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="database",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        content_bucket = s3.Bucket(
            self,
            "ContentBucket",
            bucket_name=cdk.Fn.sub(
                f"{NAME_PREFIX}-${{AWS::AccountId}}-content"
            ),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=False,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireShortLivedReports",
                    prefix="logan-synthetic/reports/",
                    expiration=cdk.Duration.days(7),
                )
            ],
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        repository = ecr.Repository(
            self,
            "Repository",
            repository_name=f"{NAME_PREFIX}-web",
            image_scan_on_push=True,
            lifecycle_rules=[ecr.LifecycleRule(max_image_count=5)],
            removal_policy=cdk.RemovalPolicy.DESTROY,
            empty_on_delete=True,
        )

        cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=f"{NAME_PREFIX}-cluster",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.DISABLED,
        )
        log_group = logs.LogGroup(
            self,
            "ApplicationLogs",
            log_group_name=f"/lcdash/{NAME_PREFIX}/web",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        task_role = iam.Role(
            self,
            "ApplicationTaskRole",
            role_name=f"{NAME_PREFIX}-task",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[parameters["cloud_cad_secret_arn"].value_as_string],
            )
        )
        task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDefinition",
            family=f"{NAME_PREFIX}-web",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task_role,
        )
        task_definition.add_volume(name="RuntimeTemp")
        repository.grant_pull(task_definition.obtain_execution_role())
        container = task_definition.add_container(
            "Web",
            image=ecs.ContainerImage.from_registry(
                cdk.Token.as_string(
                    cdk.Fn.condition_if(
                        "PilotImagePublishedCondition",
                        cdk.Fn.join(
                            "",
                            [
                                repository.repository_uri,
                                "@",
                                parameters["pilot_image_digest"].value_as_string,
                            ],
                        ),
                        cdk.Fn.join(
                            "",
                            [
                                repository.repository_uri,
                                ":dormant-not-published",
                            ],
                        ),
                    )
                )
            ),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="web",
                log_group=log_group,
            ),
            readonly_root_filesystem=True,
            user="10001:10001",
            environment={
                "LCDASH_DEBUG": "false",
                "LCDASH_DEPLOYMENT_MODE": "synthetic-disconnected",
                "LCDASH_TENANT": "logan-synthetic",
                "LCDASH_CLOUD_CAD_ENABLED": "true",
                "LCDASH_CLOUD_CAD_MODE": "centralsquare-read-poll",
                "LCDASH_CLOUD_CAD_SECRET_ARN": parameters[
                    "cloud_cad_secret_arn"
                ].value_as_string,
                "LCDASH_CLOUD_CAD_POLL_SECONDS": "30",
                "LCDASH_CLOUD_CAD_RECONCILIATION_OVERLAP_SECONDS": "120",
                "LCDASH_CLOUD_AI_MODE": "advisory-rag",
                "LCDASH_CLOUD_AI_KNOWLEDGE_BASE_ID": parameters[
                    "cloud_ai_knowledge_base_id"
                ].value_as_string,
                "LCDASH_CLOUD_AI_DOCUMENTS_INGESTED": parameters[
                    "cloud_ai_documents_ingested"
                ].value_as_string,
                "LCDASH_CLOUD_AI_ALLOWED_S3_PREFIXES": parameters[
                    "cloud_ai_allowed_s3_prefixes"
                ].value_as_string,
                "LCDASH_CLOUD_AI_GENERATION_MODEL_ID": "us.amazon.nova-pro-v1:0",
                "LCDASH_CLOUD_AI_MAX_OUTPUT_TOKENS": "400",
                "LCDASH_CLOUD_AI_RETRIEVAL_RESULT_LIMIT": "5",
                "LCDASH_CLOUD_AI_POLLY_VOICE": "Joanna",
                "LCDASH_CLOUD_AI_VOICE_ENABLED": "true",
                # MAE read-only tool-calling. Model set explicitly to Nova Pro
                # rather than relying on the generation-model fallback, so a
                # later change to the generation model cannot silently drop
                # tool-calling onto a weaker model.
                "LCDASH_CLOUD_AI_TOOL_CALLING_ENABLED": "true",
                "LCDASH_CLOUD_AI_TOOL_MODEL_ID": "us.amazon.nova-pro-v1:0",
                "EMS_DELAY_ALERT_ENABLED": "false",
                "EMS_DELAY_ALERT_MODE": "disabled",
                "NGA911_PROVIDER_MODE": "mock",
                "TMPDIR": "/tmp",
                "HOME": "/tmp/home",
                "XDG_CACHE_HOME": "/tmp/cache",
            },
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)\" || exit 1",
                ],
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                retries=3,
                start_period=cdk.Duration.seconds(20),
            ),
        )
        container.add_mount_points(
            ecs.MountPoint(
                source_volume="RuntimeTemp",
                container_path="/tmp",
                read_only=False,
            )
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        database_log_group = logs.LogGroup(
            self,
            "DatabaseLogs",
            log_group_name=f"/aws/rds/instance/{NAME_PREFIX}-db/postgresql",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        database = rds.DatabaseInstance(
            self,
            "Database",
            instance_identifier=f"{NAME_PREFIX}-db",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.of("17.10", "17")
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE4_GRAVITON,
                ec2.InstanceSize.MICRO,
            ),
            credentials=rds.Credentials.from_generated_secret("lcdash_app"),
            database_name="lcdash",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
            publicly_accessible=False,
            multi_az=False,
            allocated_storage=20,
            max_allocated_storage=20,
            storage_encrypted=True,
            # The analytics warehouse holds imported historical CAD data that
            # cannot be cheaply reconstructed in cloud (the collector cannot run
            # here yet), so the database is protected rather than disposable:
            # 7 days of automated backups gives point-in-time recovery, deletion
            # protection blocks an accidental drop, and RETAIN keeps the instance
            # alive even if the stack itself is torn down.
            backup_retention=cdk.Duration.days(7),
            delete_automated_backups=False,
            deletion_protection=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            cloudwatch_logs_exports=["postgresql"],
        )
        database.node.add_dependency(database_log_group)
        if database.secret is None:
            raise ValueError("Generated database secret was not created.")
        container.add_environment(
            "LCDASH_DATABASE_HOST",
            database.db_instance_endpoint_address,
        )
        container.add_environment(
            "LCDASH_DATABASE_PORT",
            database.db_instance_endpoint_port,
        )
        container.add_environment("LCDASH_DATABASE_NAME", "lcdash")
        container.add_secret(
            "LCDASH_DATABASE_USERNAME",
            ecs.Secret.from_secrets_manager(database.secret, "username"),
        )
        container.add_secret(
            "LCDASH_DATABASE_PASSWORD",
            ecs.Secret.from_secrets_manager(database.secret, "password"),
        )

        alb_security_group = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=vpc,
            security_group_name=f"{NAME_PREFIX}-alb",
            allow_all_outbound=False,
        )
        alb_security_group.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80))
        alb_security_group.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443))

        app_security_group = ec2.SecurityGroup(
            self,
            "ApplicationSecurityGroup",
            vpc=vpc,
            security_group_name=f"{NAME_PREFIX}-app",
            allow_all_outbound=False,
        )
        app_security_group.add_ingress_rule(alb_security_group, ec2.Port.tcp(8000))
        alb_security_group.add_egress_rule(app_security_group, ec2.Port.tcp(8000))
        app_security_group.add_egress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443))
        resolver = ec2.Peer.ipv4("10.42.0.2/32")
        app_security_group.add_egress_rule(resolver, ec2.Port.udp(53))
        app_security_group.add_egress_rule(resolver, ec2.Port.tcp(53))
        database.connections.allow_default_port_from(app_security_group)

        service = ecs.FargateService(
            self,
            "Service",
            service_name=f"{NAME_PREFIX}-web",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=parameters["desired_task_count"].value_as_number,
            assign_public_ip=True,
            security_groups=[app_security_group],
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            min_healthy_percent=0,
            max_healthy_percent=200,
        )

        load_balancer = elbv2.ApplicationLoadBalancer(
            self,
            "LoadBalancer",
            load_balancer_name=f"{NAME_PREFIX}-alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_security_group,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )
        certificate = acm.Certificate.from_certificate_arn(
            self,
            "Certificate",
            parameters["certificate_arn"].value_as_string,
        )
        user_pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=f"{NAME_PREFIX}-users",
            self_sign_up_enabled=False,
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(
                sms=False,
                otp=True,
            ),
            sign_in_aliases=cognito.SignInAliases(email=True),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            password_policy=cognito.PasswordPolicy(
                min_length=14,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=cdk.Duration.days(1),
            ),
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        cognito.CfnUserPoolGroup(
            self,
            "PilotViewerGroup",
            user_pool_id=user_pool.user_pool_id,
            group_name="lcdash-pilot-viewer",
            description=(
                "Read-only access to the Logan synthetic pilot; no operational outputs."
            ),
            precedence=20,
        )
        cognito.CfnUserPoolGroup(
            self,
            "PilotReviewerGroup",
            user_pool_id=user_pool.user_pool_id,
            group_name="lcdash-pilot-reviewer",
            description=(
                "Read-only pilot review and evaluation; no tenant or operational authority."
            ),
            precedence=10,
        )
        alb_callback_url = cdk.Fn.join(
            "",
            ["https://", PILOT_DOMAIN_NAME, "/oauth2/idpresponse"],
        )
        alb_logout_url = cdk.Fn.join(
            "",
            ["https://", PILOT_DOMAIN_NAME, "/"],
        )
        user_pool_client = user_pool.add_client(
            "AlbClient",
            user_pool_client_name=f"{NAME_PREFIX}-alb",
            generate_secret=True,
            access_token_validity=cdk.Duration.minutes(15),
            id_token_validity=cdk.Duration.minutes(15),
            refresh_token_validity=cdk.Duration.days(1),
            auth_session_validity=cdk.Duration.minutes(3),
            enable_token_revocation=True,
            refresh_token_rotation_grace_period=cdk.Duration.seconds(0),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[alb_callback_url],
                logout_urls=[alb_logout_url],
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            prevent_user_existence_errors=True,
        )
        user_pool_domain = user_pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=parameters["cognito_domain_prefix"].value_as_string
            ),
        )
        https_listener = load_balancer.add_listener(
            "HttpsListener",
            port=443,
            certificates=[certificate],
            ssl_policy=elbv2.SslPolicy.RECOMMENDED_TLS,
        )
        target_group = elbv2.ApplicationTargetGroup(
            self,
            "WebTarget",
            vpc=vpc,
            port=8000,
            protocol=elbv2.ApplicationProtocol.HTTP,
            targets=[service],
            health_check=elbv2.HealthCheck(path="/health"),
        )
        https_listener.add_action(
            "AuthenticateThenForward",
            action=elbv2_actions.AuthenticateCognitoAction(
                user_pool=user_pool,
                user_pool_client=user_pool_client,
                user_pool_domain=user_pool_domain,
                next=elbv2.ListenerAction.forward([target_group]),
                on_unauthenticated_request=elbv2.UnauthenticatedAction.AUTHENTICATE,
                scope="openid email profile",
                session_cookie_name="LCDashPilotAuth",
                # The ALB's own session cookie, not a Cognito token, is what
                # actually gates re-login -- it is checked locally by the ALB
                # and is independent of the 15-minute access/ID token
                # lifetimes. Matches the 24-hour refresh token window.
                session_timeout=cdk.Duration.hours(24),
                allow_https_outbound=True,
            ),
        )
        load_balancer.add_listener(
            "HttpRedirect",
            port=80,
            default_action=elbv2.ListenerAction.redirect(
                protocol="HTTPS",
                port="443",
                permanent=True,
            ),
        )

        self._grant_content_access(task_role, content_bucket)
        self._grant_document_library_read(task_role)
        self._grant_managed_providers(
            task_role,
            parameters["cloud_ai_knowledge_base_id"],
        )
        self._add_budget(parameters)
        self._add_optional_trail(parameters["create_trail"])
        self._apply_parameter_tags(parameters)

        cdk.CfnOutput(self, "ApplicationUrl", value=f"https://{PILOT_DOMAIN_NAME}")
        cdk.CfnOutput(
            self,
            "HostingerApplicationCnameTarget",
            value=load_balancer.load_balancer_dns_name,
            description=(
                "After foundation deployment, create a Hostinger CNAME for aws.logan911.com "
                "to this ALB hostname and validate it externally."
            ),
        )

    def _parameters(self) -> dict[str, cdk.CfnParameter]:
        parameters = {
            "certificate_arn": cdk.CfnParameter(self, "CertificateArn", type="String"),
            "cognito_domain_prefix": cdk.CfnParameter(self, "CognitoDomainPrefix", type="String"),
            "budget_owner": cdk.CfnParameter(self, "BudgetOwner", type="String", min_length=1),
            "budget_email": cdk.CfnParameter(self, "BudgetSubscriberEmail", type="String", min_length=3),
            "owner": cdk.CfnParameter(self, "Owner", type="String", min_length=1),
            "cost_center": cdk.CfnParameter(self, "CostCenter", type="String", min_length=1),
            "expiration": cdk.CfnParameter(self, "Expiration", type="String", min_length=1),
            "cloud_ai_knowledge_base_id": cdk.CfnParameter(
                self,
                "CloudAiKnowledgeBaseId",
                type="String",
                allowed_pattern="^[A-Z0-9]{10}$",
                description="Existing reviewed Bedrock knowledge base ID.",
            ),
            "cloud_ai_documents_ingested": cdk.CfnParameter(
                self,
                "CloudAiDocumentsIngested",
                type="String",
                allowed_values=["true", "false"],
                default="false",
            ),
            "cloud_ai_allowed_s3_prefixes": cdk.CfnParameter(
                self,
                "CloudAiAllowedS3Prefixes",
                type="String",
                min_length=1,
                description="Comma-separated approved S3 prefixes for cited retrieval.",
            ),
            "create_trail": cdk.CfnParameter(
                self,
                "CreatePilotCloudTrail",
                type="String",
                allowed_values=["true", "false"],
                default="false",
            ),
            "desired_task_count": cdk.CfnParameter(
                self,
                "PilotServiceDesiredCount",
                type="Number",
                allowed_values=["0", "1"],
                default=0,
                description=(
                    "Keep at 0 for initial foundation creation. Set to 1 only in a "
                    "separately reviewed update after the pilot image is published."
                ),
            ),
            "pilot_image_digest": cdk.CfnParameter(
                self,
                "PilotImageDigest",
                type="String",
                default="NOT_PUBLISHED",
                allowed_pattern=r"^(NOT_PUBLISHED|sha256:[a-f0-9]{64})$",
                constraint_description=(
                    "Use NOT_PUBLISHED only while desired count is zero, or supply "
                    "an immutable lowercase sha256 image digest."
                ),
                description=(
                    "Immutable digest published to the Phase 1 ECR repository. "
                    "NOT_PUBLISHED is the dormant initial placeholder only."
                ),
            ),
            "cloud_cad_secret_arn": cdk.CfnParameter(
                self,
                "CloudCadReadSecretArn",
                type="String",
                allowed_pattern=(
                    r"^arn:aws:secretsmanager:us-east-1:862772137583:secret:"
                    r"lcdash-p1-logan-use1/centralsquare/read-only-[A-Za-z0-9]{6}$"
                ),
                constraint_description=(
                    "Supply the exact reviewed Logan CentralSquare read-only secret ARN."
                ),
                description=(
                    "Provider reference only; the disabled-default task does not resolve it."
                ),
                no_echo=True,
            ),
        }
        cdk.CfnRule(
            self,
            "PilotImageRequiredForActivation",
            assertions=[
                cdk.CfnRuleAssertion(
                    assert_=cdk.Fn.condition_or(
                        cdk.Fn.condition_equals(
                            parameters["desired_task_count"].value_as_string,
                            "0",
                        ),
                        cdk.Fn.condition_not(
                            cdk.Fn.condition_equals(
                                parameters["pilot_image_digest"].value_as_string,
                                "NOT_PUBLISHED",
                            )
                        ),
                    ),
                    assert_description=(
                        "PilotServiceDesiredCount=1 requires a published immutable "
                        "PilotImageDigest."
                    ),
                )
            ],
        )
        cdk.CfnCondition(
            self,
            "PilotServiceActivatedCondition",
            expression=cdk.Fn.condition_equals(
                parameters["desired_task_count"].value_as_string,
                "1",
            ),
        )
        cdk.CfnCondition(
            self,
            "PilotImagePublishedCondition",
            expression=cdk.Fn.condition_not(
                cdk.Fn.condition_equals(
                    parameters["pilot_image_digest"].value_as_string,
                    "NOT_PUBLISHED",
                )
            ),
        )
        return parameters

    def _apply_tags(self) -> None:
        fixed_tags = {
            "Project": "LCDash-AWS",
            "Environment": "pilot",
            "Phase": "1",
            "Tenant": "logan-synthetic",
            "Region": APPROVED_REGION,
            "DataScope": "synthetic-disconnected",
            "Authority": "non-authoritative",
            "ManagedBy": "CDK",
        }
        for key, value in fixed_tags.items():
            cdk.Tags.of(self).add(key, value)

    def _apply_parameter_tags(
        self, parameters: dict[str, cdk.CfnParameter]
    ) -> None:
        parameter_tags = {
            "Owner": parameters["owner"].value_as_string,
            "BudgetOwner": parameters["budget_owner"].value_as_string,
            "CostCenter": parameters["cost_center"].value_as_string,
            "Expiration": parameters["expiration"].value_as_string,
        }
        for construct in self.node.find_all():
            if not isinstance(construct, cdk.CfnResource):
                continue
            for key, value in parameter_tags.items():
                cdk.Tags.of(construct).add(key, value)

    def _grant_content_access(self, role: iam.Role, bucket: s3.Bucket) -> None:
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[bucket.bucket_arn],
                conditions={
                    "StringLike": {
                        "s3:prefix": [
                            "logan-synthetic/gis/*",
                            "logan-synthetic/knowledge/*",
                            "logan-synthetic/reports/*",
                        ]
                    }
                },
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[
                    bucket.arn_for_objects("logan-synthetic/gis/*"),
                    bucket.arn_for_objects("logan-synthetic/knowledge/*"),
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                resources=[bucket.arn_for_objects("logan-synthetic/reports/*")],
            )
        )

    def _grant_document_library_read(self, role: iam.Role) -> None:
        """Read-only access to the same two approved-document prefixes the
        Bedrock Knowledge Base already retrieves from -- one reviewed set of
        164 documents, one source of truth for both citation retrieval and
        the document library UI. Never grants Put/Delete; this bucket holds
        a signed approval gate's output, not an application-writable store.
        """
        document_library_bucket = s3.Bucket.from_bucket_name(
            self,
            "DocumentLibraryBucket",
            bucket_name=cdk.Fn.sub(
                f"{NAME_PREFIX}-${{AWS::AccountId}}-document-library"
            ),
        )
        approved_prefixes = [
            "tenants/logan-synthetic/document-library/mindshare/current/"
            "onprem-approved-164-2026-08-05/*",
            "tenants/logan-synthetic/document-library/centralsquare/current/"
            "onprem-approved-164-2026-08-05/*",
        ]
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[document_library_bucket.bucket_arn],
                conditions={"StringLike": {"s3:prefix": approved_prefixes}},
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[
                    document_library_bucket.arn_for_objects(prefix)
                    for prefix in approved_prefixes
                ],
            )
        )

    def _grant_managed_providers(
        self,
        role: iam.Role,
        knowledge_base_id: cdk.CfnParameter,
    ) -> None:
        region_condition = {"StringEquals": {"aws:RequestedRegion": APPROVED_REGION}}
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=[cdk.Fn.sub(
                    "arn:${AWS::Partition}:bedrock:${AWS::Region}:${AWS::AccountId}:"
                    "knowledge-base/${KnowledgeBaseId}",
                    {"KnowledgeBaseId": knowledge_base_id.value_as_string},
                )],
                conditions=region_condition,
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    cdk.Fn.sub(
                        "arn:${AWS::Partition}:bedrock:us-east-1:${AWS::AccountId}:"
                        "inference-profile/us.amazon.nova-pro-v1:0"
                    ),
                    cdk.Fn.sub(
                        "arn:${AWS::Partition}:bedrock:us-east-1::"
                        "foundation-model/amazon.nova-pro-v1:0"
                    ),
                    cdk.Fn.sub(
                        "arn:${AWS::Partition}:bedrock:us-east-2::"
                        "foundation-model/amazon.nova-pro-v1:0"
                    ),
                    cdk.Fn.sub(
                        "arn:${AWS::Partition}:bedrock:us-west-2::"
                        "foundation-model/amazon.nova-pro-v1:0"
                    ),
                ],
                conditions={
                    "StringEquals": {
                        "aws:RequestedRegion": ["us-east-1", "us-east-2", "us-west-2"]
                    }
                },
            )
        )
        # Sentence-streamed advisory generation (app/services/cloud_ai_streaming.py)
        # calls converse_stream, which Bedrock authorizes separately from the
        # synchronous converse/invoke call above. Same resources, same region
        # scope as InvokeModel -- streaming reaches only the models the
        # whole-answer path can already reach.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModelWithResponseStream"],
                resources=[
                    cdk.Fn.sub(
                        "arn:${AWS::Partition}:bedrock:us-east-1:${AWS::AccountId}:"
                        "inference-profile/us.amazon.nova-pro-v1:0"
                    ),
                    cdk.Fn.sub(
                        "arn:${AWS::Partition}:bedrock:us-east-1::"
                        "foundation-model/amazon.nova-pro-v1:0"
                    ),
                    cdk.Fn.sub(
                        "arn:${AWS::Partition}:bedrock:us-east-2::"
                        "foundation-model/amazon.nova-pro-v1:0"
                    ),
                    cdk.Fn.sub(
                        "arn:${AWS::Partition}:bedrock:us-west-2::"
                        "foundation-model/amazon.nova-pro-v1:0"
                    ),
                ],
                conditions={
                    "StringEquals": {
                        "aws:RequestedRegion": ["us-east-1", "us-east-2", "us-west-2"]
                    }
                },
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["polly:SynthesizeSpeech"],
                resources=["*"],
                conditions=region_condition,
            )
        )
        # Amazon Transcribe streaming defines no resource type for
        # StartStreamTranscription, so the region condition is the tightest
        # scope the service allows. Batch transcription stays ungranted.
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["transcribe:StartStreamTranscription"],
                resources=["*"],
                conditions=region_condition,
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "geo-maps:GetTile",
                    "geo-maps:GetStyleDescriptor",
                    "geo-places:Geocode",
                    "geo-places:ReverseGeocode",
                    "geo-places:SearchText",
                    "geo-places:SearchNearby",
                    "geo-places:Autocomplete",
                    "geo-places:GetPlace",
                    "geo-routes:CalculateRoutes",
                    "geo-routes:CalculateRouteMatrix",
                    "geo-routes:CalculateIsolines",
                    "geo-routes:OptimizeWaypoints",
                    "geo-routes:SnapToRoads",
                ],
                resources=["*"],
                conditions=region_condition,
            )
        )

    def _add_budget(self, parameters: dict[str, cdk.CfnParameter]) -> None:
        subscriber = budgets.CfnBudget.SubscriberProperty(
            address=parameters["budget_email"].value_as_string,
            subscription_type="EMAIL",
        )
        budgets.CfnBudget(
            self,
            "MonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=f"{NAME_PREFIX}-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(
                    amount=200,
                    unit="USD",
                ),
                cost_filters={"TagKeyValue": ["user:Project$LCDash-AWS"]},
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="FORECASTED",
                        threshold=80,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[subscriber],
                ),
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=100,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[subscriber],
                ),
            ],
        )
        budgets.CfnBudget(
            self,
            "AiMonthlyBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=f"{NAME_PREFIX}-ai-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=500, unit="USD"),
                cost_filters={"Service": ["Amazon Bedrock"]},
            ),
            notifications_with_subscribers=[
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="FORECASTED",
                        threshold=80,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[subscriber],
                ),
                budgets.CfnBudget.NotificationWithSubscribersProperty(
                    notification=budgets.CfnBudget.NotificationProperty(
                        comparison_operator="GREATER_THAN",
                        notification_type="ACTUAL",
                        threshold=100,
                        threshold_type="PERCENTAGE",
                    ),
                    subscribers=[subscriber],
                ),
            ],
        )

    def _add_optional_trail(self, create_parameter: cdk.CfnParameter) -> None:
        condition = cdk.CfnCondition(
            self,
            "CreatePilotTrailCondition",
            expression=cdk.Fn.condition_equals(create_parameter.value_as_string, "true"),
        )
        audit_bucket = s3.Bucket(
            self,
            "AuditBucket",
            bucket_name=cdk.Fn.sub(f"{NAME_PREFIX}-${{AWS::AccountId}}-audit"),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=False,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ExpireAuditEvidence",
                    expiration=cdk.Duration.days(90),
                )
            ],
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )
        trail = cloudtrail.Trail(
            self,
            "PilotTrail",
            trail_name=f"{NAME_PREFIX}-management",
            bucket=audit_bucket,
            is_multi_region_trail=False,
            include_global_service_events=False,
            enable_file_validation=True,
            send_to_cloud_watch_logs=False,
        )
        conditional_resources = {
            *audit_bucket.node.find_all(),
            *trail.node.find_all(),
        }
        for resource in conditional_resources:
            if isinstance(resource, cdk.CfnResource):
                resource.cfn_options.condition = condition
