from pathlib import Path
import sys
import unittest


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INFRASTRUCTURE_ROOT))

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

    def test_service_allows_one_replacement_task_during_rollout(self):
        template = self.template.to_json()
        service = next(
            resource
            for resource in template["Resources"].values()
            if resource["Type"] == "AWS::ECS::Service"
        )
        deployment = service["Properties"]["DeploymentConfiguration"]
        self.assertEqual(deployment["MinimumHealthyPercent"], 0)
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
        self.assertEqual(len(secret_policies), 1)
        attached_role = secret_policies[0]["Properties"]["Roles"][0]["Ref"]
        self.assertIn("ExecutionRole", attached_role)
        self.assertNotIn("ApplicationTaskRole", attached_role)

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
        self.assertEqual(len(matches), 2)
        cad_statement = next(
            statement
            for statement in matches
            if statement["Resource"] == {"Ref": "CloudCadReadSecretArn"}
        )
        self.assertEqual(cad_statement["Action"], "secretsmanager:GetSecretValue")

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

    def test_cognito_pool_requires_strong_password_and_totp_mfa(self):
        self.template.has_resource_properties(
            "AWS::Cognito::UserPool",
            {
                "AdminCreateUserConfig": {"AllowAdminCreateUserOnly": True},
                "MfaConfiguration": "ON",
                "EnabledMfas": ["SOFTWARE_TOKEN_MFA"],
                "AccountRecoverySetting": {
                    "RecoveryMechanisms": [
                        {"Name": "verified_email", "Priority": 1}
                    ]
                },
                "Policies": {
                    "PasswordPolicy": {
                        "MinimumLength": 14,
                        "RequireLowercase": True,
                        "RequireUppercase": True,
                        "RequireNumbers": True,
                        "RequireSymbols": True,
                        "TemporaryPasswordValidityDays": 1,
                    }
                },
            },
        )

    def test_cognito_groups_are_named_read_only_roles_without_iam_roles(self):
        self.template.resource_count_is("AWS::Cognito::UserPoolGroup", 2)
        resources = self.template.to_json()["Resources"]
        groups = {
            resource["Properties"]["GroupName"]: resource["Properties"]
            for resource in resources.values()
            if resource["Type"] == "AWS::Cognito::UserPoolGroup"
        }
        self.assertEqual(
            set(groups),
            {"lcdash-pilot-viewer", "lcdash-pilot-reviewer"},
        )
        for group in groups.values():
            self.assertNotIn("RoleArn", group)
            self.assertIn("Read-only", group["Description"])
            self.assertIn("operational", group["Description"])

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
        self.template.resource_count_is(
            "AWS::ElasticLoadBalancingV2::ListenerRule",
            0,
        )

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
