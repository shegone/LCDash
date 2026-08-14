"""Explicit environment validation with no AWS context lookups."""

from dataclasses import dataclass
import re

import aws_cdk as cdk


ACCOUNT_PATTERN = re.compile(r"^[0-9]{12}$")
APPROVED_REGION = "us-east-1"
NAME_PREFIX = "lcdash-p1-logan-use1"

# The registrable domain. Registered at Hostinger, but authoritative DNS is
# delegated to Cloudflare -- every record below is managed there, never in
# Hostinger. Note that this apex already resolves (A records to the Hostinger
# web host), which satisfies Cognito's requirement that a custom auth domain's
# parent domain have a DNS A record before it can be created.
PILOT_ZONE_NAME = "logan911.com"

# Hostname serving the application, behind the ALB.
PILOT_DOMAIN_NAME = f"aws.{PILOT_ZONE_NAME}"

# The ALB's OIDC session cookie name. The load balancer shards it as
# "<name>-0", "<name>-1", ... up to four parts. Defined here because BOTH the
# listener that sets it and the application that must expire it on sign-out
# have to agree; when they did not, sign-out silently did nothing.
ALB_SESSION_COOKIE_NAME = "LCDashPilotAuth"

# The only paths the HTTPS listener serves WITHOUT Cognito authentication.
# Browsers request all three in the background while the user is still on the
# login page -- the manifest is even fetched WITHOUT cookies unless the link
# tag opts in -- and every such request used to restart the ALB's auth flow,
# minting a fresh AWSALBAuthNonce cookie that clobbered the one the real login
# was using. Cognito's callback then arrived bound to a nonce the browser no
# longer held, and the ALB answered 401 on the first login of every session
# (diagnosed from ALB access logs, 2026-08-14).
#
# Everything here must be static, secret-free, and safe to serve to the open
# internet, because that is exactly what this list does. Do not add paths
# casually: /static/* at large, every API route, and every page stay behind
# authentication.
ALB_UNAUTHENTICATED_PATHS = (
    "/static/service-worker.js",
    "/static/manifest.webmanifest",
    "/favicon.ico",
)

# Hostname serving Cognito managed login. Kept on our own domain rather than the
# Cognito prefix domain for two reasons: a county-owned hostname is what users
# should be asked to trust with a password, and WebAuthn passkeys bind to a
# relying-party ID that can only be the custom domain once one exists -- so
# enrolling passkeys against the prefix domain first would invalidate every one
# of them the day this is introduced.
PILOT_AUTH_DOMAIN_NAME = f"auth.{PILOT_ZONE_NAME}"

# Sender identity for Cognito messages (MFA codes and admin-created temporary
# credentials). These are literals rather than stack parameters because CDK
# validates at synthesis that the from-address sits inside the verified domain,
# and it cannot compare two unresolved CloudFormation parameter tokens. Keeping
# them together here also removes the chance of deploying a mismatched pair.
#
# PILOT_MAIL_DOMAIN must be verified as a DOMAIN identity in SES -- which means
# publishing its DKIM CNAME records in authoritative Cloudflare DNS -- because
# this domain becomes the EmailConfiguration SourceArn.
PILOT_MAIL_DOMAIN = PILOT_ZONE_NAME
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
