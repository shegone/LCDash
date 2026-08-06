# Private cloud document-library IaC plan

## Status and boundary

This is local Package 1A infrastructure and upload planning only. The standalone
entry point is `infrastructure/document_library_app.py`; the main foundation
entry point does not include this stack. No bucket, role, object, AWS change, or
source-system access is authorized by this plan.

## Proposed storage shape

Proposed bucket name:
`lcdash-p1-logan-use1-${AWS::AccountId}-document-library`.

The bucket blocks every form of public access, rejects non-TLS requests, and
uses S3-managed server-side encryption. Object ownership remains private to the
AWS account through the CDK bucket defaults. The initial pilot uses no customer
managed KMS key, avoiding key administration and per-request KMS cost.

Approved application-readable prefixes are:

- `tenants/logan-synthetic/document-library/centralsquare/current/`
- `tenants/logan-synthetic/document-library/mindshare/current/`
- `tenants/logan-synthetic/document-library/mindshare/sanitized-system/`
- `tenants/logan-synthetic/document-library/mindshare/software-catalog/`
- `tenants/logan-synthetic/document-library/manifests/approved/`

`tenants/logan-synthetic/document-library/staging/` is never application-readable.
It expires after seven days. Incomplete multipart uploads are aborted after one
day. Approved documents have no automatic expiry because the inventory does not
provide a lawful retention period; adding one requires a later policy decision.

## Versioning and deletion decision

Versioning is disabled. Package 1A explicitly accepts no backup, restore, or
recovery capability, and the approved infrastructure shape already records
content bucket versioning as false. Retaining old object versions would create
an undeclared recovery mechanism and recurring storage cost. The stack uses
delete-on-stack-removal behavior to preserve the time-bounded disposable pilot
model. The stack deliberately has no auto-delete provider, cleanup Lambda,
custom resource, or cleanup IAM role. CloudFormation can therefore remove the
bucket only while it is empty; any later object-removal procedure requires its
own authorization and must not be inferred from stack deletion. Deletion or
replacement may permanently destroy documents and must be accepted at the later
authorization gate before deployment or upload.

## Least-privilege application role

The proposed `lcdash-p1-logan-use1-document-library-read` role is assumable only
by ECS tasks. It can list the bucket only for the five exact approved prefixes
and can call `s3:GetObject` only below those prefixes. It receives no upload,
delete, version, ACL, policy, encryption, staging, unrelated tenant, or bucket
administration permission. This role is dormant and is not attached to the
currently deployed task definition.

## Manifest-driven admission and upload

`DOCUMENT_LIBRARY_UPLOAD_PLAN_2026-08-05.json` converts the existing on-premises
inventory into a fail-closed admission contract. Before any upload, an approved
read-only enumeration must supply object path, size, SHA-256, owner, sensitivity,
license basis, retention class, supersession information, malware and secret-scan
results, and human approval. A separate authorization must approve both resource
creation and the final object manifest. No upload utility is included.

CentralSquare is limited to individually reviewed PDFs. Mindshare current
documentation is eligible only after review; Logan County system references must
be sanitized; Software Catalog is metadata-only and must be secret-free. Vendor
archives are excluded by default. Public-site files must not be copied from the
on-premises snapshot and require a separate reviewed re-fetch. GIS remains in a
separate structured-data workflow.

## Explicit exclusions

Credentials and secret-bearing material; backups and recovery artifacts; raw CAD
payloads and protected operational records; recordings and operational
transcripts; emergency-service outputs; software binaries, firmware and models;
GIS data; empty placeholders; and generated sync metadata remain excluded. The
exact exclusion contract is machine-readable in the upload plan.
