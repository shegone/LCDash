# Bedrock PDF RAG and interactive voice readiness plan

> Superseded for the private 164-document KB design by
> `PRIVATE_BEDROCK_KB_RAG_READINESS_2026-08-05.md`. In particular, the verified
> source has two exact prefixes and the reviewed S3 Vectors candidate uses
> semantic 300/1/95 chunking, not the earlier hierarchical proposal.

## Decision and current readiness

Status: **PLAN ONLY - RAG CREATION BLOCKED**.

Read-only checks on 2026-08-05 used account `862772137583`, region
`us-east-1`, and profile `lcdash-sandbox-admin`. No model was invoked and no
resource was created or changed.

- `amazon.titan-embed-text-v2:0`, `amazon.nova-micro-v1:0`, and
  `amazon.nova-lite-v1:0` report agreement available, authorization
  `AUTHORIZED`, entitlement available, and region available.
- Titan Text Embeddings V2 is active for on-demand text embeddings. Nova Micro
  and Nova Lite are active for on-demand inference; US inference profiles also
  exist, but the pilot should start with the single-region base model to keep
  processing in `us-east-1` and the IAM policy simple.
- S3 Vectors, Amazon Transcribe, and Amazon Polly control-plane calls succeed.
  No vector bucket is currently visible.
- The private source bucket
  `lcdash-p1-logan-use1-862772137583-document-library` is in `us-east-1` and
  uses SSE-S3 AES-256. Its last authorized empty-state verification found zero
  objects; this assessment did not list keys or read content.
- Current en-US neural Polly choices include Matthew, Stephen, Gregory, Kevin,
  Justin, and Joey (male), and Joanna, Ruth, Danielle, Salli, Kimberly, Kendra,
  and Ivy (female). Start with Matthew and Joanna for a matched, selectable
  neural pair. Voice is a user preference, never an authorization boundary.

The lowest-complexity managed vector store for the USD 200 monthly pilot is
**S3 Vectors**. It is serverless, requires a dedicated vector bucket and index,
and avoids the standing collection or database footprint of OpenSearch
Serverless or Aurora PostgreSQL. Use on-demand Bedrock inference only; do not
buy provisioned throughput. Add budget alarms and per-feature usage metrics
before activation. Pricing must be rechecked at the authorization gate.

Current AWS references: [S3 Vectors](https://aws.amazon.com/s3/features/vectors/),
[S3 pricing](https://aws.amazon.com/s3/pricing/),
[Bedrock pricing](https://aws.amazon.com/bedrock/pricing/), and
[Bedrock advanced parsing](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-advanced-parsing.html).
AWS describes S3 Vectors as cost-optimized for infrequent vector access, while
advanced PDF parsing adds per-page or model-token charges. Actual compliance
with the USD 200 cap therefore depends on admitted pages, vector count, query
volume, answer tokens, audio minutes, and existing pilot spend.

## Hard blockers before creating RAG

1. **No approved documents exist in the source bucket.** Bedrock Knowledge Base
   creation and ingestion must not start with an empty source. A signed
   per-object admission manifest must complete the existing malware, secret,
   ownership, copyright, retention, sensitivity, hash, and human-review gates.
2. **PII/PHI decision is unresolved.** The data owner and privacy/security owner
   must state in writing whether any admitted document contains PII, PHI,
   criminal-justice information, protected operational details, or other
   regulated data. Default is `NO SENSITIVE DATA AUTHORIZED`. If any is present,
   stop for pre-ingestion redaction, legal/compliance review, role-based metadata
   filtering, customer-managed KMS decisions, logging controls, retention and
   deletion rules, breach response, and an explicit approval. Response masking
   does not remove sensitive text already stored in source files or vectors.
3. The knowledge-base name, source prefixes, embedding dimensions, vector
   bucket/index names, metadata schema, chunking choice, parser model/cost,
   ingestion window, and rollback owner need approval. Chunking cannot be
   changed after data-source creation without recreating the data source.
4. A representative, sanitized PDF test set must include narrative manuals,
   multi-column pages, revision tables, procedures, and complex tables. It needs
   retrieval-quality acceptance questions and page-level expected citations.
5. The application must implement tenant-bound retrieval, citation rendering,
   deny-by-default answers, sanitized telemetry, timeouts, and a text-only
   fallback before voice is enabled.

## Proposed PDF RAG architecture

Use one Bedrock Knowledge Base with one S3 data source scoped only to the five
approved document-library prefixes. Create a separate S3 Vectors vector bucket
and one index for `logan-synthetic`; do not store vectors in the ordinary
document bucket. Use Titan Text Embeddings V2 with an explicitly chosen
dimension that exactly matches the vector index; prefer its standard 1024
dimension unless a measured pilot justifies a smaller supported dimension.

For technical manuals and procedures, use **hierarchical chunking plus advanced
FM-based parsing**. Preserve headings, numbered steps, warnings, table headers,
rows, captions, page numbers, document title, revision, and supersession data.
Start the evaluation with approximately 1,200-token parent sections and
300-token child chunks with modest overlap, then tune from measured retrieval
results. Do not accept default parsing for complex tables or figures. OCR/image
PDF handling, parser model choice, and parser cost require separate validation.

Every admitted object should have sidecar metadata for tenant, library, document
owner, title, document type, publication/revision date, current/superseded state,
sensitivity class, retention class, approval identifier, and SHA-256. Retrieval
must filter `tenant=logan-synthetic`, `approved=true`, `current=true`, and the
caller's allowed library/sensitivity classes before ranking.

The application should use **Retrieve, then Converse** rather than opaque
Retrieve-and-Generate. Retrieve a small bounded result set, discard results below
the tested relevance threshold, build a citation-constrained prompt, and call
Nova Micro on demand with an explicit small `maxTokens`. Use Nova Lite only when
evaluation proves Micro inadequate for table-heavy or multi-section questions.
The answer must cite document title, revision, page/section, and source object
reference for every material claim.

If retrieval has no approved supporting passage, citations are missing, tenant
or sensitivity metadata is absent, a document is superseded, or the request asks
for an operational action, answer that the approved library does not support the
request. Never fill gaps from general model knowledge as though it came from a
manual. The assistant is advisory and read-only: no CAD update, acknowledgement,
dispatch, message, page, alert, tone, station-alert release, public warning,
ESInet/radio action, or other operational output is permitted.

## Least-privilege IaC plan

Create these only after the blockers and a separate authorization gate clear:

1. **Dedicated S3 Vectors bucket and index** using the approved encryption and
   deletion posture. No public access and no cross-tenant index.
2. **Bedrock Knowledge Base service role**, trusted only by
   `bedrock.amazonaws.com`, with `aws:SourceAccount=862772137583` and a
   `SourceArn` limited first to the account/region KB pattern, then tightened to
   the created KB ID. Its permissions are limited to:
   - `s3:ListBucket` with the five approved prefixes and `s3:GetObject` only
     beneath them;
   - `bedrock:InvokeModel` only for the exact Titan embedding model ARN;
   - `s3vectors:PutVectors`, `GetVectors`, `DeleteVectors`, `QueryVectors`, and
     `GetIndex` only on the dedicated vector index ARN;
   - KMS permissions only if a separately approved customer-managed key is used.
3. **Application runtime policy**, attached to the existing ECS task role only
   in a separately reviewed foundation update. Grant `bedrock:Retrieve` on the
   one KB and `bedrock:InvokeModel` only on the selected Nova base model. Do not
   grant ingestion, KB mutation, vector writes, S3 writes, or `bedrock:*`.
4. **Ingestion operator role**, separate from the application, limited to the
   exact KB/data source ingestion operations and read-only job status. Source
   upload permission remains a different human-controlled workflow.

No new classic Bedrock Agent is needed. The product needs bounded retrieval and
generation, not an autonomous action agent.

## Interactive male/female voice plan

Keep text authoritative and voice optional:

1. The authenticated browser captures push-to-talk audio and sends a bounded
   stream to the existing application session. Do not persist raw audio by
   default.
2. The backend uses Amazon Transcribe streaming for `en-US`, applies explicit
   duration/size/time limits, and accepts only the final transcript. Never use a
   transcript as an operational command.
3. The transcript follows the same tenant authorization, retrieval, citation,
   and deny-by-default RAG path as typed text.
4. The answer text is always displayed first. Polly synthesizes only that
   already-approved answer with the user's selectable voice: Matthew (male) or
   Joanna (female), neural engine. Preserve the pronunciation rule that `911`
   is spoken as "nine one one." Audio failure falls back to text without retry
   loops that delay the interface.
5. Do not retain input audio, transcripts, or synthesized audio unless a later
   data-owner retention decision explicitly authorizes it. Log only request ID,
   tenant, service status, latency, bounded usage counts, selected non-sensitive
   voice ID, and sanitized failure category.

The runtime role needs only `transcribe:StartStreamTranscription` and
`polly:SynthesizeSpeech`, region-limited to `us-east-1`; these APIs do not offer
useful per-resource scoping, so do not add unrelated Transcribe/Polly actions.
Browser audio transport, consent/notice, microphone permission, accessibility,
retention, concurrent-session limits, and cost caps require tests and approval.

## Acceptance sequence after authorization

1. Admit a small sanitized document set and confirm the signed manifest.
2. Create the vector bucket/index, service roles, KB, and S3 data source through
   reviewed CDK; verify the synthesized IAM and resource inventory before write.
3. Ingest once, wait for completion, and reconcile processed/failed counts to
   the approved manifest.
4. Run retrieval-only tests for citations, tables, revision filtering,
   cross-tenant denial, sensitive-metadata denial, irrelevant queries, and no
   document-content logging.
5. Enable bounded Nova generation only after retrieval passes; evaluate factual
   support and refusal behavior. No model invocation is authorized by this plan.
6. Add text-only UI, then Transcribe push-to-talk, then Polly voice selection.
   Each layer retains the prior fallback and receives its own cost/latency tests.

This plan authorizes none of those creation, ingestion, invocation, or activation
steps.
