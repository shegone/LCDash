---
inclusion: always
---

# Technology direction

## Application

- Python 3.13, FastAPI, Jinja2, JavaScript, and the existing test suite remain
  the initial application stack.
- AWS CDK in Python is the infrastructure-as-code standard for this workspace.
- Container images are stored in Amazon ECR.
- Long-running web and worker processes run on Amazon ECS. Fargate is the
  default; EC2 capacity providers are allowed only for workloads that require
  GPUs or unsupported host capabilities.
- Aurora PostgreSQL-Compatible or RDS for PostgreSQL is the authoritative
  operational analytics store. Select the engine and sizing per environment.

## Managed AWS capability providers

- AI: Amazon Bedrock through a provider interface; model IDs are configuration.
- Agent hosting, tools, identity, and observability: evaluate Amazon Bedrock
  AgentCore after the basic Bedrock provider and read-only tools are proven.
- Knowledge: S3 plus a replaceable retrieval provider. Prefer Bedrock Managed
  Knowledge Bases where region and compliance requirements permit; retain an
  Aurora pgvector or OpenSearch-compatible fallback.
- Speech recognition: Amazon Transcribe streaming/batch provider.
- Speech generation: Amazon Polly provider. Preserve the existing Qwen voice
  provider as an optional GPU-backed profile when voice parity is required.
- GIS: Amazon Location Service for base maps, geocoding, routing, and optional
  geofencing, layered with each county's authoritative GIS data.
- Configuration and product profiles: AWS AppConfig.
- Secrets: AWS Secrets Manager with county-specific KMS keys.
- Identity: Amazon Cognito federation and Amazon Verified Permissions, with
  the county tenant and role carried in every authorization decision.
- Events: EventBridge, SQS, and Step Functions for asynchronous workflows;
  scheduled collectors use EventBridge Scheduler and bounded ECS tasks.
- Observability: CloudWatch, X-Ray/ADOT, CloudTrail, Config, GuardDuty,
  Security Hub, Inspector, and appropriately scoped alarms.
- Recovery: AWS Backup, database point-in-time recovery, S3 versioning, and
  tested restore procedures.

## Delivery

- GitHub remains the source repository.
- GitHub OIDC may run CI and request short-lived AWS roles. Trust policies must
  be restricted to the exact organization, repository, branch, and protected
  GitHub environment.
- AWS CodePipeline/CodeBuild/CodeDeploy may perform controlled deployments from
  GitHub to ECR and ECS with blue/green verification and automatic rollback.
- Do not store long-lived AWS keys in GitHub or Kiro.

## Portability

- Never hardcode `arn:aws`; use partition-aware CDK tokens.
- Keep regional feature availability behind capability flags.
- Every managed provider must have a stable application interface and a test
  double so a county can choose an alternative service without forking code.

