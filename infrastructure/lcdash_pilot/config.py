"""Explicit environment validation with no AWS context lookups."""

from dataclasses import dataclass
import re

import aws_cdk as cdk


ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
APPROVED_REGION = "us-east-1"
NAME_PREFIX = "lcdash-p1-logan-use1"
PILOT_DOMAIN_NAME = "aws.logan911.com"


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
