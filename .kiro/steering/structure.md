---
inclusion: always
---

# Target project structure

The migration is incremental. Existing on-prem behavior stays functional while
new interfaces are introduced and tested in this AWS branch.

```text
app/
  core/                    normalized domain, tenant context, capabilities
  integrations/
    cad/
      base.py              CAD provider protocol and capability contract
      centralsquare/       CentralSquare authentication and field mapping
      synthetic/           deterministic test/demo provider
    ai/                    Ollama and Bedrock providers
    knowledge/             local and AWS retrieval providers
    speech/                local, Transcribe, Polly, optional Qwen providers
    identity/              claims, county context, authorization adapter
  modules/
    operations/            calls, units, real-time views
    analytics/
    reports/
    gis/
    assistants/            MAE, JACK, NOVA policies and tools
    station_alerts/        separately permissioned, disabled in AWS initially
    nga911/
  tenancy/                 county profile and tenant-isolation enforcement
config/
  counties/
    schema.json            non-secret county profile schema
    synthetic-demo.yaml    safe example only
infrastructure/
  app.py                   CDK entrypoint
  stacks/
    foundation.py          VPC, endpoints, KMS, baseline logging
    county_data.py         database, S3, backup, county key
    county_app.py          ECR/ECS/ALB/autoscaling
    identity.py            Cognito/Verified Permissions
    ai.py                  Bedrock/knowledge/guardrails capability resources
    observability.py
    delivery.py            GitHub/CodePipeline/CodeBuild/CodeDeploy
    control_plane.py       tenant catalog and provisioning workflows
  constructs/              reusable county and provider constructs
tests/
  contracts/               provider and tenant-isolation tests
  infrastructure/          CDK assertions and policy tests
.kiro/
  steering/
  specs/
handoffs/
```

## Provider rule

Application modules consume normalized protocols only. They must not import a
vendor-specific CAD client directly. Each CAD adapter maps vendor data into
versioned normalized call, unit, event, and agency models and declares its
capabilities, such as `search_calls`, `get_call`, `search_units`, `webhooks`,
and `write_messages`.

## Tenant rule

Every request, database session, S3 key, queue message, log dimension, metric,
and AI tool invocation carries an immutable `tenant_id`. A missing tenant is a
hard failure. County configuration contains no secret values.

