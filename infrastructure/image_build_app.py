#!/usr/bin/env python3
"""Standalone CDK entry point for the authorized pilot image builder."""

import aws_cdk as cdk

from lcdash_pilot.config import load_environment
from lcdash_pilot.image_build_stack import Phase1ImageBuildStack


app = cdk.App()
environment = load_environment(app)

Phase1ImageBuildStack(
    app,
    "lcdash-p1-logan-use1-image-build",
    env=cdk.Environment(account=environment.account, region=environment.region),
    description="LCDash Phase 1 synthetic pilot image builder",
)

app.synth()
