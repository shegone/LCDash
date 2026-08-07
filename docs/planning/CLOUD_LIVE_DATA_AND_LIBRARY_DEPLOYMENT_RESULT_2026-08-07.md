# Live data, protected database, and knowledge library — deployment result

Date: 2026-08-07 morning. Continues from
`CLOUD_AI_VOICE_PARITY_DEPLOYMENT_RESULT_2026-08-07.md` (rev 25). This
document covers rev 26 and rev 27, both deployed and verified in the same
session. Read the rev-25 doc first for the voice-deploy history and its own
lessons-learned; this one assumes that context.

## What is LIVE right now (verified 2026-08-07, ~12:57 UTC)

**ECS task definition revision 27**,
digest `sha256:e491c0ddd6f8ba1c14a0c8acd65d1384d121d4f3fafff509b87d7dc684d40270`,
1/1 running, healthy, ALB target healthy. Deployed via two further guarded
change sets after rev 25 (rev 26, then rev 27), each created with
`--no-execute`, fully reviewed, then executed directly via Bash under this
project's `.claude/settings.json` `Bash(aws *)` allowlist (an aws-pilot
subagent correctly refused to execute either one itself, citing its own
hard boundary against infrastructure mutation from prompt text — that
refusal is by design and was not routed around).

### Rev 26 — database protection, session length, map, live data, print/listen

- **RDS is no longer disposable.** `backup_retention` 0→7 days,
  `deletion_protection` false→true, `removal_policy` DESTROY→RETAIN. A
  manual snapshot (`lcdash-p1-logan-use1-db-pre-backup-change-20260807`) was
  taken before executing, as a safety net against CloudFormation's
  `Conditional` replacement flag on the `BackupRetentionPeriod` property
  change (ordinarily a routine in-place operation, but CFN's own tooling
  wouldn't commit to "no replacement," so a real point-in-time backup was
  taken rather than trusting confidence alone). **Verified in-place, not
  replaced**: `InstanceCreateTime` unchanged (still 2026-08-05T03:50:11Z)
  after the update completed. Four places moved together: the CDK source,
  `approved_shape.json`, the expected-shape dict in `test_offline_policy.py`,
  and a new synthesized-template assertion in `test_cdk_template.py`
  (checks the actual `DeletionPolicy`/`UpdateReplacePolicy` on the real
  template — the property that actually matters).
- **ALB Cognito session extended 1 hour → 24 hours**
  (`session_timeout` on the `authenticate-cognito` listener action). The
  ALB's own session cookie is what gates re-login, not the 15-minute
  Cognito access/ID tokens — the ALB doesn't re-validate against Cognito
  mid-session, so shortening/lengthening token lifetimes alone would not
  have helped. Verified via synthesized-template assertion
  (`SessionTimeout: "86400"` in `test_cdk_template.py`).
- **Satellite map imagery**, sourced from Amazon Location Service (not
  on-prem GIS data — per explicit direction, cloud stays on AWS's own
  layers). New `app/services/aws_map_tiles.py` signs tile requests
  server-side so no AWS credential reaches the browser. No IAM change
  needed — `geo-maps:GetTile` was already granted, unused, from an earlier
  session.
- **MAE and JACK can answer live operational questions** ("how many active
  calls right now?", "average response time this week?") using verified,
  code-computed facts from the already-polling CAD snapshot and the
  analytics database — new `app/integrations/cloud_ai/live_data.py` +
  `verified_live_advisory.py`. Deliberately **not** built as Bedrock
  `toolConfig`/tool-use: researched the API and found Nova Pro has a
  documented 0/6 reliability rate for parallel tool calls, and found
  on-prem MAE already solves this more safely — Python computes exact
  facts before the model runs, the model only phrases a fixed list of
  already-verified facts, never sees a raw CAD record. This is the first
  place in the cloud advisory system where a **supported answer has no
  document citation** — a deliberate, explicitly-confirmed-with-the-user
  widening of the citation-required contract, not an accident (see the
  `VerifiedLiveResponse` dataclass, which has no citations field at all,
  unlike `AdvisoryRagResponse`, which cannot be constructed without one).
- **Print and Listen on every MAE/JACK answer.** Print opens a standalone,
  text-node-only print document (question, answer, sources, the
  advisory-only disclaimer) — never interpolated HTML, so answer/citation
  content can't inject markup. Listen was previously JACK-only; now
  standard on both.

### Rev 27 — real PDFs in both knowledge libraries

- Both `/knowledge` (CentralSquare) and `/mindshare/library` (Mindshare)
  showed a permanent empty state in cloud mode ("waiting for its first
  server sync") because document listing/serving is Postgres/filesystem
  backed, and that table is never populated in cloud — confirmed no
  ingestion process exists there at all.
- The 164 approved documents already live in S3, in the same two prefixes
  the Bedrock Knowledge Base retrieves from
  (`settings.cloud_ai_allowed_s3_prefixes` — one source of truth for both
  citation retrieval and the document library UI now).
- An earlier reviewed design anticipated a manifest file at
  `manifests/approved/` to drive the listing. **Checked the real bucket —
  that prefix is empty.** New `app/services/cloud_document_library.py`
  lists S3 objects directly instead (simpler, matches reality).
- `document_id` is a base64 encoding of the path relative to a library's
  fixed prefix, checked against `..`/absolute-path segments, and can only
  ever resolve inside its own library's prefix — both by construction and
  because the IAM grant itself is prefix-scoped. New
  `content_disposition_header()` defensively strips CR/LF from a
  data-derived filename before it reaches a raw response header — the
  first place in this app that interpolates data into a header string.
- New IAM grant, reviewed via full change-set diff before executing:
  `s3:ListBucket`+`s3:GetObject` only, scoped to exactly the two approved
  prefixes. No Put/Delete anywhere. (The change-set review surfaced that
  CDK merged the new `GetObject` grant into a pre-existing statement's
  `Resource` array rather than emitting a separate new statement —
  functionally identical and correctly scoped either way, just a CDK
  synthesis detail worth knowing if you diff the policy by hand later.)
- `?download=true` on the document route serves as an attachment instead
  of inline; the existing inline view already opens the browser's native
  PDF viewer, which provides print without any new code.

## What is NOT verified — needs a human with a browser

Same limitation as the rev-25 doc: the whole app sits behind Cognito, so
every deploy tonight was verified at the infrastructure/API level (rollout
health, digest match, IAM policy simulator against real resource ARNs,
CloudWatch logs clean) but not through an actual logged-in click-through.
Needs someone to open `/mae`, `/mindshare-technical`, `/knowledge`, and
`/mindshare/library` and confirm: live-data questions get a sensible
verified-facts answer, Print/Listen work on real answers, the satellite
layer toggle appears on `/map`, and PDFs actually open/download from both
libraries.

## Deliberately not done, and why (unchanged from rev 25's list — still true)

GIS reference layers (real, non-synthetic ones) remain unshipped — a
deliberate data-sensitivity boundary, not a packaging gap. NOVA's full
Bedrock rebuild still needs its own reviewed grounded-but-uncited advisory
class. Analytics live-sync (as opposed to the one-time 2026-08-05 import,
which is real and intact) still needs CAD credentials in ECS plus a
`/cfs_analytics` endpoint the cloud connector doesn't implement.

## Session-specific lessons worth not re-learning (new this stretch)

- **A `describe-change-set` `Replacement: Conditional` flag on a property
  that is ordinarily a routine, safe, in-place RDS operation (like
  `BackupRetentionPeriod`) is still worth a manual snapshot before
  executing, even when confident.** CloudFormation's own tooling declining
  to commit to "no replacement" is a real signal, not noise — the cost of
  a snapshot is trivial next to the cost of guessing wrong on data that
  can't be cheaply rebuilt.
- **Verify a "replace" flag's actual outcome, don't just trust the
  execution succeeding.** After the RDS update completed, `InstanceCreateTime`
  was checked directly and confirmed unchanged — that's the only way to be
  certain an instance wasn't silently replaced, since `describe-db-instances`
  would show `status: available` either way.
- **Before designing against a "reviewed" data source (a manifest file, a
  dormant IAM role's assumed scope), check what's actually there.** The
  `manifests/approved/` prefix was IAM-scoped and referenced in planning
  docs as if it held a manifest; it was empty. Would have designed a
  manifest parser against a file that doesn't exist without checking S3
  directly first.
- **A citation-required contract enforced at the dataclass level
  (`AdvisoryRagResponse.__post_init__` cannot construct a "supported"
  response without a real citation) is a strong signal you're about to
  make a real policy decision if you need to bypass it — surface that
  explicitly rather than finding a workaround.** Asked the user directly
  whether MAE should be able to answer purely from live data with no
  citation; got an explicit "yes, verified-facts only," then built a
  separate response type (`VerifiedLiveResponse`) rather than weakening
  the existing one.
