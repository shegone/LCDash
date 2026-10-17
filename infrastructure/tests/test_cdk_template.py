from pathlib import Path
import sys
import unittest


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
# Repo root first so the application's `app` namespace package resolves even
# when the suite runs from infrastructure/ (the CDK entry script is named
# cdk_app.py, not app.py, so it cannot shadow that package).
sys.path.insert(0, str(INFRASTRUCTURE_ROOT))
sys.path.insert(0, str(INFRASTRUCTURE_ROOT.parent))

try:
    import aws_cdk as cdk
    from aws_cdk.assertions import Match, Template
    from lcdash_pilot.foundation_stack import Phase1FoundationStack
except ImportError:
    cdk = None


@unittest.skipUnless(cdk is not None, "aws-cdk-lib is not installed")
class CdkTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        stack = Phase1FoundationStack(
            app,
            "TestFoundation",
            env=cdk.Environment(account="111111111111", region="us-east-1"),
        )
        cls.template = Template.from_stack(stack)

    def test_exact_service_and_database_counts(self):
        self.template.resource_count_is("AWS::ECS::Service", 1)
        self.template.resource_count_is("AWS::RDS::DBInstance", 1)
        self.template.resource_count_is("AWS::ECR::Repository", 1)
        self.template.resource_count_is("AWS::ElasticLoadBalancingV2::LoadBalancer", 1)
        self.template.resource_count_is("AWS::Cognito::UserPool", 1)
        self.template.resource_count_is("AWS::Lambda::Function", 1)
        self.template.resource_count_is("Custom::S3AutoDeleteObjects", 2)
        self.template.resource_count_is("AWS::Route53::RecordSet", 0)

    def test_identity_rejections_page_the_operator(self):
        # 2026-09-11: every request failed ALB identity verification for three
        # days and nobody knew until a user reported "restricted". The log line
        # the verifier writes on every failure must drive an alarm that emails
        # the operator address the stack already holds.
        self.template.resource_count_is("AWS::SNS::Topic", 1)
        # One subscription, email only: the sns_alerts_exception in
        # phase1_deployment_allowlist.json is scoped to exactly this.
        self.template.resource_count_is("AWS::SNS::Subscription", 1)
        self.template.has_resource_properties(
            "AWS::SNS::Subscription",
            {"Protocol": "email", "Endpoint": {"Ref": "BudgetSubscriberEmail"}},
        )
        self.template.has_resource_properties(
            "AWS::Logs::MetricFilter",
            {
                "LogGroupName": {"Ref": Match.string_like_regexp("ApplicationLogs.*")},
                "FilterPattern": Match.string_like_regexp(".*Rejected ALB identity headers.*"),
                "MetricTransformations": [
                    Match.object_like(
                        {
                            "MetricNamespace": "LCDash/Pilot",
                            "MetricName": "AlbIdentityRejections",
                            "MetricValue": "1",
                        }
                    )
                ],
            },
        )
        self.template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "AlarmName": "lcdash-p1-logan-use1-alb-identity-rejections",
                "Namespace": "LCDash/Pilot",
                "MetricName": "AlbIdentityRejections",
                "Statistic": "Sum",
                "Period": 300,
                "EvaluationPeriods": 1,
                "Threshold": 3,
                "ComparisonOperator": "GreaterThanOrEqualToThreshold",
                "TreatMissingData": "notBreaching",
                "AlarmActions": [{"Ref": Match.string_like_regexp("OperationsAlerts.*")}],
                "OKActions": [{"Ref": Match.string_like_regexp("OperationsAlerts.*")}],
            },
        )

    def test_silent_radio_listener_pages_the_operator(self):
        # 2026-10-17: the Mindshare radio listener logs "radio-heartbeat ok"
        # for each accepted heartbeat. Fewer than one in each of two 5-minute
        # periods (missing data included) must email the operator through the
        # same alerts topic the identity alarm uses.
        self.template.resource_count_is("AWS::Logs::MetricFilter", 2)
        self.template.resource_count_is("AWS::CloudWatch::Alarm", 2)
        # Still one topic and one email subscription (sns_alerts_exception).
        self.template.resource_count_is("AWS::SNS::Topic", 1)
        self.template.resource_count_is("AWS::SNS::Subscription", 1)

        filters = [
            resource["Properties"]
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::Logs::MetricFilter"
            and resource["Properties"]["MetricTransformations"][0]["MetricName"]
            == "HeartbeatCount"
        ]
        self.assertEqual(len(filters), 1)
        heartbeat_filter = filters[0]
        self.assertEqual(heartbeat_filter["FilterPattern"], '"radio-heartbeat ok"')
        self.assertEqual(
            heartbeat_filter["MetricTransformations"],
            [
                {
                    "MetricNamespace": "LCDash/Radio",
                    "MetricName": "HeartbeatCount",
                    "MetricValue": "1",
                }
            ],
            "No DefaultValue: a silent listener must yield missing data, not zeros",
        )
        self.template.has_resource_properties(
            "AWS::Logs::MetricFilter",
            {
                "LogGroupName": {"Ref": Match.string_like_regexp("ApplicationLogs.*")},
                "FilterPattern": '"radio-heartbeat ok"',
            },
        )

        self.template.has_resource_properties(
            "AWS::CloudWatch::Alarm",
            {
                "AlarmName": "lcdash-p1-logan-use1-radio-listener-silent",
                "AlarmDescription": "Radio listener has not reported for 10 minutes",
                "Namespace": "LCDash/Radio",
                "MetricName": "HeartbeatCount",
                "Statistic": "Sum",
                "Period": 300,
                "EvaluationPeriods": 2,
                "Threshold": 1,
                "ComparisonOperator": "LessThanThreshold",
                "TreatMissingData": "breaching",
                "AlarmActions": [{"Ref": Match.string_like_regexp("OperationsAlerts.*")}],
                "OKActions": [{"Ref": Match.string_like_regexp("OperationsAlerts.*")}],
            },
        )
        # Both alarms must share the one alerts topic.
        topics = {
            tuple(
                action["Ref"]
                for action in resource["Properties"]["AlarmActions"]
            )
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::CloudWatch::Alarm"
        }
        self.assertEqual(len(topics), 1)
        # No em dashes anywhere in the alarm text.
        for resource in self.template.to_json()["Resources"].values():
            if resource["Type"] == "AWS::CloudWatch::Alarm":
                for field in ("AlarmName", "AlarmDescription"):
                    self.assertNotIn("—", resource["Properties"][field])

    def test_database_uses_verified_postgresql_engine_version(self):
        self.template.has_resource_properties(
            "AWS::RDS::DBInstance",
            {
                "Engine": "postgres",
                "EngineVersion": "17.10",
            },
        )

    def test_hostinger_cname_target_is_an_output_not_an_aws_dns_record(self):
        outputs = self.template.to_json()["Outputs"]
        self.assertIn("HostingerApplicationCnameTarget", outputs)
        self.assertEqual(
            outputs["ApplicationUrl"]["Value"],
            "https://aws.logan911.com",
        )

    def test_no_nat_and_service_is_inactive_by_default(self):
        self.template.resource_count_is("AWS::EC2::NatGateway", 0)
        template = self.template.to_json()
        parameter = template["Parameters"]["PilotServiceDesiredCount"]
        self.assertEqual(parameter["Type"], "Number")
        self.assertEqual(parameter["Default"], 0)
        self.assertEqual(parameter["AllowedValues"], ["0", "1"])
        self.template.has_resource_properties(
            "AWS::ECS::Service",
            {
                "DesiredCount": {"Ref": "PilotServiceDesiredCount"},
                "DeploymentConfiguration": Match.object_like(
                    {"DeploymentCircuitBreaker": {"Enable": True, "Rollback": True}}
                ),
            },
        )

    def test_activation_parameter_cannot_request_more_than_one_task(self):
        parameter = self.template.to_json()["Parameters"]["PilotServiceDesiredCount"]
        self.assertNotIn(2, parameter["AllowedValues"])
        self.assertNotIn("2", parameter["AllowedValues"])

    def test_dormant_service_uses_a_valid_inactive_image_reference(self):
        template = self.template.to_json()
        digest = template["Parameters"]["PilotImageDigest"]
        self.assertEqual(digest["Default"], "NOT_PUBLISHED")
        self.assertEqual(
            digest["AllowedPattern"],
            "^(NOT_PUBLISHED|sha256:[a-f0-9]{64})$",
        )
        task = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
        )
        image = task["Properties"]["ContainerDefinitions"][0]["Image"]
        self.assertEqual(image["Fn::If"][0], "PilotImagePublishedCondition")
        dormant_parts = image["Fn::If"][2]["Fn::Join"][1]
        self.assertEqual(dormant_parts[-1], ":dormant-not-published")
        self.assertNotIn({"Ref": "PilotImageDigest"}, dormant_parts)

    def test_active_service_uses_only_an_immutable_digest_reference(self):
        template = self.template.to_json()
        task = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
        )
        image = task["Properties"]["ContainerDefinitions"][0]["Image"]
        active_parts = image["Fn::If"][1]["Fn::Join"][1]
        self.assertEqual(active_parts[-2], "@")
        self.assertEqual(active_parts[-1], {"Ref": "PilotImageDigest"})
        self.assertNotIn(":dormant-not-published", active_parts)

    def test_default_digest_keeps_the_image_dormant_at_zero_tasks(self):
        template = self.template.to_json()
        self.assertEqual(
            template["Parameters"]["PilotServiceDesiredCount"]["Default"], 0
        )
        self.assertEqual(
            template["Parameters"]["PilotImageDigest"]["Default"],
            "NOT_PUBLISHED",
        )
        self.assertEqual(
            template["Conditions"]["PilotImagePublishedCondition"],
            {
                "Fn::Not": [
                    {"Fn::Equals": [{"Ref": "PilotImageDigest"}, "NOT_PUBLISHED"]}
                ]
            },
        )

    def test_published_digest_selects_the_immutable_image_with_zero_tasks(self):
        template = self.template.to_json()
        service = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::Service"
        )
        task = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
        )
        image = task["Properties"]["ContainerDefinitions"][0]["Image"]
        self.assertEqual(
            service["Properties"]["DesiredCount"],
            {"Ref": "PilotServiceDesiredCount"},
        )
        self.assertEqual(image["Fn::If"][0], "PilotImagePublishedCondition")
        self.assertEqual(
            image["Fn::If"][1]["Fn::Join"][1][-1],
            {"Ref": "PilotImageDigest"},
        )

    def test_service_deploys_make_before_break(self):
        # min 100 / max 200 with desired_count=1: the scheduler must start the
        # replacement task and see it healthy before stopping the old one.
        # min was 0 until 2026-08-09 (deploys could dip to zero healthy
        # targets); Ted approved the change alongside the no-healthy-target
        # alarm, which would otherwise fire on every deploy.
        template = self.template.to_json()
        service = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::Service"
        )
        deployment = service["Properties"]["DeploymentConfiguration"]
        self.assertEqual(deployment["MinimumHealthyPercent"], 100)
        self.assertEqual(deployment["MaximumPercent"], 200)
        self.assertTrue(deployment["DeploymentCircuitBreaker"]["Enable"])
        self.assertTrue(deployment["DeploymentCircuitBreaker"]["Rollback"])

    def test_published_digest_with_one_task_preserves_the_activation_rule(self):
        template = self.template.to_json()
        self.assertEqual(
            template["Conditions"]["PilotServiceActivatedCondition"],
            {"Fn::Equals": [{"Ref": "PilotServiceDesiredCount"}, "1"]},
        )
        rule = template["Rules"]["PilotImageRequiredForActivation"]
        self.assertEqual(
            rule["Assertions"][0]["Assert"]["Fn::Or"][1],
            {
                "Fn::Not": [
                    {"Fn::Equals": [{"Ref": "PilotImageDigest"}, "NOT_PUBLISHED"]}
                ]
            },
        )

    def test_one_task_requires_an_immutable_sha256_digest(self):
        template = self.template.to_json()
        self.assertEqual(
            template["Conditions"]["PilotServiceActivatedCondition"],
            {"Fn::Equals": [{"Ref": "PilotServiceDesiredCount"}, "1"]},
        )
        rule = template["Rules"]["PilotImageRequiredForActivation"]
        expression = rule["Assertions"][0]["Assert"]["Fn::Or"]
        self.assertEqual(
            expression[0],
            {"Fn::Equals": [{"Ref": "PilotServiceDesiredCount"}, "0"]},
        )
        self.assertEqual(
            expression[1],
            {
                "Fn::Not": [
                    {"Fn::Equals": [{"Ref": "PilotImageDigest"}, "NOT_PUBLISHED"]}
                ]
            },
        )
        pattern = template["Parameters"]["PilotImageDigest"]["AllowedPattern"]
        self.assertRegex("sha256:" + "a" * 64, pattern)
        self.assertNotRegex("pilot", pattern)
        self.assertNotRegex("sha256:" + "A" * 64, pattern)

    def test_fargate_container_matches_immutable_pilot_image_contract(self):
        self.template.has_resource_properties(
            "AWS::ECS::TaskDefinition",
            {
                "Volumes": [{"Name": "RuntimeTemp"}],
                "ContainerDefinitions": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Name": "Web",
                                "ReadonlyRootFilesystem": True,
                                "User": "10001:10001",
                                "PortMappings": Match.array_with(
                                    [Match.object_like({"ContainerPort": 8000})]
                                ),
                                "MountPoints": [
                                    {
                                        "ContainerPath": "/tmp",
                                        "ReadOnly": False,
                                        "SourceVolume": "RuntimeTemp",
                                    }
                                ],
                                "HealthCheck": Match.object_like(
                                    {
                                        "Command": Match.array_with(
                                            [Match.string_like_regexp("/health")]
                                        )
                                    }
                                ),
                            }
                        )
                    ]
                ),
            },
        )
        resources = self.template.to_json()["Resources"]
        task = next(
            resource
            for resource in resources.values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
        )
        web = next(
            container
            for container in task["Properties"]["ContainerDefinitions"]
            if container["Name"] == "Web"
        )
        environment = {
            item["Name"]: item["Value"] for item in web["Environment"]
        }
        self.assertEqual(environment["TMPDIR"], "/tmp")
        self.assertEqual(environment["HOME"], "/tmp/home")
        self.assertEqual(environment["XDG_CACHE_HOME"], "/tmp/cache")
        self.assertEqual(environment["EMS_DELAY_ALERT_ENABLED"], "false")
        self.assertEqual(environment["EMS_DELAY_ALERT_MODE"], "disabled")

    def test_database_secret_is_injected_by_execution_role_only(self):
        resources = self.template.to_json()["Resources"]
        task = next(
            resource
            for resource in resources.values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
        )
        web = next(
            container
            for container in task["Properties"]["ContainerDefinitions"]
            if container["Name"] == "Web"
        )
        environment = {
            item["Name"]: item["Value"] for item in web["Environment"]
        }
        self.assertEqual(environment["LCDASH_DATABASE_NAME"], "lcdash")
        self.assertIn("Fn::GetAtt", environment["LCDASH_DATABASE_HOST"])
        self.assertIn("Fn::GetAtt", environment["LCDASH_DATABASE_PORT"])

        secrets = {item["Name"]: item["ValueFrom"] for item in web["Secrets"]}
        self.assertEqual(
            set(secrets),
            {"LCDASH_DATABASE_USERNAME", "LCDASH_DATABASE_PASSWORD"},
        )
        self.assertEqual(secrets["LCDASH_DATABASE_USERNAME"]["Fn::Join"][1][-1], ":username::")
        self.assertEqual(secrets["LCDASH_DATABASE_PASSWORD"]["Fn::Join"][1][-1], ":password::")

        secret_policies = []
        for resource in resources.values():
            if resource["Type"] != "AWS::IAM::Policy":
                continue
            statements = resource["Properties"]["PolicyDocument"]["Statement"]
            if any(
                "secretsmanager:GetSecretValue"
                in ([statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"])
                and statement["Resource"] == {"Ref": "DatabaseSecretAttachmentE5D1B020"}
                for statement in statements
            ):
                secret_policies.append(resource)
        # One for the web execution role, one for the scheduled collector's.
        self.assertEqual(len(secret_policies), 2)
        for policy in secret_policies:
            attached_role = policy["Properties"]["Roles"][0]["Ref"]
            self.assertIn("ExecutionRole", attached_role)
            self.assertNotIn("ApplicationTaskRole", attached_role)
            self.assertNotIn("AnalyticsCollectorTaskRole", attached_role)

    def test_alb_identity_is_wired_to_this_deployments_own_alb_and_pool(self):
        """The app must be told which ALB and pool to trust, and ship dormant.

        The load balancer ARN is what the application compares against the
        ``signer`` field of each forwarded assertion, so if it were ever wired
        to a literal or to another stack's balancer, forged identity headers
        would verify. It must be a reference to this stack's own resources.
        """
        template = self.template.to_json()
        task = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
        )
        web = task["Properties"]["ContainerDefinitions"][0]
        environment = {item["Name"]: item["Value"] for item in web["Environment"]}

        # Dormant by default: enabling per-user identity is a reviewed change.
        self.assertEqual(
            environment["LCDASH_ALB_IDENTITY_ENABLED"], {"Ref": "AlbIdentityEnabled"}
        )
        self.assertEqual(
            template["Parameters"]["AlbIdentityEnabled"]["Default"], "false"
        )

        # Each trust anchor resolves to a resource in this stack, not a literal.
        for key in (
            "LCDASH_ALB_IDENTITY_LOAD_BALANCER_ARN",
            "LCDASH_ALB_IDENTITY_USER_POOL_ID",
            "LCDASH_ALB_IDENTITY_CLIENT_ID",
        ):
            value = environment[key]
            self.assertIsInstance(value, dict, f"{key} must be a resource reference")
            self.assertTrue(
                {"Ref", "Fn::GetAtt"} & value.keys(),
                f"{key} must reference a stack resource, got {value!r}",
            )

        alb_logical_ids = [
            name
            for name, resource in template["Resources"].items()
            if resource["Type"] == "AWS::ElasticLoadBalancingV2::LoadBalancer"
        ]
        self.assertEqual(len(alb_logical_ids), 1)
        self.assertEqual(
            environment["LCDASH_ALB_IDENTITY_LOAD_BALANCER_ARN"],
            {"Ref": alb_logical_ids[0]},
        )

        pool_logical_ids = [
            name
            for name, resource in template["Resources"].items()
            if resource["Type"] == "AWS::Cognito::UserPool"
        ]
        self.assertEqual(len(pool_logical_ids), 1)
        self.assertEqual(
            environment["LCDASH_ALB_IDENTITY_USER_POOL_ID"],
            {"Ref": pool_logical_ids[0]},
        )

    def test_cloud_cad_read_poll_reference_is_enabled_and_exactly_scoped(self):
        template = self.template.to_json()
        task = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
        )
        web = task["Properties"]["ContainerDefinitions"][0]
        environment = {item["Name"]: item["Value"] for item in web["Environment"]}
        self.assertEqual(environment["LCDASH_CLOUD_CAD_ENABLED"], "true")
        self.assertEqual(environment["LCDASH_CLOUD_CAD_MODE"], "centralsquare-read-poll")
        self.assertEqual(
            environment["LCDASH_CLOUD_CAD_SECRET_ARN"],
            {"Ref": "CloudCadReadSecretArn"},
        )
        matches = []
        for resource in template["Resources"].values():
            if resource["Type"] != "AWS::IAM::Policy":
                continue
            for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
                actions = statement["Action"]
                actions = actions if isinstance(actions, list) else [actions]
                if any(action.startswith("secretsmanager:") for action in actions):
                    matches.append(statement)
        # Two database-secret grants (web + collector execution roles) and two
        # CAD-secret grants (web + collector task roles); nothing else.
        self.assertEqual(len(matches), 4)
        cad_statements = [
            statement
            for statement in matches
            if statement["Resource"] == {"Ref": "CloudCadReadSecretArn"}
        ]
        self.assertEqual(len(cad_statements), 2)
        for statement in cad_statements:
            self.assertEqual(statement["Action"], "secretsmanager:GetSecretValue")

    def test_container_insights_is_explicitly_disabled(self):
        self.template.has_resource_properties(
            "AWS::ECS::Cluster",
            {
                "ClusterSettings": [
                    {"Name": "containerInsights", "Value": "disabled"}
                ]
            },
        )

    def test_https_listener_authenticates_before_forwarding(self):
        self.template.has_resource_properties(
            "AWS::ElasticLoadBalancingV2::Listener",
            {
                "Port": 443,
                "DefaultActions": Match.array_with(
                    [
                        Match.object_like({"Type": "authenticate-cognito", "Order": 1}),
                        Match.object_like({"Type": "forward", "Order": 2}),
                    ]
                ),
            },
        )
        self.template.has_resource_properties(
            "AWS::Cognito::UserPoolClient",
            {
                "GenerateSecret": True,
                "AllowedOAuthFlows": ["code"],
                "EnableTokenRevocation": True,
                "AccessTokenValidity": 15,
                "IdTokenValidity": 15,
                "RefreshTokenValidity": 1440,
                "AuthSessionValidity": 3,
                "RefreshTokenRotation": {
                    "Feature": "ENABLED",
                    "RetryGracePeriodSeconds": 0,
                },
            },
        )

    def test_cognito_pool_requires_mfa_by_emailed_code_and_no_authenticator_app(self):
        """MFA stays mandatory, but the second factor needs no installed app.

        SOFTWARE_TOKEN_MFA is deliberately absent: requiring an authenticator
        app was the main friction for this user base. EMAIL_OTP keeps two
        factors while needing nothing installed.

        Account recovery is admin_only rather than verified_email because a user
        whose MFA arrives by email cannot also receive a password-reset code at
        that same address -- Cognito would leave them with no valid recovery
        method. With one administrator, admin-driven reset beats collecting a
        phone number for everyone purely as a second channel.
        """
        self.template.has_resource_properties(
            "AWS::Cognito::UserPool",
            {
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
                "MfaConfiguration": "ON",
                "EnabledMfas": ["EMAIL_OTP"],
                "AccountRecoverySetting": {
                    "RecoveryMechanisms": [{"Name": "admin_only", "Priority": 1}]
                },
                "Policies": {
                    "PasswordPolicy": {
                        # Shorter than the previous 14 because MFA is still
                        # required on every sign-in; complexity is unchanged.
                        "MinimumLength": 10,
                        "RequireLowercase": True,
                        "RequireUppercase": True,
                        "RequireNumbers": True,
                        "RequireSymbols": True,
                        "TemporaryPasswordValidityDays": 1,
                    }
                },
            },
        )
        # EMAIL_OTP is only permitted when the pool sends through SES with
        # EmailSendingAccount=DEVELOPER; Cognito's built-in sender cannot serve
        # a code on every login.
        self.template.has_resource_properties(
            "AWS::Cognito::UserPool",
            {
                "UserPoolTier": "ESSENTIALS",
                "EmailConfiguration": {
                    "EmailSendingAccount": "DEVELOPER",
                    "From": "no-reply@logan911.com",
                },
            },
        )

    def test_cognito_ses_source_arn_names_the_verified_domain_not_the_address(self):
        """SourceArn must name the SES identity that is actually verified.

        identity/<domain> and identity/<address> are different identities. If
        this named the address while only the domain was verified, Cognito would
        accept the configuration and then silently fail to deliver MFA codes,
        locking every user out with no error at deploy time.
        """
        pool = next(
            resource
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::Cognito::UserPool"
        )
        source_arn = pool["Properties"]["EmailConfiguration"]["SourceArn"]
        joined = "".join(
            part for part in source_arn["Fn::Join"][1] if isinstance(part, str)
        )
        self.assertTrue(
            joined.endswith(":identity/logan911.com"),
            f"SourceArn must name the verified domain identity, got {joined!r}",
        )
        self.assertNotIn("identity/no-reply@", joined)

    def test_user_emails_explain_themselves_and_carry_required_substitutions(self):
        """Both user-facing emails must say what they are and who sent them.

        The Cognito defaults read as phishing: an unexpected message containing a
        password, or a bare "Your authentication code is 123456" naming no system
        at all. Staff at a 911 centre are trained to distrust exactly that, and a
        recipient could not distinguish the defaults from an attack.

        The substitution assertions are not cosmetic -- Cognito rejects an
        invitation template missing {username} or {####}, and a sign-in code
        message missing {####}, so losing one breaks deployment rather than
        quietly sending a broken email.
        """
        pool = next(
            resource
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::Cognito::UserPool"
        )
        properties = pool["Properties"]

        invite = properties["AdminCreateUserConfig"]["InviteMessageTemplate"]
        self.assertIn("LCDash", invite["EmailSubject"])
        self.assertIn("{username}", invite["EmailMessage"])
        self.assertIn("{####}", invite["EmailMessage"])
        # Identifies the sender, explains why it arrived unexpectedly, and says
        # what to do if the recipient was not expecting it.
        for expected in ("Logan County 911", "administrator", "not expecting"):
            with self.subTest(invite=expected):
                self.assertIn(expected, invite["EmailMessage"])
        # A real, visible link to the application's own hostname.
        self.assertIn('href="https://aws.logan911.com"', invite["EmailMessage"])

        code_subject = properties["EmailAuthenticationSubject"]
        code_message = properties["EmailAuthenticationMessage"]
        self.assertIn("LCDash", code_subject)
        self.assertIn("{####}", code_message)
        for expected in ("Logan County 911", "did not just try to sign in"):
            with self.subTest(code=expected):
                self.assertIn(expected, code_message)

        # Deliberately NO link in the code email. A message that carries a
        # one-time code and also invites a click is the exact shape of a
        # credential-harvesting email, so this asserts the absence.
        self.assertNotIn("href=", code_message)

    def test_cognito_groups_are_named_read_only_roles_without_iam_roles(self):
        self.template.resource_count_is("AWS::Cognito::UserPoolGroup", 6)
        resources = self.template.to_json()["Resources"]
        groups = {
            resource["Properties"]["GroupName"]: resource["Properties"]
            for resource in resources.values()
            if resource["Type"] == "AWS::Cognito::UserPoolGroup"
        }
        self.assertEqual(
            set(groups),
            {
                "lcdash-pilot-user",
                "lcdash-pilot-supervisor",
                "lcdash-pilot-dispatcher",
                "lcdash-pilot-fire-officer",
                "lcdash-pilot-admin",
                "lcdash-pilot-avatar",
            },
        )
        for group in groups.values():
            self.assertNotIn("RoleArn", group)
            self.assertIn("Read-only", group["Description"])
            self.assertIn("operational", group["Description"])

    def test_cognito_groups_match_the_application_role_map_exactly(self):
        """Infrastructure and the app's role map must not drift apart.

        They already had: the map named lcdash-pilot-administrator and its own
        test asserted all three roles were covered, while infrastructure created
        only two groups -- so the suite locked in a role the deployment could not
        grant. resolve_pilot_role denies any group it does not recognize, so a
        group present only in infrastructure locks its members out entirely,
        and one present only in the map is simply unassignable. Either direction
        is a silent failure, so both are asserted here.
        """
        from app.core.cloud_pilot_roles import COGNITO_GROUP_ROLE_MAP

        template_groups = {
            resource["Properties"]["GroupName"]
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::Cognito::UserPoolGroup"
        }
        self.assertEqual(
            template_groups,
            set(COGNITO_GROUP_ROLE_MAP),
            "Cognito groups in infrastructure must match COGNITO_GROUP_ROLE_MAP",
        )

    def test_login_has_no_public_bypass_or_browser_aws_credentials(self):
        self.template.resource_count_is("AWS::Cognito::IdentityPool", 0)
        self.template.resource_count_is("AWS::Lambda::Function", 1)
        resources = self.template.to_json()["Resources"]
        listeners = [
            resource["Properties"]
            for resource in resources.values()
            if resource["Type"] == "AWS::ElasticLoadBalancingV2::Listener"
        ]
        self.assertEqual(len(listeners), 2)
        https = next(listener for listener in listeners if listener["Port"] == 443)
        self.assertEqual(
            [action["Type"] for action in https["DefaultActions"]],
            ["authenticate-cognito", "forward"],
        )
        self.assertEqual(
            https["DefaultActions"][0]["AuthenticateCognitoConfig"]["OnUnauthenticatedRequest"],
            "authenticate",
        )
        self.assertEqual(
            https["DefaultActions"][0]["AuthenticateCognitoConfig"]["SessionTimeout"],
            "86400",
        )

    def test_only_the_approved_asset_paths_skip_authentication(self):
        """Exactly two listener rules exist, and only they bypass Cognito.

        Rule 1 (priority 5) forwards the static, secret-free paths in
        ALB_UNAUTHENTICATED_PATHS. Those background requests (service worker,
        manifest, favicon) fire while the user is still on the login page; when
        they authenticated, each one restarted the ALB auth flow and clobbered
        the AWSALBAuthNonce cookie, so the first Cognito callback of every
        session got a 401.

        Rule 2 (priority 6, added 2026-10-16) forwards POST
        ALB_MACHINE_INGEST_PATH, and since 2026-10-17 also POST
        ALB_MACHINE_HEARTBEAT_PATH, for the signed Mindshare radio listener;
        the application verifies an Ed25519 signature itself.

        This test is the guardrail: broadening either list, adding a third
        rule, widening the ingest rule to another method or a wildcard, or
        attaching an authenticate action to either rule must fail synth tests
        before it reaches a change set.
        """
        from lcdash_pilot.config import (
            ALB_MACHINE_HEARTBEAT_PATH,
            ALB_MACHINE_INGEST_PATH,
            ALB_MACHINE_PATHS,
            ALB_UNAUTHENTICATED_PATHS,
        )

        self.template.resource_count_is(
            "AWS::ElasticLoadBalancingV2::ListenerRule",
            2,
        )
        rules = sorted(
            (
                resource["Properties"]
                for resource in self.template.to_json()["Resources"].values()
                if resource["Type"] == "AWS::ElasticLoadBalancingV2::ListenerRule"
            ),
            key=lambda properties: properties["Priority"],
        )
        self.assertEqual([rule["Priority"] for rule in rules], [5, 6])
        asset_rule, ingest_rule = rules

        # Rule 1: unchanged.
        self.assertEqual(
            [action["Type"] for action in asset_rule["Actions"]],
            ["forward"],
            "The asset bypass must forward only -- never authenticate, "
            "redirect, or fixed-response",
        )
        self.assertEqual(len(asset_rule["Conditions"]), 1)
        self.assertEqual(asset_rule["Conditions"][0]["Field"], "path-pattern")
        self.assertEqual(
            sorted(asset_rule["Conditions"][0]["PathPatternConfig"]["Values"]),
            sorted(ALB_UNAUTHENTICATED_PATHS),
            "The bypassed paths must match ALB_UNAUTHENTICATED_PATHS exactly",
        )
        for path in ALB_UNAUTHENTICATED_PATHS:
            self.assertNotIn(
                "*",
                path,
                "Bypassed paths must be exact files, never wildcards",
            )

        # Rule 2: exactly one more, POST-only, two exact paths, forward-only.
        self.assertEqual(ALB_MACHINE_INGEST_PATH, "/api/radio/ingest")
        self.assertEqual(ALB_MACHINE_HEARTBEAT_PATH, "/api/radio/heartbeat")
        self.assertEqual(
            ALB_MACHINE_PATHS,
            ("/api/radio/ingest", "/api/radio/heartbeat"),
            "The signed machine rule covers exactly ingest and heartbeat",
        )
        for path in ALB_MACHINE_PATHS:
            self.assertNotIn("*", path)
            self.assertNotIn("?", path)
        self.assertEqual(
            [action["Type"] for action in ingest_rule["Actions"]],
            ["forward"],
            "The machine-ingest rule must forward only -- never authenticate, "
            "redirect, or fixed-response",
        )
        self.assertEqual(
            asset_rule["Actions"][0]["TargetGroupArn"],
            ingest_rule["Actions"][0]["TargetGroupArn"],
            "Both bypass rules must forward to the same web target group",
        )
        conditions = {
            condition["Field"]: condition for condition in ingest_rule["Conditions"]
        }
        self.assertEqual(
            set(conditions),
            {"path-pattern", "http-request-method"},
            "The ingest rule must be constrained by path AND method only",
        )
        self.assertEqual(len(ingest_rule["Conditions"]), 2)
        self.assertEqual(
            conditions["path-pattern"]["PathPatternConfig"]["Values"],
            ["/api/radio/ingest", "/api/radio/heartbeat"],
            "The machine rule must match exactly the two signed radio paths",
        )
        self.assertEqual(
            conditions["http-request-method"]["HttpRequestMethodConfig"]["Values"],
            ["POST"],
        )

        # Nothing else skips authentication: every bypassed pattern across
        # both rules is accounted for, and the listener default action stays
        # Cognito authenticate-then-forward.
        bypassed = {
            value
            for rule in rules
            for condition in rule["Conditions"]
            if condition["Field"] == "path-pattern"
            for value in condition["PathPatternConfig"]["Values"]
        }
        self.assertEqual(
            bypassed,
            set(ALB_UNAUTHENTICATED_PATHS) | set(ALB_MACHINE_PATHS),
        )
        https = next(
            resource["Properties"]
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::ElasticLoadBalancingV2::Listener"
            and resource["Properties"]["Port"] == 443
        )
        self.assertEqual(
            [action["Type"] for action in https["DefaultActions"]],
            ["authenticate-cognito", "forward"],
        )

    def test_content_bucket_expires_radio_audio_after_fifteen_days(self):
        """Radio audio is kept 15 days (decision of 2026-10-16), on a prefix
        of the existing content bucket. No new bucket, key, or encryption
        change; the pre-existing report rule must be untouched."""
        resources = self.template.to_json()["Resources"]
        content = next(
            resource["Properties"]
            for resource in resources.values()
            if resource["Type"] == "AWS::S3::Bucket"
            and "-content" in str(resource["Properties"].get("BucketName"))
        )
        rules = {
            rule["Id"]: rule for rule in content["LifecycleConfiguration"]["Rules"]
        }
        self.assertEqual(
            set(rules),
            {
                "ExpireShortLivedReports",
                "RadioAudioExpiry15Days",
                "RadioCorrectedAudioExpiry365Days",
            },
        )
        radio = rules["RadioAudioExpiry15Days"]
        self.assertEqual(radio["Prefix"], "radio/audio/")
        self.assertEqual(radio["ExpirationInDays"], 15)
        self.assertEqual(radio["Status"], "Enabled")
        # Operator-corrected radio items: own exact prefix, kept 365 days.
        corrected = rules["RadioCorrectedAudioExpiry365Days"]
        self.assertEqual(corrected["Prefix"], "radio/corrected/")
        self.assertEqual(corrected["ExpirationInDays"], 365)
        self.assertEqual(corrected["Status"], "Enabled")
        reports = rules["ExpireShortLivedReports"]
        self.assertEqual(reports["Prefix"], "logan-synthetic/reports/")
        self.assertEqual(reports["ExpirationInDays"], 7)
        # Still S3-managed encryption, no KMS, and still exactly two buckets.
        sse = content["BucketEncryption"]["ServerSideEncryptionConfiguration"]
        self.assertEqual(
            [
                item["ServerSideEncryptionByDefault"]["SSEAlgorithm"]
                for item in sse
            ],
            ["AES256"],
        )
        self.template.resource_count_is("AWS::S3::Bucket", 2)
        self.template.resource_count_is("AWS::KMS::Key", 0)

    def test_task_role_may_only_get_and_put_radio_audio_and_corrected_objects(self):
        resources = self.template.to_json()["Resources"]
        radio_statements = []
        for resource in resources.values():
            if resource["Type"] != "AWS::IAM::Policy":
                continue
            roles = [role.get("Ref", "") for role in resource["Properties"]["Roles"]]
            if not any(role.startswith("ApplicationTaskRole") for role in roles):
                continue
            for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
                serialized = str(statement["Resource"])
                if "radio/audio/" in serialized:
                    radio_statements.append(statement)
        self.assertEqual(len(radio_statements), 1)
        statement = radio_statements[0]
        self.assertEqual(statement["Effect"], "Allow")
        self.assertEqual(
            sorted(statement["Action"]), ["s3:GetObject", "s3:PutObject"]
        )
        self.assertNotIn("Condition", statement)
        # Exactly two resources, each <content bucket arn>/<radio prefix>*,
        # and nothing bucket-wide or broader than those two prefixes.
        resource_values = statement["Resource"]
        self.assertIsInstance(resource_values, list)
        self.assertEqual(len(resource_values), 2)
        suffixes = []
        for resource_value in resource_values:
            self.assertIsInstance(resource_value, dict)
            join_parts = resource_value["Fn::Join"][1]
            self.assertIn("ContentBucket", str(join_parts[0]))
            suffixes.append(join_parts[-1])
        self.assertEqual(
            sorted(suffixes), ["/radio/audio/*", "/radio/corrected/*"]
        )
        # The only other radio-scoped statement is ListBucket, pinned to the
        # two radio prefixes (so an expired object reads as NoSuchKey, not
        # AccessDenied). No grant reaches either prefix with Delete, and
        # nothing bucket-wide was added.
        list_statements = []
        for resource in resources.values():
            if resource["Type"] != "AWS::IAM::Policy":
                continue
            for item in resource["Properties"]["PolicyDocument"]["Statement"]:
                if "radio" in str(item.get("Condition", "")):
                    list_statements.append(item)
                actions = item["Action"]
                actions = [actions] if isinstance(actions, str) else actions
                item_resource = str(item.get("Resource", ""))
                if "radio/audio/" in item_resource or "radio/corrected/" in item_resource:
                    self.assertNotIn("s3:DeleteObject", actions)
                    self.assertNotIn("s3:*", actions)
        self.assertEqual(len(list_statements), 1)
        listing = list_statements[0]
        self.assertEqual(listing["Effect"], "Allow")
        self.assertEqual(listing["Action"], "s3:ListBucket")
        self.assertEqual(
            listing["Condition"],
            {"StringLike": {"s3:prefix": ["radio/audio/*", "radio/corrected/*"]}},
        )
        self.assertIn("ContentBucket", str(listing["Resource"]))

    def test_radio_ingest_environment_is_plain_and_secret_free(self):
        from lcdash_pilot.config import RADIO_INGEST_PUBLIC_KEY

        template = self.template.to_json()
        task = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
            and any(
                container["Name"] == "Web"
                for container in resource["Properties"]["ContainerDefinitions"]
            )
        )
        web = next(
            container
            for container in task["Properties"]["ContainerDefinitions"]
            if container["Name"] == "Web"
        )
        environment = {item["Name"]: item["Value"] for item in web["Environment"]}
        self.assertEqual(
            environment["LCDASH_RADIO_INGEST_PUBLIC_KEY"], RADIO_INGEST_PUBLIC_KEY
        )
        # Raw 32-byte Ed25519 public key, base64 (not a secret).
        import base64

        self.assertEqual(len(base64.b64decode(RADIO_INGEST_PUBLIC_KEY)), 32)
        self.assertEqual(environment["LCDASH_RADIO_AUDIO_PREFIX"], "radio/audio/")
        self.assertEqual(
            environment["LCDASH_RADIO_CORRECTED_PREFIX"], "radio/corrected/"
        )
        # The bucket is a reference to this stack's own content bucket.
        bucket_logical_id = next(
            name
            for name, resource in template["Resources"].items()
            if resource["Type"] == "AWS::S3::Bucket"
            and "-content" in str(resource["Properties"].get("BucketName"))
        )
        self.assertEqual(
            environment["LCDASH_RADIO_BUCKET"], {"Ref": bucket_logical_id}
        )
        # Nothing radio-related arrives through the Secrets channel.
        secret_names = {item["Name"] for item in web.get("Secrets", [])}
        self.assertFalse([name for name in secret_names if "RADIO" in name])

    def test_tenant_binding_is_fixed_in_task_not_a_parameter(self):
        template = self.template.to_json()
        self.assertNotIn("Tenant", template.get("Parameters", {}))
        task = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
        )
        web = task["Properties"]["ContainerDefinitions"][0]
        environment = {
            item["Name"]: item["Value"] for item in web["Environment"]
        }
        self.assertEqual(environment["LCDASH_TENANT"], "logan-synthetic")

    def test_teardown_resources_are_emptyable(self):
        self.template.resource_count_is("AWS::S3::Bucket", 2)
        self.template.has_resource_properties(
            "AWS::ECR::Repository",
            {"EmptyOnDelete": True},
        )
        resources = self.template.to_json()["Resources"]
        buckets = [
            resource
            for resource in resources.values()
            if resource["Type"] == "AWS::S3::Bucket"
        ]
        for bucket in buckets:
            self.assertEqual(bucket.get("DeletionPolicy"), "Delete")
            self.assertEqual(bucket.get("UpdateReplacePolicy"), "Delete")

    def test_dns_egress_is_limited_to_vpc_resolver(self):
        resources = self.template.to_json()["Resources"]
        application_groups = [
            resource
            for resource in resources.values()
            if resource["Type"] == "AWS::EC2::SecurityGroup"
            and resource["Properties"].get("GroupName")
            == "lcdash-p1-logan-use1-app"
        ]
        self.assertEqual(len(application_groups), 1)
        dns_egress = [
            rule
            for rule in application_groups[0]["Properties"]["SecurityGroupEgress"]
            if rule.get("FromPort") == 53 or rule.get("ToPort") == 53
        ]
        self.assertEqual(
            sorted(
                (
                    rule.get("IpProtocol"),
                    rule.get("CidrIp"),
                    rule.get("FromPort"),
                    rule.get("ToPort"),
                )
                for rule in dns_egress
            ),
            [
                ("tcp", "10.42.0.2/32", 53, 53),
                ("udp", "10.42.0.2/32", 53, 53),
            ],
        )

    def test_optional_audit_resources_are_conditioned(self):
        resources = self.template.to_json()["Resources"]
        optional = {
            logical_id: resource
            for logical_id, resource in resources.items()
            if "AuditBucket" in logical_id or "PilotTrail" in logical_id
        }
        self.assertTrue(optional)
        for logical_id, resource in optional.items():
            self.assertEqual(
                resource.get("Condition"),
                "CreatePilotTrailCondition",
                logical_id,
            )

    def test_database_is_backed_up_and_protected_without_high_availability(self):
        self.template.has_resource_properties(
            "AWS::RDS::DBInstance",
            {
                "BackupRetentionPeriod": 7,
                "DeletionProtection": True,
                # Single-AZ stays deliberate: this is a cost-bounded pilot, and
                # durability here comes from backups, not standby capacity.
                "MultiAZ": False,
            },
        )

    def test_database_survives_stack_teardown(self):
        self.template.has_resource(
            "AWS::RDS::DBInstance",
            {"DeletionPolicy": "Retain", "UpdateReplacePolicy": "Retain"},
        )

    def _collector_task_definition(self):
        return next(
            resource
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
            and resource["Properties"]["Family"]
            == "lcdash-p1-logan-use1-analytics-collector"
        )

    def _collector_security_group(self):
        resources = self.template.to_json()["Resources"]
        groups = [
            (logical_id, resource)
            for logical_id, resource in resources.items()
            if resource["Type"] == "AWS::EC2::SecurityGroup"
            and resource["Properties"].get("GroupName")
            == "lcdash-p1-logan-use1-analytics-collector"
        ]
        self.assertEqual(len(groups), 1)
        return groups[0]

    def test_analytics_collector_task_runs_the_collector_entrypoint(self):
        task = self._collector_task_definition()
        self.assertEqual(len(task["Properties"]["ContainerDefinitions"]), 1)
        container = task["Properties"]["ContainerDefinitions"][0]
        self.assertEqual(container["Name"], "AnalyticsCollector")
        self.assertEqual(
            container["Command"],
            ["python", "-m", "app.tools.cloud_analytics_collector"],
        )
        self.assertTrue(container["ReadonlyRootFilesystem"])
        self.assertEqual(container["User"], "10001:10001")
        # Same image contract as the web task: the shared pilot digest.
        self.assertEqual(
            container["Image"]["Fn::If"][0], "PilotImagePublishedCondition"
        )
        self.assertEqual(
            container["Image"]["Fn::If"][1]["Fn::Join"][1][-1],
            {"Ref": "PilotImageDigest"},
        )
        environment = {
            item["Name"]: item["Value"] for item in container["Environment"]
        }
        self.assertEqual(environment["LCDASH_CLOUD_CAD_ENABLED"], "true")
        self.assertEqual(
            environment["LCDASH_CLOUD_CAD_MODE"], "centralsquare-read-poll"
        )
        self.assertEqual(
            environment["LCDASH_CLOUD_CAD_SECRET_ARN"],
            {"Ref": "CloudCadReadSecretArn"},
        )
        self.assertEqual(environment["LCDASH_TENANT"], "logan-synthetic")
        self.assertEqual(environment["LCDASH_DATABASE_NAME"], "lcdash")
        self.assertIn("Fn::GetAtt", environment["LCDASH_DATABASE_HOST"])
        self.assertEqual(
            {item["Name"] for item in container["Secrets"]},
            {"LCDASH_DATABASE_USERNAME", "LCDASH_DATABASE_PASSWORD"},
        )
        self.assertEqual(
            container["LogConfiguration"]["Options"]["awslogs-stream-prefix"],
            "analytics-collector",
        )
        self.template.has_resource_properties(
            "AWS::Logs::LogGroup",
            {"LogGroupName": "/lcdash/lcdash-p1-logan-use1/analytics-collector"},
        )

    def test_analytics_collector_schedule_ships_disabled(self):
        template = self.template.to_json()
        self.template.resource_count_is("AWS::Events::Rule", 1)
        enabled = template["Parameters"]["AnalyticsCollectorEnabled"]
        self.assertEqual(enabled["Default"], "false")
        self.assertEqual(enabled["AllowedValues"], ["true", "false"])
        minutes = template["Parameters"]["AnalyticsCollectorScheduleMinutes"]
        self.assertEqual(minutes["Type"], "Number")
        self.assertEqual(minutes["Default"], 30)
        self.assertEqual(minutes["MinValue"], 5)
        self.assertEqual(
            template["Conditions"]["AnalyticsCollectorEnabledCondition"],
            {"Fn::Equals": [{"Ref": "AnalyticsCollectorEnabled"}, "true"]},
        )
        rule = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::Events::Rule"
        )
        # State is a condition, so the default parameter value renders DISABLED.
        self.assertEqual(
            rule["Properties"]["State"],
            {
                "Fn::If": [
                    "AnalyticsCollectorEnabledCondition",
                    "ENABLED",
                    "DISABLED",
                ]
            },
        )
        self.assertEqual(
            rule["Properties"]["ScheduleExpression"]["Fn::Join"][1],
            ["rate(", {"Ref": "AnalyticsCollectorScheduleMinutes"}, " minutes)"],
        )

    def test_analytics_collector_rule_targets_the_collector_task_publicly(self):
        template = self.template.to_json()
        rule = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::Events::Rule"
        )
        targets = rule["Properties"]["Targets"]
        self.assertEqual(len(targets), 1)
        parameters = targets[0]["EcsParameters"]
        self.assertEqual(parameters["LaunchType"], "FARGATE")
        self.assertEqual(parameters["TaskCount"], 1)
        collector_logical_id = next(
            logical_id
            for logical_id, resource in template["Resources"].items()
            if resource["Type"] == "AWS::ECS::TaskDefinition"
            and resource["Properties"]["Family"]
            == "lcdash-p1-logan-use1-analytics-collector"
        )
        self.assertEqual(
            parameters["TaskDefinitionArn"], {"Ref": collector_logical_id}
        )
        self.assertEqual(targets[0]["Arn"], {"Fn::GetAtt": ["ClusterEB0386A7", "Arn"]})

        network = parameters["NetworkConfiguration"]["AwsVpcConfiguration"]
        # This VPC has no NAT and no interface endpoints, so an isolated-subnet
        # task could never pull the image or read its secrets.
        self.assertEqual(network["AssignPublicIp"], "ENABLED")
        subnets = [subnet["Ref"] for subnet in network["Subnets"]]
        self.assertTrue(subnets)
        for subnet in subnets:
            self.assertIn("publicSubnet", subnet)
        collector_group_id, _ = self._collector_security_group()
        self.assertEqual(
            network["SecurityGroups"],
            [{"Fn::GetAtt": [collector_group_id, "GroupId"]}],
        )

    def test_analytics_collector_security_group_is_https_dns_and_postgres_only(self):
        collector_group_id, group = self._collector_security_group()
        egress = group["Properties"]["SecurityGroupEgress"]
        self.assertEqual(
            sorted(
                (
                    rule.get("IpProtocol"),
                    rule.get("FromPort"),
                    rule.get("ToPort"),
                    rule.get("CidrIp"),
                )
                for rule in egress
            ),
            [
                ("tcp", 53, 53, "10.42.0.2/32"),
                ("tcp", 443, 443, "0.0.0.0/0"),
                ("udp", 53, 53, "10.42.0.2/32"),
            ],
        )
        self.assertNotIn("SecurityGroupIngress", group["Properties"])

        resources = self.template.to_json()["Resources"]
        database_group_id = next(
            logical_id
            for logical_id, resource in resources.items()
            if resource["Type"] == "AWS::EC2::SecurityGroup"
            and "DatabaseSecurityGroup" in logical_id
        )
        postgres_egress = [
            resource["Properties"]
            for resource in resources.values()
            if resource["Type"] == "AWS::EC2::SecurityGroupEgress"
            and resource["Properties"].get("GroupId")
            == {"Fn::GetAtt": [collector_group_id, "GroupId"]}
        ]
        self.assertEqual(len(postgres_egress), 1)
        self.assertEqual(postgres_egress[0]["FromPort"], 5432)
        self.assertEqual(postgres_egress[0]["ToPort"], 5432)
        self.assertEqual(
            postgres_egress[0]["DestinationSecurityGroupId"],
            {"Fn::GetAtt": [database_group_id, "GroupId"]},
        )

        postgres_ingress = [
            resource["Properties"]
            for resource in resources.values()
            if resource["Type"] == "AWS::EC2::SecurityGroupIngress"
            and resource["Properties"].get("SourceSecurityGroupId")
            == {"Fn::GetAtt": [collector_group_id, "GroupId"]}
        ]
        self.assertEqual(len(postgres_ingress), 1)
        self.assertEqual(postgres_ingress[0]["FromPort"], 5432)
        self.assertEqual(
            postgres_ingress[0]["GroupId"],
            {"Fn::GetAtt": [database_group_id, "GroupId"]},
        )

    def test_analytics_collector_task_role_holds_only_the_cad_secret(self):
        resources = self.template.to_json()["Resources"]
        policies = [
            resource
            for logical_id, resource in resources.items()
            if resource["Type"] == "AWS::IAM::Policy"
            and logical_id.startswith("AnalyticsCollectorTaskRole")
        ]
        self.assertEqual(len(policies), 1)
        statements = policies[0]["Properties"]["PolicyDocument"]["Statement"]
        self.assertEqual(len(statements), 1)
        self.assertEqual(statements[0]["Action"], "secretsmanager:GetSecretValue")
        self.assertEqual(statements[0]["Resource"], {"Ref": "CloudCadReadSecretArn"})

        collector_role_id = next(
            logical_id
            for logical_id, resource in resources.items()
            if resource["Type"] == "AWS::IAM::Role"
            and logical_id.startswith("AnalyticsCollectorTaskRole")
        )
        role = resources[collector_role_id]
        self.assertNotIn("ManagedPolicyArns", role["Properties"])
        self.assertNotIn("Policies", role["Properties"])

        for statement in statements:
            actions = statement["Action"]
            actions = actions if isinstance(actions, list) else [actions]
            for action in actions:
                self.assertFalse(action.startswith("s3:"), action)
                self.assertFalse(action.startswith("kms:"), action)
                self.assertFalse(action.startswith("bedrock:"), action)

    def test_analytics_collector_outputs_are_published(self):
        outputs = self.template.to_json()["Outputs"]
        for name in (
            "AnalyticsCollectorTaskDefinitionArn",
            "AnalyticsCollectorLogGroupName",
            "AnalyticsCollectorSecurityGroupId",
        ):
            self.assertIn(name, outputs)
        self.assertEqual(
            outputs["AnalyticsCollectorLogGroupName"]["Value"],
            {"Ref": "AnalyticsCollectorLogsC30B61C9"},
        )

    def test_budget_is_two_hundred_usd(self):
        self.template.has_resource_properties(
            "AWS::Budgets::Budget",
            {
                "Budget": Match.object_like(
                    {"BudgetLimit": {"Amount": 200, "Unit": "USD"}}
                )
            },
        )


if __name__ == "__main__":
    unittest.main()
