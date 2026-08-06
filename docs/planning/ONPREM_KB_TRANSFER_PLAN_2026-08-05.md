# On-prem knowledge-base inventory and transfer plan

Status: **COMPLETE METADATA MANIFEST CAPTURED; NO TRANSFER AUTHORIZED**.

An initial server-enforced metadata session was truncated in the client
transcript. A separately authorized repeat redirected the same forced-command
stdout directly to `work/onprem_kb_metadata_inventory_2026-08-05.tsv`. The
complete capture contains 341 files: 305 under `mindshare/` and 36 under
`centralsquare/`. The earlier 333/297 transcript estimate is superseded.

The deterministic manifest classifies 164 candidates, 176 exclusions, and one
sanitization hold. Candidate totals are 131 Mindshare and 33 CentralSquare.

Observed candidate families include current Mindshare user manuals,
application notes, procedures, and release notes; older download copies;
public-site copies; a Logan County system-information PDF; a software catalog;
and CentralSquare configuration, end-user, installation, API, reporting, CAD,
mapping, records, NCIC, mobile, and quick-reference PDFs.

Mandatory metadata-level exclusions include sync-status files, `desktop.ini`,
empty one-byte readme placeholders, discontinued-document paths, backup and
firmware procedures, default-excluded download/archive copies, and the
49,035,668-byte combined CentralSquare PDF because it exceeds the current
25 MiB per-file intake limit. The Logan County system-information PDF requires
separate sanitization review before it can enter the sanitized-system prefix.

The exact transfer plan is fail-closed:

1. Use the complete per-file manifest to apply type, size, generated-file,
   discontinued, archive, backup, firmware, and public-site exclusions.
2. Obtain a separately authorized read-only SHA-256 pass to reconcile duplicate
   and current versions without copying or opening document content.
3. Record owner, sensitivity, copyright/license basis, retention, malware scan,
   secret scan, current/superseded status, and human approval per candidate.
4. Stage only signed-manifest files beneath a new manifest-scoped prefix. Verify
   exact count, hashes, encryption, metadata, and non-public bucket posture.
5. Keep Bedrock knowledge-base creation, vector creation, ingestion, and RAG
   activation behind their separate authorization and acceptance gates.

No content was read, no source file was changed, no document was transferred or
uploaded, and no AWS or RAG resource was changed.
