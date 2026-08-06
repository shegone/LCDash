#!/usr/bin/env python3
"""Standalone synth entry point for the dormant release builder."""

import aws_cdk as cdk

from lcdash_pilot.config import load_environment
from lcdash_pilot.release_builder_stack import Phase1ReleaseBuilderStack


app = cdk.App()
environment = load_environment(app)

Phase1ReleaseBuilderStack(
    app,
    "lcdash-p1-logan-use1-release-builder",
    env=cdk.Environment(account=environment.account, region=environment.region),
    description="LCDash Phase 1 local-only release-builder definition",
)

app.synth()
