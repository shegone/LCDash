#!/usr/bin/env python3
"""Standalone CDK entry point for the unapproved document-library stack."""

import aws_cdk as cdk

from lcdash_pilot.config import load_environment
from lcdash_pilot.document_library_stack import Phase1DocumentLibraryStack


app = cdk.App()
environment = load_environment(app)

Phase1DocumentLibraryStack(
    app,
    "lcdash-p1-logan-use1-document-library",
    env=cdk.Environment(account=environment.account, region=environment.region),
    description=(
        "LCDash Phase 1 private reviewed-document library; local plan only until "
        "separately authorized"
    ),
)

app.synth()
