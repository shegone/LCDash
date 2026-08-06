# Private Bedrock KB/RAG readiness — 2026-08-05

## Decision

Status: **IMPLEMENTATION READY; PROVISIONING AND INGESTION NOT AUTHORIZED**.

The local design, fail-closed application wiring, and standalone CDK template
are ready for a later reviewed provisioning gate. This document authorizes no
AWS write. Do not create the vector bucket/index, KMS key, IAM role, knowledge
base, or data sources; do not start ingestion; do not call Retrieve,
RetrieveAndGenerate, Converse, or InvokeModel; and do not update the live ECS
service until the explicit gates below are complete.

The 164 admitted private objects already present in the approved S3 source
prefixes remain the only source set: 131 Mindshare and 33 CentralSquare. All
previous exclusions and holds remain excluded. No CAD data is a knowledge-base
source and the RAG path has no CAD or operational tools.

This decision supersedes the older five-prefix/hierarchical proposal in
`BEDROCK_RAG_VOICE_READINESS_PLAN_2026-08-05.md`. The verified upload has two
exact prefixes, and current AWS guidance does not recommend hierarchical
chunking with S3 Vectors because parent/child context consumes constrained
vector metadata.

## Architecture frozen for provisioning review

1. One standalone, termination-protected CloudFormation stack named
   `lcdash-p1-logan-use1-knowledge-search`; it has no reference from the live
   foundation stack.
2. One customer-managed KMS key with rotation, retain policy, and a 30-day
   pending-deletion window for vector storage and ingestion-transient data.
3. One private S3 Vectors bucket and one tenant-specific index using `float32`,
   1,024 dimensions, cosine distance, and Titan Text Embeddings V2
   (`amazon.titan-embed-text-v2:0`). The dimensions exactly match the index.
4. One Bedrock Knowledge Base and two S3 data sources, each restricted to one
   exact approved prefix. `DataDeletionPolicy=RETAIN` prevents an accidental
   source/data-source deletion from silently erasing vectors.
5. Advanced FM parsing preserves headings, numbered procedures, warnings,
   table rows/headers, captions, page references, revisions, and supersession
   language. The exact supported parser model ARN is a deployment parameter and
   must be rechecked for support and cost at the later gate.
6. The application path uses `bedrock-agent-runtime:Retrieve` only. It performs
   semantic search, keeps at most five results, rejects scores below 0.5,
   rejects every source URI outside the two exact prefixes, and returns only
   retrieved excerpts with source citations. It has no generation-model call.
7. A future generated-answer layer, if ever approved, is a separate gate. It
   must never present uncited model knowledge as an approved manual answer.

## Irreversible chunking choice

The provisioning candidate is frozen as **semantic chunking: maximum 300
tokens, buffer size 1, breakpoint percentile 95, with advanced FM parsing**.

This selection is intentional for the mixed long-form manuals, procedures,
revision material, and tables, while avoiding the S3 Vectors metadata pressure
of hierarchical parent/child chunks. It is irreversible on a created Bedrock
data source: changing it requires deleting and recreating each data source and
performing a complete re-ingestion/reconciliation. Therefore the operator must
record written approval of these four values and the parser model ARN before
creating either data source. `DataDeletionPolicy=RETAIN` also means a recreation
plan must explicitly handle retained old vectors to prevent stale or duplicate
retrieval.

## Required PII/PHI decision — blocking user action

Before any ingestion, the data owner and privacy/security owner must sign one,
and only one, of these outcomes for the exact 164-object manifest:

- **Outcome A — no regulated/sensitive content authorized:** “The admitted
  objects contain no PII, PHI, CJIS/CJI, caller/patient/person records,
  credentials, private keys, protected operational details, or other regulated
  data. The exact manifest is approved for private Bedrock ingestion.”
- **Outcome B — sensitive content identified:** list every affected object and
  data category, stop ingestion, create redacted derivative objects under a new
  reviewed prefix, repeat malware/secret/hash/human review, define authorized
  roles and metadata filters, approve KMS/logging/retention/deletion/breach
  controls, and sign a new exact manifest. The unredacted originals must not be
  ingested.

Silence, uncertainty, or a mixed answer means **not authorized**. Response
masking is insufficient: raw retrieved references can contain source text and
guardrails do not sanitize those raw chunks. Application logs must never record
queries, retrieved passage text, full provider responses, or source-document
content.

## Least-privilege permission review

Knowledge Base service role:

- Trust only `bedrock.amazonaws.com`, with exact account `862772137583` and
  `SourceArn` restricted to `us-east-1` knowledge bases; tighten to the created
  KB ARN after creation.
- `s3:ListBucket` only with the two exact prefix conditions and `s3:GetObject`
  only beneath those prefixes; no S3 write or delete.
- `bedrock:InvokeModel` only for Titan Text Embeddings V2 and the exact approved
  advanced-parser model ARN, with `aws:RequestedRegion=us-east-1`.
- `s3vectors:PutVectors`, `GetVectors`, `DeleteVectors`, `QueryVectors`, and
  `GetIndex` only on the dedicated index ARN.
- KMS encrypt/decrypt/data-key permissions only on the dedicated key.

Future application runtime policy (not in this stack and not authorized now):

- `bedrock:Retrieve` only on the exact created KB ARN.
- No `StartIngestionJob`, KB/data-source mutation, vector writes, S3 document
  reads/writes, `RetrieveAndGenerate`, `InvokeModel`, or wildcard Bedrock access
  for the citation-only release.

Ingestion operator policy (separate human-controlled gate):

- Exact KB/data-source read and `StartIngestionJob` permissions only.
- No source upload permission and no application-runtime attachment.

CloudTrail management events cover resource creation and ingestion management.
Before retrieval activation, separately enable Bedrock Knowledge Base data-event
auditing with bounded retention; retrieval calls are data events and are not
logged by default. Telemetry must remain metadata-only.

## Cost guardrail

Pricing is time-sensitive and must be refreshed immediately before the write
gate. Current official AWS pricing checked on 2026-08-05 gives these planning
inputs for `us-east-1`:

- Titan Text Embeddings V2: USD 0.02 per million input tokens.
- S3 Vectors: USD 0.06/GB-month storage and USD 0.20/logical GB PUT; query
  charges include USD 2.50 per million requests, processed-data tiers beginning
  at USD 0.004/TB for indexes up to 100,000 vectors, and USD 0.01/GB returned
  after the per-query free allowance.
- The 2,099-review-chunk count implies about 8.2 MB of vector values alone at
  1,024 float32 dimensions, before metadata and keys. At 300 tokens per chunk,
  the conservative 629,700-token embedding bound is about USD 0.013 for the
  initial embeddings. These are directional bounds, not a quote; Bedrock may
  create a different chunk count after advanced parsing.
- Advanced FM parser cost is model-token based and cannot be approved until the
  exact parser model and page/token estimate are recorded. KMS key/API charges,
  ordinary S3 source storage/requests, CloudTrail data events, and future query
  embeddings are additional.

No provisioned throughput is allowed. Before provisioning, create a written
estimate from admitted page count, parser input/output tokens, expected vector
count/metadata size, ingestion frequency, and monthly retrieval queries. Reserve
headroom inside the existing USD 200 pilot budget and define a stop threshold.

Official references: [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/),
[S3 pricing](https://aws.amazon.com/s3/pricing/),
[S3 Vectors with Bedrock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-bedrock-kb.html),
[Bedrock KB permissions](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-permissions.html),
and [Bedrock chunking](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-chunking.html).

## Exact provisioning checklist — do not execute without authorization

- [ ] Record Outcome A or complete Outcome B above with both required owners.
- [ ] Reconcile the remote 164-object key/size/SHA-256/metadata manifest again;
      confirm 131 Mindshare, 33 CentralSquare, no extras, and all holds absent.
- [ ] Approve semantic `300 / 1 / 95`, its recreation consequence, and retained
      vector cleanup procedure.
- [ ] Select a currently supported advanced-parser model ARN in `us-east-1`;
      record its price and representative page/token estimate.
- [ ] Decide whether Bedrock sidecar metadata is required for title, revision,
      approval ID, sensitivity, and current/superseded filtering. If required,
      generate and admit it through a new exact manifest before ingestion.
- [ ] Refresh Titan, S3 Vectors, parser, KMS, and CloudTrail pricing; approve a
      monthly query limit and budget stop threshold.
- [ ] Run local unit/contract tests and standalone CDK synth; validate the
      synthesized CloudFormation template and retain its SHA-256.
- [ ] Review the change set: only the dedicated KMS, S3 Vectors, IAM, Bedrock KB,
      two data sources, outputs, policies, and tags may appear. Abort on any live
      foundation, ECS, CAD, database, network, Cognito, or source-bucket change.
- [ ] With explicit provisioning authorization, create the standalone stack.
      Do not attach the application runtime and do not start ingestion.
- [ ] Confirm vector bucket/index encryption, 1,024 dimensions, cosine distance,
      policies, KB role trust, exact prefixes, and data-source chunking/parser.
- [ ] Tighten the service-role trust from `knowledge-base/*` to the exact KB ARN
      through a second reviewed change set.
- [ ] Obtain a separate explicit ingestion authorization naming the KB ID, both
      data-source IDs, manifest hash, window, operator, and rollback owner.
- [ ] Start one ingestion job per data source, poll to `COMPLETE`, and reconcile
      processed/failed counts. Stop on any skipped, extra, or failed document.
- [ ] Run retrieval-only acceptance tests: expected citations, tables/revisions,
      wrong tenant, outside-prefix source, low score, irrelevant question, and
      no-content logging. Do not call a generation model.
- [ ] Only after retrieval acceptance, separately review an exact KB-scoped
      runtime-policy and application deployment change set.

## Test evidence

- Focused application retrieval/provider/runtime/config/startup contracts: 24
  passed; no network call occurred in the focused tests.
- Standalone knowledge-search infrastructure tests: 4 passed.
- Python compilation and scoped whitespace/error checks: passed.
- Fresh standalone synth: passed. Template SHA-256:
  `a0988ab81b45c9fa6d1495cda61cad211818aa54358ade885cbbbe3043dce8e4`.
- AWS CloudFormation `validate-template`: passed with only
  `AdvancedParserModelArn` and the CDK bootstrap parameter.
- Full application contract discovery ran 238 tests and exposed three existing
  failures plus two environment errors outside this KB package: a county
  hardcoding invariant already contradicted by existing cloud pilot code, two
  pre-existing Units route-shape expectations, and two missing Windows `tzdata`
  errors. The 24 focused KB/cloud-AI contracts passed in that same workspace.
- Full infrastructure discovery ran 122 tests: 117 passed, one skipped, three
  stale expectations for the already-authorized live CAD/container state failed,
  and one unrelated analytics test could not import optional `cryptography`.
  The dedicated four-test knowledge-search suite passed.
