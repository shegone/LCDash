# Private Bedrock knowledge-search cost approval card — 2026-08-05

## Decision requested

Approve the parser, budget limits, and metadata policy below for the exact
164-document private library. This card does **not** itself authorize an AWS
write, ingestion, model invocation, or live-service activation. The documented
privacy/security and provisioning gates still have to be completed in writing.

## Recommended parser

Use Amazon Nova Lite:

`arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0`

Read-only checks on 2026-08-05 confirmed that this exact model is active,
authorized for the sandbox account, and available for on-demand use in
`us-east-1`. It accepts text and images and returns text. That makes it the
lowest-cost sensible choice for preserving tables, figures, headings, and page
structure in this mixed PDF library. No model was invoked during this check.

AWS documents that advanced Knowledge Base parsing supports Amazon Nova vision
models and charges for parser input and output tokens. See [Advanced parsing
options](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-advanced-parsing.html)
and [models and Regions supported by Knowledge Bases](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-supported.html).

## Exact aggregate library census

- 164 documents: 162 PDFs and 2 Markdown files
- 251,295,872 source bytes (0.251 GB decimal; 239.65 MiB)
- 3,649 PDF pages, counted locally with no document upload or AWS call
- 2,099 deterministic local review chunks under the current preparation method
- Source scope remains the already approved two-prefix manifest only

No private document text, title, path, or raw payload is reproduced here.

## Cost recommendation

Approve these two hard operating limits:

- **One-time ingestion ceiling: $25.00.** Stop before ingestion and return for
  approval if the refreshed estimate could exceed this amount.
- **Monthly citation-only ceiling: $5.00, with at most 10,000 retrieval queries
  per month.** Alert at 50%, 80%, and 100%; stop new citation-search traffic at
  the ceiling until an owner approves a change.

The likely costs are materially below those ceilings:

| Item | Planning estimate | Basis |
|---|---:|---|
| Nova Lite advanced parsing | about $1.30 once | Conservative document-analysis proxy of 2,900 input and 750 output tokens per PDF page, at $0.06/M input and $0.24/M output tokens; actual parser token use controls the bill |
| Titan Text Embeddings V2 for initial chunks | about $0.02 once | 2,099 chunks capped at 300 tokens, rounded upward; $0.02/M input tokens |
| S3 Vectors initial write | less than $0.01 once | Roughly 2,099 vectors at 1,024 float32 dimensions plus bounded metadata |
| KMS key | about $1.00/month | One customer-managed key; request charges are expected to be negligible at this scale |
| S3 Vectors storage | less than $0.01/month | The vector data is only on the order of tens of MB after allowing for metadata |
| 10,000 monthly retrievals | about $0.25 plus negligible vector scan/return charges | Conservative 1,000 embedding-input tokens per query plus S3 Vectors request pricing |
| **Expected steady state** | **about $1.25/month** | Citation retrieval only; no answer-generation model |

The $25 ceiling deliberately leaves room for parser-token variance, semantic
chunking variance, metadata, retries, and small AWS request charges. The $5
monthly ceiling is a budget control, not a forecast. It excludes existing ECS,
source-S3, logging, and network costs that are not created by this knowledge
search package. Prices must be refreshed immediately before any authorized
write or ingestion.

Pricing references: [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/),
[Titan Text Embeddings V2 pricing example](https://aws.amazon.com/blogs/machine-learning/get-started-with-amazon-titan-text-embeddings-v2-a-new-state-of-the-art-embeddings-model-on-amazon-bedrock/),
[Amazon S3 and S3 Vectors pricing](https://aws.amazon.com/s3/pricing/), and
[AWS KMS pricing](https://aws.amazon.com/kms/pricing/).

## Metadata recommendation

Before ingestion, admit a reviewed Bedrock sidecar metadata file for every
source object and bind it to a new exact manifest. Do not add or modify those
objects under this read-only package.

Recommended filterable fields:

- `tenant_id`: fixed to the approved Logan County tenant identifier
- `library`: `mindshare` or `centralsquare`
- `approved`: boolean and required to be `true`
- `current`: boolean so superseded manuals can be excluded
- `sensitivity_class`: approved classification from the privacy review
- `revision_date_epoch`: optional numeric date when reliably known

Keep citations human-readable with document title, revision, page, and section,
but keep metadata small. Do not place private source text, credentials, raw CAD
data, caller/patient/person data, or operational payloads in metadata. Reserve
the S3 Vectors non-filterable-key list for Bedrock's required
`AMAZON_BEDROCK_TEXT` and `AMAZON_BEDROCK_METADATA` fields. S3 Vectors limits
custom metadata to 1 KB and 35 keys per vector, so avoid speculative fields and
high-cardinality filters. See [Using S3 Vectors with Amazon Bedrock Knowledge
Bases](https://docs.aws.amazon.com/AmazonS3/latest/userguide/s3-vectors-bedrock-kb.html).

## Exact confirmation text

Only send the following text if the named data owner and privacy/security owner
can truthfully make every statement. If any statement is uncertain, the gate
remains closed and the affected files require a separate redaction/review path.

> I confirm that the exact 164-document manifest (162 PDFs and 2 Markdown files,
> 251,295,872 bytes) is the approved source set and contains no PII, PHI,
> CJIS/CJI, caller, patient, or person records, credentials, private keys,
> protected operational details, or other regulated data. I approve Amazon Nova
> Lite parser ARN
> arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0, semantic
> chunking at maximum 300 tokens with buffer size 1 and breakpoint percentile
> 95, and the metadata policy in
> PRIVATE_BEDROCK_KB_COST_APPROVAL_CARD_2026-08-05.md. I approve a hard one-time
> ingestion ceiling of $25.00 and a hard monthly citation-only ceiling of $5.00
> with no more than 10,000 retrieval queries per month. This confirmation
> completes only the documented parser, cost, metadata, and Outcome A privacy
> decisions. It does not authorize AWS resource creation, document ingestion,
> model invocation, generated answers, or activation in the running service.

After that confirmation, the orchestration manager must separately record the
remaining authorization gate and exact scope before any AWS resource is
created. Ingestion and citation-search activation remain later, independent
gates.
