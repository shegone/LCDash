#!/usr/bin/env python3
"""Local CDK entry point; requires explicit account and us-east-1 context."""

import aws_cdk as cdk

from lcdash_pilot.config import load_environment
from lcdash_pilot.certificate_stack import Phase1CertificateStack
from lcdash_pilot.foundation_stack import Phase1FoundationStack


app = cdk.App()
environment = load_environment(app)

Phase1CertificateStack(
    app,
    "lcdash-p1-logan-use1-certificate",
    env=cdk.Environment(account=environment.account, region=environment.region),
    description="LCDash Phase 1 certificate request for Hostinger-managed DNS",
)

Phase1FoundationStack(
    app,
    "lcdash-p1-logan-use1-foundation",
    env=cdk.Environment(account=environment.account, region=environment.region),
    description="LCDash Phase 1 synthetic/disconnected secondary pilot",
)

app.synth()
