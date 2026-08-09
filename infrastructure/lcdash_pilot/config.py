"""Explicit environment validation with no AWS context lookups."""

from dataclasses import dataclass
import re

import aws_cdk as cdk


ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
APPROVED_REGION = "us-east-1"
NAME_PREFIX = "lcdash-p1-logan-use1"
PILOT_DOMAIN_NAME = "aws.logan911.com"

# Sender identity for Cognito messages (MFA codes and admin-created temporary
# credentials). These are literals rather than stack parameters because CDK
# validates at synthesis that the from-address sits inside the verified domain,
# and it cannot compare two unresolved CloudFormation parameter tokens. Keeping
# them together here also removes the chance of deploying a mismatched pair.
#
# PILOT_MAIL_DOMAIN must be verified as a DOMAIN identity in SES -- which means
# publishing its DKIM CNAME records in authoritative Cloudflare DNS -- because
# this domain becomes the EmailConfiguration SourceArn.
PILOT_MAIL_DOMAIN = "logan911.com"
PILOT_MAIL_FROM_ADDRESS = f"no-reply@{PILOT_MAIL_DOMAIN}"


@dataclass(frozen=True, slots=True)
class PilotEnvironment:
    account: str
    region: str


def load_environment(app: cdk.App) -> PilotEnvironment:
    account = str(app.node.try_get_context("account") or "").strip()
    region = str(app.node.try_get_context("region") or "").strip()
    if not ACCOUNT_PATTERN.fullmatch(account):
        raise ValueError("Explicit 12-digit account context is required.")
    if region != APPROVED_REGION:
        raise ValueError("Phase 1 is restricted to us-east-1.")
    return PilotEnvironment(account=account, region=region)
