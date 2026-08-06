#!/usr/bin/env python3
"""Standalone synthesis entry point; no live foundation references."""

import aws_cdk as cdk

from lcdash_pilot.knowledge_search_stack import Phase1KnowledgeSearchStack

app = cdk.App()
Phase1KnowledgeSearchStack(
    app,
    "lcdash-p1-logan-use1-knowledge-search",
    env=cdk.Environment(account="862772137583", region="us-east-1"),
    description="Plan-only private Bedrock knowledge search for approved documents",
    termination_protection=True,
)
app.synth()
