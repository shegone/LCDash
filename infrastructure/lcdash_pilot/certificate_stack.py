"""Standalone ACM certificate request for externally managed Hostinger DNS."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import aws_certificatemanager as acm
from constructs import Construct

from .config import APPROVED_REGION, PILOT_DOMAIN_NAME


class Phase1CertificateStack(cdk.Stack):
    """Request only the pilot certificate; Hostinger validation remains manual."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        if self.region != APPROVED_REGION:
            raise ValueError("Phase 1 certificate may synthesize only in us-east-1.")

        certificate = acm.CfnCertificate(
            self,
            "PilotCertificate",
            domain_name=PILOT_DOMAIN_NAME,
            validation_method="DNS",
            domain_validation_options=[
                acm.CfnCertificate.DomainValidationOptionProperty(
                    domain_name=PILOT_DOMAIN_NAME,
                    validation_domain=PILOT_DOMAIN_NAME,
                )
            ],
            tags=[
                cdk.CfnTag(key="Project", value="LCDash-AWS"),
                cdk.CfnTag(key="Environment", value="pilot"),
                cdk.CfnTag(key="Phase", value="1"),
                cdk.CfnTag(key="Tenant", value="logan-synthetic"),
                cdk.CfnTag(key="Region", value=APPROVED_REGION),
                cdk.CfnTag(key="DataScope", value="synthetic-disconnected"),
                cdk.CfnTag(key="Authority", value="non-authoritative"),
                cdk.CfnTag(key="ManagedBy", value="CDK"),
            ],
        )
        cdk.CfnOutput(
            self,
            "CertificateArn",
            value=certificate.ref,
            description="Use only after Hostinger DNS validation reports ISSUED.",
        )
