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
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_logs as logs,
    aws_rds as rds,
    aws_s3 as s3,
)
from constructs import Construct

from .config import (
    ALB_SESSION_COOKIE_NAME,
    APPROVED_REGION,
    NAME_PREFIX,
    PILOT_DOMAIN_NAME,
    PILOT_MAIL_DOMAIN,
    PILOT_MAIL_FROM_ADDRESS,
)


# Wording for the two emails Cognito sends to users. Both are deliberately
# explicit about what the message is and why it arrived, because the defaults are
# indistinguishable from phishing: an unexpected email containing a password,
# from a domain that has never written to the recipient before. Staff at a 911
# centre are trained to distrust exactly that, and the right response to a real
# phishing attempt and to these defaults looked identical.
#
# Sent as HTML: with SES as the sending account Cognito delivers these as HTML,
# so plain newlines would collapse into one run-on paragraph.
#
# {username} and {####} are Cognito substitutions. The invitation REQUIRES both;
# the sign-in code message requires {####}. Removing either breaks deployment.
INVITE_EMAIL_SUBJECT = "Your new LCDash account (Logan County 911)"
INVITE_EMAIL_BODY = (
    "<p>Logan County 911 has created an LCDash account for you.</p>"
    "<p>LCDash is the Logan County 911 operations dashboard. An administrator set "
    "this account up for you, so this message may be unexpected: you did not sign "
    "up for it yourself.</p>"
    # The visible link text is the full URL rather than "click here" on purpose:
    # being able to see the destination before clicking is exactly how a recipient
    # tells this apart from a phishing message.
    "<p><strong>Sign in at:</strong> "
    '<a href="https://aws.logan911.com">https://aws.logan911.com</a></p>'
    "<p><strong>Username:</strong> {username}<br>"
    "<strong>Temporary password:</strong> {####}</p>"
    "<p>That temporary password expires in 24 hours. When you sign in you will "
    "choose your own password, and then enter a short code that we email to this "
    "same address. There is no app to install.</p>"
    "<p>If you were not expecting this, or you are not sure why you would have an "
    "LCDash account, do not use the password above. Please contact Logan County "
    "911 and tell them you received this message.</p>"
)

SIGN_IN_CODE_SUBJECT = "Your LCDash sign-in code"
SIGN_IN_CODE_BODY = (
    "<p>Your LCDash sign-in code is <strong>{####}</strong></p>"
    "<p>You are receiving this because a correct username and password were just "
    "entered for LCDash, the Logan County 911 operations dashboard. Entering this "
    "code is the second step that confirms it was you.</p>"
    "<p>The code expires in a few minutes and can only be used once.</p>"
    "<p>If you did not just try to sign in, someone else may have your password. "
    "Do not share this code with anyone. Please contact Logan County 911 so the "
    "account can be secured.</p>"
)


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
        pilot_image_uri = cdk.Token.as_string(
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
        container = task_definition.add_container(
            "Web",
            image=ecs.ContainerImage.from_registry(pilot_image_uri),
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
                "LCDASH_CLOUD_AI_UPLOADS_DATA_SOURCE_ID": parameters[
                    "cloud_ai_uploads_data_source_id"
                ].value_as_string,
                # Sign-out needs Cognito's hosted-UI origin: expiring the ALB
                # session cookie alone leaves Cognito's session intact, which
                # would sign the next person in without a password.
                "LCDASH_ALB_IDENTITY_HOSTED_UI_URL": cdk.Fn.join(
                    "",
                    [
                        "https://",
                        parameters["cognito_domain_prefix"].value_as_string,
                        f".auth.{APPROVED_REGION}.amazoncognito.com",
                    ],
                ),
                "LCDASH_ALB_IDENTITY_SIGNED_OUT_URL": f"https://{PILOT_DOMAIN_NAME}/",
                "LCDASH_ALB_IDENTITY_SESSION_COOKIE": ALB_SESSION_COOKIE_NAME,
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
            # cannot be cheaply reconstructed in cloud (only forward increments
            # are collected here), so the database is protected rather than
            # disposable:
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
            # Make-before-break: with desired_count=1, min 0% let every deploy
            # take the site down until the new task went healthy. 100/200 makes
            # the scheduler start the replacement first (approved 2026-08-09).
            min_healthy_percent=100,
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
            deletion_protection=True,
        )
        # Access logs + deletion protection were applied live 2026-08-09
        # (approved). The log bucket is managed OUT of this stack on purpose:
        # a teardown must not take the forensic record of the auth front door
        # with it. Attributes are mirrored here so the template and the live
        # ALB agree; the bucket policy grants the us-east-1 ELB log-delivery
        # account PutObject on alb/AWSLogs/<account>/* (see
        # docs/planning/AWS_HARDENING_2026-08-09.md).
        load_balancer.set_attribute("access_logs.s3.enabled", "true")
        load_balancer.set_attribute(
            "access_logs.s3.bucket",
            "lcdash-p1-logan-use1-862772137583-alb-logs",
        )
        load_balancer.set_attribute("access_logs.s3.prefix", "alb")
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
            # ESSENTIALS is required for email MFA (mfa_second_factor.email) and
            # is also the tier passkeys need later; LITE cannot enable either.
            feature_plan=cognito.FeaturePlan.ESSENTIALS,
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(
                sms=False,
                # No authenticator app: MFA is an emailed one-time code instead
                # of a software token, so nothing has to be installed.
                otp=False,
                email=True,
            ),
            # Cognito's own sender caps around 50 messages/day, which an MFA
            # code on every login exhausts quickly. SES is required beyond that
            # -- see LCDASH_COGNITO_SES_FROM_ADDRESS below.
            email=cognito.UserPoolEmail.with_ses(
                from_email=PILOT_MAIL_FROM_ADDRESS,
                # The SES identity must be the verified DOMAIN, not the single
                # from-address. These produce different SourceArn values
                # (identity/<domain> vs identity/<address>), and Cognito cannot
                # send if the ARN names an identity that is not verified.
                ses_verified_domain=PILOT_MAIL_DOMAIN,
            ),
            user_invitation=cognito.UserInvitationConfig(
                email_subject=INVITE_EMAIL_SUBJECT,
                email_body=INVITE_EMAIL_BODY,
            ),
            sign_in_aliases=cognito.SignInAliases(email=True),
            # Email MFA and email account recovery cannot share an address: a
            # user whose MFA is by email cannot also receive a password-reset
            # code by email. With a single administrator today, admin-driven
            # reset (no self-service recovery mechanism) is simpler than
            # collecting phone numbers for everyone to use as a second channel.
            account_recovery=cognito.AccountRecovery.NONE,
            password_policy=cognito.PasswordPolicy(
                # Shortened from 14: MFA is still required on every sign-in, so
                # the password is no longer the only thing standing between an
                # attacker and this account. Complexity requirements are kept.
                min_length=10,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
                temp_password_validity=cdk.Duration.days(1),
            ),
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        # The sign-in code message has no L2 property -- UserPool exposes
        # user_invitation and user_verification but nothing for email MFA -- so it
        # is set on the underlying resource. Deliberately not left at the Cognito
        # default, which says only "Your authentication code is 123456" with no
        # indication of what system sent it or what to do if you were not signing
        # in. Note there is intentionally NO link in this one: a message that
        # carries a code and also invites a click is the exact shape of a
        # credential-harvesting email.
        pool_resource = user_pool.node.default_child
        pool_resource.email_authentication_subject = SIGN_IN_CODE_SUBJECT
        pool_resource.email_authentication_message = SIGN_IN_CODE_BODY

        # Group names must match COGNITO_GROUP_ROLE_MAP in
        # app/core/cloud_pilot_roles.py exactly; a test asserts both directions,
        # because resolve_pilot_role denies any group it does not recognize.
        # The application's own ROLE_PRECEDENCE is what resolves a user's
        # effective role -- Cognito precedence only orders the preferred-role
        # claim for identity pools, and this pool has none and grants no
        # RoleArn, so these numbers imply no AWS authority.
        cognito.CfnUserPoolGroup(
            self,
            "PilotUserGroup",
            user_pool_id=user_pool.user_pool_id,
            group_name="lcdash-pilot-user",
            description=(
                "Read-only restricted access to the Logan synthetic pilot; the "
                "tier the sanitized build narrows. No operational outputs."
            ),
            precedence=20,
        )
        cognito.CfnUserPoolGroup(
            self,
            "PilotSupervisorGroup",
            user_pool_id=user_pool.user_pool_id,
            group_name="lcdash-pilot-supervisor",
            description=(
                "Read-only but unrestricted pilot access including review and "
                "evaluation; no tenant or operational authority."
            ),
            precedence=10,
        )
        cognito.CfnUserPoolGroup(
            self,
            "PilotAdminGroup",
            user_pool_id=user_pool.user_pool_id,
            group_name="lcdash-pilot-admin",
            description=(
                "Read-only pilot access administration; adds pilot access review "
                "only, and no CAD, tenant, output, AWS, or operational authority."
            ),
            precedence=0,
        )
        alb_callback_url = cdk.Fn.join(
            "",
            ["https://", PILOT_DOMAIN_NAME, "/oauth2/idpresponse"],
        )
        # Cognito redirects here after clearing its own session. This is the
        # authenticated application root on purpose: with the ALB session
        # cookie expired, the load balancer finds no session and sends the
        # browser to the login page, which is the sign-out confirmation. A
        # dedicated unauthenticated landing page would need a prohibited
        # ListenerRule (see the listener below).
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
        # Per-user identity settings are attached here rather than in the
        # container definition above, because the application must be told which
        # load balancer and user pool to trust and neither exists yet at that
        # point. Adding them afterwards avoids reordering the whole stack.
        # The load balancer ARN is the value the application compares against
        # the "signer" field of each forwarded assertion, so a token minted by
        # any other load balancer is rejected.
        for name, value in {
            "LCDASH_ALB_IDENTITY_ENABLED": parameters[
                "alb_identity_enabled"
            ].value_as_string,
            "LCDASH_ALB_IDENTITY_REGION": self.region,
            "LCDASH_ALB_IDENTITY_LOAD_BALANCER_ARN": load_balancer.load_balancer_arn,
            "LCDASH_ALB_IDENTITY_USER_POOL_ID": user_pool.user_pool_id,
            "LCDASH_ALB_IDENTITY_CLIENT_ID": user_pool_client.user_pool_client_id,
        }.items():
            container.add_environment(name, value)
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
            # 30s, not the 300s default: one target means every deploy waits
            # out the full drain, and this app holds no long-lived requests
            # worth five minutes of connection draining. Applied live
            # 2026-08-09 (approved); mirrored here so the template agrees.
            deregistration_delay=cdk.Duration.seconds(30),
        )
        # NOTE: AWS suggests a dedicated UNauthenticated logout landing page
        # ("client logout landing pages... cannot be behind an Application
        # Load Balancer rule that requires authentication"). That would need
        # an AWS::ElasticLoadBalancingV2::ListenerRule, which
        # phase1_deployment_allowlist.json PROHIBITS -- deliberately, so this
        # ALB keeps exactly one path and every path authenticates. The
        # guardrail wins: sign-out instead lands on the application root,
        # where the ALB finds no session and hands the browser to the login
        # page. The user sees the sign-in screen rather than a custom
        # confirmation page, which is unambiguous and adds no bypass.
        https_listener.add_action(
            "AuthenticateThenForward",
            action=elbv2_actions.AuthenticateCognitoAction(
                user_pool=user_pool,
                user_pool_client=user_pool_client,
                user_pool_domain=user_pool_domain,
                next=elbv2.ListenerAction.forward([target_group]),
                on_unauthenticated_request=elbv2.UnauthenticatedAction.AUTHENTICATE,
                scope="openid email profile",
                # Shared with the app as LCDASH_ALB_IDENTITY_SESSION_COOKIE so
                # sign-out expires the cookies that actually exist. The first
                # sign-out attempt shipped expiring "AWSELBAuthSessionCookie-N"
                # -- the AWS default, not this custom name -- so it deleted
                # nothing and the session survived. One constant, two uses.
                session_cookie_name=ALB_SESSION_COOKIE_NAME,
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

        self._add_analytics_collector(
            parameters=parameters,
            vpc=vpc,
            cluster=cluster,
            repository=repository,
            image_uri=pilot_image_uri,
            database=database,
        )

        self._grant_content_access(task_role, content_bucket)
        self._grant_document_library_read(task_role)
        self._grant_pilot_access_administration(task_role, user_pool)
        self._grant_knowledge_uploads(
            task_role,
            parameters["cloud_ai_knowledge_base_id"],
        )
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
            # Output name is legacy and kept for continuity with the deployment
            # runbooks. Authoritative DNS is Cloudflare; Hostinger is registrar
            # only, and editing DNS there is prohibited and would have no effect.
            "HostingerApplicationCnameTarget",
            value=load_balancer.load_balancer_dns_name,
            description=(
                "After foundation deployment, create a DNS-only CNAME for "
                "aws.logan911.com to this ALB hostname in authoritative Cloudflare "
                "DNS -- never in Hostinger, which is registrar only -- and validate "
                "it externally."
            ),
        )

    def _add_analytics_collector(
        self,
        *,
        parameters: dict[str, cdk.CfnParameter],
        vpc: ec2.Vpc,
        cluster: ecs.Cluster,
        repository: ecr.Repository,
        image_uri: str,
        database: rds.DatabaseInstance,
    ) -> None:
        """Scheduled incremental analytics collection.

        Reuses the web image and the same cloud CAD read path; the only new
        privilege is the CAD secret on its task role and the database secret on
        its execution role. It runs in the PUBLIC subnets with a public IP
        because this VPC has no NAT and no interface endpoints -- a task in the
        isolated subnets cannot reach ECR, Secrets Manager, or CloudWatch Logs
        and would never start.
        """
        if database.secret is None:
            raise ValueError("Generated database secret was not created.")

        log_group = logs.LogGroup(
            self,
            "AnalyticsCollectorLogs",
            log_group_name=f"/lcdash/{NAME_PREFIX}/analytics-collector",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        collector_task_role = iam.Role(
            self,
            "AnalyticsCollectorTaskRole",
            role_name=f"{NAME_PREFIX}-analytics-collector",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description=(
                "Scheduled analytics collector: reviewed CentralSquare read-only "
                "secret and nothing else"
            ),
        )
        collector_task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[parameters["cloud_cad_secret_arn"].value_as_string],
            )
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "AnalyticsCollectorTaskDefinition",
            family=f"{NAME_PREFIX}-analytics-collector",
            cpu=512,
            memory_limit_mib=1024,
            task_role=collector_task_role,
        )
        task_definition.add_volume(name="CollectorTemp")
        repository.grant_pull(task_definition.obtain_execution_role())
        container = task_definition.add_container(
            "AnalyticsCollector",
            image=ecs.ContainerImage.from_registry(image_uri),
            command=["python", "-m", "app.tools.cloud_analytics_collector"],
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="analytics-collector",
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
                "LCDASH_DATABASE_HOST": database.db_instance_endpoint_address,
                "LCDASH_DATABASE_PORT": database.db_instance_endpoint_port,
                "LCDASH_DATABASE_NAME": "lcdash",
                "TMPDIR": "/tmp",
                "HOME": "/tmp/home",
                "XDG_CACHE_HOME": "/tmp/cache",
            },
            secrets={
                "LCDASH_DATABASE_USERNAME": ecs.Secret.from_secrets_manager(
                    database.secret, "username"
                ),
                "LCDASH_DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(
                    database.secret, "password"
                ),
            },
        )
        container.add_mount_points(
            ecs.MountPoint(
                source_volume="CollectorTemp",
                container_path="/tmp",
                read_only=False,
            )
        )

        collector_security_group = ec2.SecurityGroup(
            self,
            "AnalyticsCollectorSecurityGroup",
            vpc=vpc,
            security_group_name=f"{NAME_PREFIX}-analytics-collector",
            description=(
                "Scheduled analytics collector: DNS, HTTPS, and pilot PostgreSQL only"
            ),
            allow_all_outbound=False,
        )
        collector_security_group.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "AWS APIs and CentralSquare over HTTPS",
        )
        resolver = ec2.Peer.ipv4("10.42.0.2/32")
        collector_security_group.add_egress_rule(resolver, ec2.Port.udp(53))
        collector_security_group.add_egress_rule(resolver, ec2.Port.tcp(53))
        # Adds the matching 5432 ingress on the RDS security group and the
        # 5432 egress on the collector group. The port is stated literally
        # rather than taken from the endpoint attribute so the rule is
        # reviewable as PostgreSQL-only in the rendered template.
        database.connections.allow_from(
            collector_security_group,
            ec2.Port.tcp(5432),
            "Scheduled analytics collector PostgreSQL access",
        )

        enabled_condition = cdk.CfnCondition(
            self,
            "AnalyticsCollectorEnabledCondition",
            expression=cdk.Fn.condition_equals(
                parameters["analytics_collector_enabled"].value_as_string,
                "true",
            ),
        )
        rule = events.Rule(
            self,
            "AnalyticsCollectorSchedule",
            rule_name=f"{NAME_PREFIX}-analytics-collector",
            description=(
                "Periodic incremental analytics collection from CentralSquare; "
                "dormant unless AnalyticsCollectorEnabled is true."
            ),
            # The L2 construct takes a literal boolean, so it cannot read the
            # parameter. Ship it off and let the CfnCondition below override
            # State, so the rendered template is DISABLED by default and only
            # ENABLED when the operator deliberately sets the parameter.
            enabled=False,
            schedule=events.Schedule.rate(
                cdk.Duration.minutes(
                    parameters["analytics_collector_schedule_minutes"].value_as_number
                )
            ),
            targets=[
                targets.EcsTask(
                    cluster=cluster,
                    task_definition=task_definition,
                    task_count=1,
                    subnet_selection=ec2.SubnetSelection(
                        subnet_type=ec2.SubnetType.PUBLIC
                    ),
                    assign_public_ip=True,
                    security_groups=[collector_security_group],
                )
            ],
        )
        cfn_rule = rule.node.default_child
        if not isinstance(cfn_rule, events.CfnRule):
            raise TypeError("Expected an AWS::Events::Rule as the rule's child.")
        cfn_rule.add_property_override(
            "State",
            cdk.Fn.condition_if(
                enabled_condition.logical_id, "ENABLED", "DISABLED"
            ),
        )

        cdk.CfnOutput(
            self,
            "AnalyticsCollectorTaskDefinitionArn",
            value=task_definition.task_definition_arn,
        )
        cdk.CfnOutput(
            self,
            "AnalyticsCollectorLogGroupName",
            value=log_group.log_group_name,
        )
        cdk.CfnOutput(
            self,
            "AnalyticsCollectorSecurityGroupId",
            value=collector_security_group.security_group_id,
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
            "cloud_ai_uploads_data_source_id": cdk.CfnParameter(
                self,
                "CloudAiUploadsDataSourceId",
                type="String",
                # Blank means admin uploads are not provisioned. The data
                # sources are created out-of-band by
                # scripts/provision_cloud_uploads_data_source.py, matching how
                # the knowledge base itself was provisioned. Comma-separated
                # because this KB caps inclusionPrefixes at one per data
                # source, so the two upload destinations are two sources.
                allowed_pattern="^([A-Z0-9]{10}(,[A-Z0-9]{10})*)?$",
                default="",
                description=(
                    "Comma-separated Bedrock data source IDs indexing the "
                    "admin cloud-uploads prefixes; blank until provisioned."
                ),
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
            "alb_identity_enabled": cdk.CfnParameter(
                self,
                "AlbIdentityEnabled",
                type="String",
                allowed_values=["true", "false"],
                default="false",
                description=(
                    "Set to true only in a separately reviewed update to derive "
                    "per-user identity and role from the ALB's verified OIDC "
                    "headers. While false, every request keeps the previous "
                    "deployment-wide viewer identity."
                ),
            ),
            "analytics_collector_enabled": cdk.CfnParameter(
                self,
                "AnalyticsCollectorEnabled",
                type="String",
                allowed_values=["true", "false"],
                default="false",
                description=(
                    "Set to true only in a separately reviewed update to start the "
                    "scheduled analytics collection runs."
                ),
            ),
            "analytics_collector_schedule_minutes": cdk.CfnParameter(
                self,
                "AnalyticsCollectorScheduleMinutes",
                type="Number",
                default=30,
                min_value=5,
                max_value=1440,
                description=(
                    "Interval in minutes between analytics collection runs when "
                    "AnalyticsCollectorEnabled is true."
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

    def _grant_pilot_access_administration(
        self, role: iam.Role, user_pool: cognito.UserPool
    ) -> None:
        """The user-administration page's exact verbs, on this pool only.

        The task role had NO Cognito permissions before this (the app only
        verified tokens, which needs none). This grant is the admin page's
        capability set and nothing more: list/inspect users, create an
        invited user, move group membership, disable and enable. Deliberately
        absent: AdminDeleteUser (accounts are disabled and retained for
        audit, matching scripts/sync_cognito_users.py), AdminSetUserPassword
        (the app must never know or set a password), and every pool-level
        mutation (UpdateUserPool and friends change authentication policy and
        belong to the deploy pipeline, not to a page).
        """
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "cognito-idp:ListUsers",
                    "cognito-idp:ListUsersInGroup",
                    "cognito-idp:AdminListGroupsForUser",
                    "cognito-idp:AdminGetUser",
                    "cognito-idp:AdminCreateUser",
                    "cognito-idp:AdminAddUserToGroup",
                    "cognito-idp:AdminRemoveUserFromGroup",
                    "cognito-idp:AdminDisableUser",
                    "cognito-idp:AdminEnableUser",
                ],
                resources=[user_pool.user_pool_arn],
            )
        )

    def _grant_knowledge_uploads(
        self, role: iam.Role, knowledge_base_id: cdk.CfnParameter
    ) -> None:
        """Admin document uploads: write access to the two cloud-uploads
        prefixes and the right to run ingestion syncs -- nothing else.

        This is deliberately disjoint from ``_grant_document_library_read``:
        the approved on-prem sets stay read-only forever (that grant's
        docstring still holds), while uploads live under their own prefixes
        whose names encode their provenance. ``mae-uploads`` is retrievable
        by MAE only; ``jack-uploads/mindshare`` contains ``/mindshare/``
        because the persona filter confines JACK to such paths, and its first
        path segment is distinct so the document-library key parser cannot
        collide it with the approved mindshare set. DeleteObject is granted
        because admins remove upload mistakes; the approved prefixes remain
        untouchable -- this statement's resources do not include them.
        """
        uploads_bucket = s3.Bucket.from_bucket_name(
            self,
            "KnowledgeUploadsBucket",
            bucket_name=cdk.Fn.sub(
                f"{NAME_PREFIX}-${{AWS::AccountId}}-document-library"
            ),
        )
        upload_prefixes = [
            "tenants/logan-synthetic/document-library/mae-uploads/current/*",
            "tenants/logan-synthetic/document-library/jack-uploads/mindshare/current/*",
        ]
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[uploads_bucket.bucket_arn],
                conditions={"StringLike": {"s3:prefix": upload_prefixes}},
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                resources=[
                    uploads_bucket.arn_for_objects(prefix)
                    for prefix in upload_prefixes
                ],
            )
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                    "bedrock:ListIngestionJobs",
                ],
                resources=[
                    cdk.Fn.sub(
                        "arn:aws:bedrock:us-east-1:${AWS::AccountId}:"
                        "knowledge-base/${KnowledgeBaseId}",
                        {"KnowledgeBaseId": knowledge_base_id.value_as_string},
                    )
                ],
                conditions={
                    "StringEquals": {"aws:RequestedRegion": APPROVED_REGION}
                },
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
