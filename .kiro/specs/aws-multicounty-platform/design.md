# Design: AWS Multi-County LCDash Platform

## 1. Recommended tenancy model

Use a shared control plane with siloed county application cells.

The control plane owns county onboarding, product profiles, deployment status,
version inventory, aggregate service health, billing tags, and approved
cross-county metrics. It does not store operational CAD records or county
credentials.

Each county cell owns its application services, CAD adapter, secret, KMS key,
database, object storage, queues, logs, backups, identity bindings, and module
configuration. The first sandbox may place one synthetic Logan cell in the
current AWS account. It is a single-county development environment and cannot
be used as evidence of account-level multi-county isolation. The long-term
template supports a county workload account created through AWS Organizations
and Control Tower.

## 2. Account and environment layout

Target landing-zone structure:

```text
Management account (no workloads)
Security OU
  Log Archive
  Audit/Security Tooling
Infrastructure OU
  Shared Delivery and Container Registry
Sandbox OU
  LCDash AWS development
Workloads-Test OU
  County acceptance cells
Workloads-Prod OU
  One county workload account per required isolation boundary
```

Do not initialize Control Tower or create accounts during the planning phase.
The current commercial account is suitable only for the first synthetic
sandbox after billing access, budgets, MFA, and the account ownership model are
confirmed.

Before the first deployment, an account-boundary decision must identify the
sandbox account owner, permitted workloads, security/logging destinations,
budget owner, and the point at which a county receives a dedicated workload
account. A second real county may not share the first sandbox account.

## 3. County cell architecture

```text
Route 53 / ACM
        |
CloudFront + WAF
        |
Application Load Balancer
        |
ECS service: FastAPI web (private subnets, multi-AZ)
        |-- Aurora/RDS PostgreSQL + RDS Proxy
        |-- S3 county documents/GIS/exports
        |-- AppConfig county profile and feature flags
        |-- Secrets Manager county CAD secret
        |-- Bedrock / Knowledge / Guardrails providers
        |-- Transcribe / Polly / Location providers
        |-- SQS and EventBridge

EventBridge Scheduler -> bounded ECS collector tasks -> normalized database
Dormant webhook option -> WAF/API receiver -> SQS -> idempotent event processor
GitHub -> CodePipeline/CodeBuild -> ECR -> ECS blue/green deployment
```

The live webhook path is not enabled for the first Logan AWS stage. Current
`.227` remains the sole callback owner unless the vendor and operator approve a
different topology. A single-writer/fencing ADR, rollback procedure, vendor
confirmation, and named operator approval are required before the dormant path
can be enabled.

## 4. CAD adapter contract

Define `CadProvider` with normalized, versioned request and response models:

- `health()`
- `search_calls(query, page)`
- `get_call(cfs_number)`
- `search_units(query, page)`
- `normalize_event(source, payload)`
- `capabilities()`

Optional operations such as `register_subscription`, `update_call`,
`send_message`, and `acknowledge` are separate interfaces. They are absent
from a read-only task role and disabled by default.

Read-only enforcement is layered. The application capability registry omits
write interfaces; the adapter task role may read only the county's read-only
secret reference; a vendor-scoped read-only credential is used where the CAD
vendor supports one; outbound destinations are allowlisted; and attempted use
of a denied capability emits an audit event and alarm. IAM alone cannot prevent
an HTTP operation performed with an over-privileged external vendor credential,
so provider contract tests and vendor-side scope are mandatory controls.

The provider receives a `TenantContext` and a secret reference, never secret
values in configuration. Provider contract tests run against synthetic
fixtures. A vendor adapter must pass the same normalization, minimization,
pagination, timeout, rate-limit, redaction, and audit tests before onboarding.

## 5. Module model

Feature modules are independently permissioned and controlled by AppConfig:

- CentralSquare or other CAD Operations
- Analytics and reports
- GIS
- MAE operational assistant
- JACK technical assistant
- Mindshare knowledge
- Station Alerts
- NGA911 Intelligence and NOVA
- Voice and avatar integrations

County profiles enable modules and select providers. A module may not infer
permission from visibility alone; API authorization evaluates tenant, user,
role, module, action, data class, and resource.

`TenantAuthorizationService` is the single policy entry point. Authentication
middleware builds an immutable `TenantContext` from trusted federation claims
and deployment bindings; request bodies and URLs cannot select or override it.
API handlers, repositories, S3 access, queue envelopes, report jobs, caches,
and AI tools all call the same deny-by-default policy contract. Amazon Verified
Permissions is an optional implementation behind that contract, not a required
dependency. Cross-tenant negative tests run against every enforcement layer.

## 6. Data design

Default production isolation is one database cluster per county cell. Every
table still carries `tenant_id` so context errors fail visibly and migrations
remain portable. Lower-cost sandbox environments may use separate logical
databases or schemas, but must pass cross-tenant denial tests.

S3 uses separate county buckets or access points and county KMS keys. Object
keys begin with the tenant identifier. Secrets, operational records, knowledge
documents, GIS, exports, and backups have separate access policies and
retention classes.

Only approved de-identified aggregate metrics may enter a shared regional
analytics plane using S3, Glue/Athena, and QuickSight. Raw CAD stays in the
county cell.

## 7. AI and knowledge design

Introduce these stable application interfaces:

- `InferenceProvider`: Ollama, Bedrock Converse/ConverseStream
- `RetrievalProvider`: local PostgreSQL, Bedrock Knowledge Base, Aurora
  pgvector, or approved search service
- `AgentProvider`: current deterministic router first; AgentCore runtime and
  gateway as an optional managed implementation
- `SpeechToTextProvider`: local Whisper/Parakeet or Transcribe
- `TextToSpeechProvider`: local Qwen/Kokoro or Polly

MAE/JACK tools call the normalized CAD and analytics services; they never hold
vendor credentials. Bedrock Guardrails complement deterministic allowlists and
redaction but do not replace them. AgentCore Gateway, Identity, and
Observability are strong candidates after the tool contracts are stable.
They remain optional until current region, partition, quota, and feature
availability are recorded in the capability registry.

## 8. GIS improvement

Retain authoritative county GIS as private, versioned county data. Use Amazon
Location for managed base maps, geocoding, reverse geocoding, routing, service
areas, and optional map matching. Do not replace county authoritative address,
road, ESB, hydrant, or trail data with commercial map results.

Full address datasets remain server-side. Browser layers receive only
allowlisted fields and geometry appropriate for the requested view.

## 9. Voice and operational output

Commercial `us-east-1` can evaluate Transcribe streaming and Polly generative
streaming for conversational latency. GovCloud support and voice-engine parity
must be evaluated independently. The Qwen voice profile remains an optional
GPU-backed ECS/EC2 capacity-provider deployment if branded voice quality is a
requirement.

No cloud service participates in Logan County's live tone or station-audio
path during the sandbox. Operational ownership is a later failover/cutover
design with a single active writer and explicit fencing.

## 10. Networking and egress

Use a multi-AZ VPC, public ALB only, private ECS/database subnets, VPC endpoints
for supported AWS services, and controlled outbound access. If CentralSquare
requires source allowlisting, route adapter traffic through a stable NAT
Elastic IP or approved egress proxy. Record external destinations in an egress
allowlist.

## 11. Delivery and rollback

Use CDK assertions and security-policy tests before `cdk synth`. Build images
with CodeBuild, scan them, publish immutable digests to ECR, and use ECS
blue/green deployments with a test listener, synthetic smoke checks, canary
traffic, CloudWatch alarms, and rollback. Database migrations use expand/
contract patterns and must remain backward compatible during traffic shift.

## 12. Phased activation

1. Architecture decisions, account boundary, and capability registry.
2. Provider contracts and synthetic local tests.
3. Partition-aware CDK synthesis and policy tests.
4. Approved AWS synthetic sandbox with no vendor secrets.
5. Bedrock, retrieval, speech, GIS, and observability evaluations.
6. Approved CentralSquare credential entry and bounded read-only polling.
7. Functional parity and failure testing.
8. County template onboarding exercise with a second synthetic county.
9. GovCloud gap assessment and production authorization package.
