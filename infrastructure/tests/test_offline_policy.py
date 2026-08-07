import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "lcdash_pilot" / "foundation_stack.py"


class OfflinePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shape = json.loads((ROOT / "approved_shape.json").read_text(encoding="utf-8"))
        cls.stack_source = STACK.read_text(encoding="utf-8")

    def test_manifest_captures_approved_single_system_shape(self):
        expected = {
            "region": "us-east-1",
            "pilot_domain": "aws.logan911.com",
            "dns_provider": "hostinger-managed",
            "route53_records": 0,
            "certificate_validation": "external-dns-two-stage",
            "prefix": "lcdash-p1-logan-use1",
            "data_scope": "synthetic-disconnected",
            "budget_usd": 200,
            "public_subnets": 2,
            "isolated_database_subnets": 2,
            "nat_gateways": 0,
            "ecs_services": 1,
            "initial_desired_tasks": 0,
            "maximum_reviewed_desired_tasks": 1,
            "activation_requires_separate_review": True,
            "initial_image_digest": "NOT_PUBLISHED",
            "activation_image_reference": "immutable-sha256-digest",
            "autoscaling": False,
            "deployment_circuit_breaker_rollback": True,
            "database_instances": 1,
            "database_multi_az": False,
            "database_backup_days": 0,
            "database_final_snapshot": False,
            "content_bucket_versioning": False,
            "bucket_auto_delete_on_teardown": True,
            "ecr_empty_on_teardown": True,
            "alb_authentication": "cognito",
            "alb_cognito_client_confidential": True,
            "cognito_groups": [
                "lcdash-pilot-viewer",
                "lcdash-pilot-reviewer",
            ],
            "cognito_mfa": "required-totp",
            "cognito_access_token_minutes": 15,
            "cognito_id_token_minutes": 15,
            "cognito_refresh_token_days": 1,
            "alb_session_hours": 1,
            "fixed_tenant": "logan-synthetic",
            "client_supplied_tenant_selector": False,
            "application_lambda_count": 0,
            "teardown_custom_resource_lambda_allowed": True,
            "cad_secret_count": 0,
            "collector_count": 0,
            "webhook_count": 0,
            "operational_output_count": 0,
        }
        for key, value in expected.items():
            self.assertEqual(self.shape[key], value, key)

    def test_stack_has_no_account_or_dns_lookup(self):
        for forbidden in (
            "Vpc.from_lookup",
            "HostedZone.from_lookup",
            "from_hosted_zone_attributes",
            "route53.ARecord",
            "value_from_lookup",
        ):
            self.assertNotIn(forbidden, self.stack_source)
        self.assertIn('nat_gateways=0', self.stack_source)

    def test_stack_is_single_system_without_backup_or_autoscaling(self):
        required = (
            'desired_count=parameters["desired_task_count"].value_as_number',
            "multi_az=False",
            "backup_retention=cdk.Duration.days(0)",
            "deletion_protection=False",
            "delete_automated_backups=True",
            "versioned=False",
            "assign_public_ip=True",
            "circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True)",
            "auto_delete_objects=True",
            "empty_on_delete=True",
        )
        for text in required:
            self.assertIn(text, self.stack_source)
        for forbidden in ("ApplicationAutoScaling", "AutoScalingGroup", "BackupPlan", "NatGateway"):
            self.assertNotIn(forbidden, self.stack_source)
        self.assertNotIn("cloudwatch_logs_retention", self.stack_source)

    def test_container_insights_uses_current_explicit_off_setting(self):
        self.assertIn(
            "container_insights_v2=ecs.ContainerInsights.DISABLED",
            self.stack_source,
        )
        self.assertNotIn("container_insights=False", self.stack_source)

    def test_database_secret_is_constructed_and_cad_reference_is_scoped(self):
        self.assertEqual(self.stack_source.count("from_generated_secret"), 1)
        self.assertNotIn("aws_secretsmanager", self.stack_source)
        self.assertNotIn("database.secret.grant_read(task_role)", self.stack_source)
        self.assertIn('actions=["secretsmanager:GetSecretValue"]', self.stack_source)
        self.assertIn('"LCDASH_CLOUD_CAD_ENABLED": "true"', self.stack_source)
        self.assertIn('"LCDASH_CLOUD_CAD_MODE": "centralsquare-read-poll"', self.stack_source)
        self.assertIn('ecs.Secret.from_secrets_manager(database.secret, "username")', self.stack_source)
        self.assertIn('ecs.Secret.from_secrets_manager(database.secret, "password")', self.stack_source)

    def test_managed_provider_permissions_are_read_or_inference_only(self):
        for action in (
            "bedrock:Retrieve",
            "bedrock:InvokeModel",
            # Sentence-streamed advisory generation (cloud_ai_streaming.py)
            # calls converse_stream, which Bedrock authorizes under this
            # action rather than plain InvokeModel.
            "bedrock:InvokeModelWithResponseStream",
            "polly:SynthesizeSpeech",
            "transcribe:StartStreamTranscription",
            "geo-maps:GetTile",
            "geo-places:Geocode",
            "geo-routes:CalculateRoutes",
        ):
            self.assertIn(action, self.stack_source)
        for action in (
            # Batch transcription would persist audio in S3; streaming does not.
            "transcribe:StartTranscriptionJob",
            "transcribe:StartMedicalStreamTranscription",
            "transcribe:StartCallAnalyticsStreamTranscription",
            "sns:Publish",
            "ses:SendEmail",
            "sqs:SendMessage",
            "execute-api:Invoke",
        ):
            self.assertNotIn(action, self.stack_source)

    def test_alb_has_explicit_task_egress(self):
        self.assertIn(
            "alb_security_group.add_egress_rule(app_security_group, ec2.Port.tcp(8000))",
            self.stack_source,
        )

    def test_app_has_explicit_vpc_resolver_dns_egress(self):
        self.assertIn('resolver = ec2.Peer.ipv4("10.42.0.2/32")', self.stack_source)
        self.assertIn("add_egress_rule(resolver, ec2.Port.udp(53))", self.stack_source)
        self.assertIn("add_egress_rule(resolver, ec2.Port.tcp(53))", self.stack_source)

    def test_https_listener_authenticates_with_confidential_cognito_client(self):
        for required in (
            "AuthenticateCognitoAction",
            "on_unauthenticated_request=elbv2.UnauthenticatedAction.AUTHENTICATE",
            "next=elbv2.ListenerAction.forward([target_group])",
            "generate_secret=True",
            '"/oauth2/idpresponse"',
        ):
            self.assertIn(required, self.stack_source)
        self.assertNotIn("generate_secret=False", self.stack_source)
        self.assertNotIn("tenant_selector", self.stack_source)
        self.assertNotIn("CognitoCallbackUrl", self.stack_source)
        self.assertNotIn("IdentityPool", self.stack_source)
        self.assertNotIn("lambda_triggers", self.stack_source)
        self.assertNotIn("UserPoolIdentityProvider", self.stack_source)

    def test_teardown_flags_cover_both_buckets_and_repository(self):
        self.assertEqual(self.stack_source.count("auto_delete_objects=True"), 2)
        self.assertEqual(self.stack_source.count("empty_on_delete=True"), 1)

    def test_optional_audit_subtree_is_conditioned(self):
        self.assertIn("*audit_bucket.node.find_all()", self.stack_source)
        self.assertIn("*trail.node.find_all()", self.stack_source)
        self.assertIn("resource.cfn_options.condition = condition", self.stack_source)

    def test_required_tags_are_deterministic(self):
        for text in (
            '"Project": "LCDash-AWS"',
            '"Environment": "pilot"',
            '"Phase": "1"',
            '"Tenant": "logan-synthetic"',
            '"DataScope": "synthetic-disconnected"',
            '"Authority": "non-authoritative"',
        ):
            self.assertIn(text, self.stack_source)


if __name__ == "__main__":
    unittest.main()
