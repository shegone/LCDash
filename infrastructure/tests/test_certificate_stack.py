from pathlib import Path
import sys
import unittest


INFRASTRUCTURE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INFRASTRUCTURE_ROOT))

try:
    import aws_cdk as cdk
    from aws_cdk.assertions import Template
    from lcdash_pilot.certificate_stack import Phase1CertificateStack
except ImportError:
    cdk = None


EXPECTED_TAGS = [
    {"Key": "Authority", "Value": "non-authoritative"},
    {"Key": "DataScope", "Value": "synthetic-disconnected"},
    {"Key": "Environment", "Value": "pilot"},
    {"Key": "ManagedBy", "Value": "CDK"},
    {"Key": "Phase", "Value": "1"},
    {"Key": "Project", "Value": "LCDash-AWS"},
    {"Key": "Region", "Value": "us-east-1"},
    {"Key": "Tenant", "Value": "logan-synthetic"},
]


@unittest.skipUnless(cdk is not None, "aws-cdk-lib is not installed")
class CertificateStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = cdk.App()
        stack = Phase1CertificateStack(
            app,
            "TestCertificate",
            env=cdk.Environment(account="111111111111", region="us-east-1"),
        )
        cls.template = Template.from_stack(stack)

    def test_requests_exactly_two_dns_validated_certificates_without_route53(self):
        """One certificate per hostname, and DNS validation stays external.

        Two separate certificates rather than one with both names as SANs:
        editing an ACM certificate's SAN list replaces the certificate, which
        would take the live ALB listener's certificate down with it.
        """
        self.template.resource_count_is("AWS::CertificateManager::Certificate", 2)
        self.template.resource_count_is("AWS::Route53::RecordSet", 0)

        certificates = {
            resource["Properties"]["DomainName"]: resource["Properties"]
            for resource in self.template.to_json()["Resources"].values()
            if resource["Type"] == "AWS::CertificateManager::Certificate"
        }
        self.assertEqual(
            set(certificates), {"aws.logan911.com", "auth.logan911.com"}
        )
        for domain, properties in certificates.items():
            with self.subTest(domain=domain):
                self.assertEqual(properties["ValidationMethod"], "DNS")
                self.assertEqual(
                    properties["DomainValidationOptions"],
                    [{"DomainName": domain, "ValidationDomain": domain}],
                )
                self.assertEqual(properties["Tags"], EXPECTED_TAGS)

    def test_each_certificate_arn_is_emitted_as_a_distinct_output(self):
        """The two ARNs feed different consumers and must not be conflated.

        CertificateArn goes to the ALB HTTPS listener; AuthCertificateArn goes to
        the Cognito custom domain. Swapping them would produce a hostname
        mismatch that only shows up as a browser certificate error at sign-in.
        """
        outputs = self.template.to_json()["Outputs"]
        self.assertIn("CertificateArn", outputs)
        self.assertIn("AuthCertificateArn", outputs)
        self.assertNotEqual(
            outputs["CertificateArn"]["Value"],
            outputs["AuthCertificateArn"]["Value"],
        )
        for name in ("CertificateArn", "AuthCertificateArn"):
            with self.subTest(output=name):
                # Both must tell the operator to wait for ISSUED, and to publish
                # the validation record in Cloudflare rather than Hostinger.
                description = outputs[name]["Description"]
                self.assertIn("ISSUED", description)
                self.assertIn("Cloudflare", description)
                self.assertNotIn("Hostinger", description)

    def test_stack_refuses_to_synthesize_outside_us_east_1(self):
        """Both the ALB listener and a Cognito custom domain require us-east-1."""
        app = cdk.App()
        with self.assertRaises(ValueError):
            Phase1CertificateStack(
                app,
                "WrongRegion",
                env=cdk.Environment(account="111111111111", region="us-west-2"),
            )


if __name__ == "__main__":
    unittest.main()
