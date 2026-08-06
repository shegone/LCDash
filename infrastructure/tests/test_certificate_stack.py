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

    def test_requests_one_dns_validated_certificate_without_route53(self):
        self.template.resource_count_is("AWS::CertificateManager::Certificate", 1)
        self.template.has_resource_properties(
            "AWS::CertificateManager::Certificate",
            {
                "DomainName": "aws.logan911.com",
                "ValidationMethod": "DNS",
                "DomainValidationOptions": [
                    {
                        "DomainName": "aws.logan911.com",
                        "ValidationDomain": "aws.logan911.com",
                    }
                ],
                "Tags": [
                    {"Key": "Authority", "Value": "non-authoritative"},
                    {"Key": "DataScope", "Value": "synthetic-disconnected"},
                    {"Key": "Environment", "Value": "pilot"},
                    {"Key": "ManagedBy", "Value": "CDK"},
                    {"Key": "Phase", "Value": "1"},
                    {"Key": "Project", "Value": "LCDash-AWS"},
                    {"Key": "Region", "Value": "us-east-1"},
                    {"Key": "Tenant", "Value": "logan-synthetic"},
                ],
            },
        )
        self.template.resource_count_is("AWS::Route53::RecordSet", 0)


if __name__ == "__main__":
    unittest.main()
