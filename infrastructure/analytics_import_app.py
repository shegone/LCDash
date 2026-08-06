#!/usr/bin/env python3
"""Standalone CDK entry point for the unapproved analytics-import stack."""

import aws_cdk as cdk

from lcdash_pilot.analytics_import_stack import Phase2AnalyticsImportStack
from lcdash_pilot.config import load_environment


app = cdk.App()
environment = load_environment(app)

Phase2AnalyticsImportStack(
    app,
    "lcdash-p2-logan-use1-analytics-import",
    env=cdk.Environment(account=environment.account, region=environment.region),
    description=(
        "Dormant one-way historical analytics import staging and task definition; "
        "not authorized for deployment or execution"
    ),
)

app.synth()
