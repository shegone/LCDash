# Historical analytics import activation runbook

Status: **STAGING STACK DEPLOYED DORMANT - IMPORT NOT AUTHORIZED**
Package: 1A planning support for a future Phase 2 decision

This runbook covers a one-way import from an already approved, encrypted staging artifact into the cloud analytics database. The completed staging-stack deployment does not authorize any further infrastructure change, source access, export, upload, task execution, live CAD access, service activation, or deletion.

The dedicated staging stack was created in account `862772137583`, region
`us-east-1`, on 2026-08-05. That completed deployment authorization only. The
approved historical export was subsequently uploaded as one client-side
authenticated-encrypted object under the staging prefix. No plaintext export
file was written. The ECS importer task has not run. Import execution, CAD
access, and application activation remain unauthorized. Sanitized deployment
and transfer evidence is recorded in
`infrastructure/work/analytics-import-deploy-evidence-20260805.json` and
`infrastructure/work/analytics-export-transfer-evidence-20260805.json`.

## Local infrastructure design

`infrastructure/analytics_import_app.py` synthesizes a separate stack containing:

- one private S3 bucket with a dedicated customer-managed KMS key;
- the tenant-bound prefix `tenants/logan-synthetic/historical-analytics/`;
- automatic expiration after three days and incomplete multipart cleanup after one day;
- a task role limited to listing and reading objects under that exact prefix plus KMS decryption; it cannot upload or delete staging objects;
- a separate execution role limited to the approved ECR repository, target database secret, and task logs;
- one Fargate task definition, with no ECS service, scheduler, event rule, Lambda function, Step Functions workflow, or source/CAD permission.

Task-definition revision 1 intentionally exits with an error and must not be
run. The locally implemented replacement invokes
`app.tools.phase2_analytics_import_runtime`, binds IAM access to the exact staged
object parameter, validates and decrypts only in memory, and uses a dedicated
security group. This replacement remains local until its image and import-stack
change set are separately reviewed and deployed.

## Authorization and activation checklist

Every item is a stop/go gate. Record named approver, UTC timestamp, and evidence reference without secret values or row content.

The sole approved on-premises SSH identity for this migration is
`administrator@14.1.1.227`. Do not substitute `ted`, another account, or
password authentication. The dedicated one-time public key is stored locally
at `C:\Users\tedsp\.ssh\lcdash_aws_migration_20260805.pub` and must be removed
from the server and workstation after the migration is accepted.

1. Approve the exact five-table and field contract in `infrastructure/phase2_data_migration_contract.json`, date window, retention decision, data owner, operator, target, and maintenance window.
2. Verify that the source export was produced through the separately authorized read-only, repeatable-snapshot process. Confirm no raw CAD payload, narrative, caller contact, medical detail, recording, credential, or operational-output record is present.
3. Review a CloudFormation change set for the separate analytics-import stack. Confirm it creates no ECS service, scheduler, network ingress, source-system permission, or CAD permission. Do not execute the change set without the infrastructure authorization gate.
4. Supply only an immutable importer image URI (`...@sha256:<64 lowercase hex>`), its exact ECR repository ARN, and the target-only database secret ARN. Never supply a source or CAD secret.
5. After authorized stack creation, record the output bucket, prefix, KMS key, task definition revision, and log group. Confirm public access is blocked, TLS is enforced, and lifecycle is three days.
6. Upload only the approved encrypted artifact and sanitized manifest through an authorized human process. Verify server-side encryption uses the stack KMS key, object paths remain under the exact tenant prefix, and checksums/counts match. This repository provides no uploader.
7. Confirm the target schema version, available capacity, empty target scope or approved resume manifest, TLS connection behavior, and rollback approval path. Keep the web service activation decision separate.
8. Review and approve the immutable runtime image digest and exact staged-object, plaintext-checksum, VPC, and target-database-security-group parameters. The runtime must read only that object, write only the five approved target tables, produce count/hash/reject-class evidence without row values, and exit nonzero on any mismatch.
9. Run exactly one task in approved private subnets and a security group permitting only required DNS, AWS service access, logs, and target PostgreSQL egress. Do not reuse a CAD-enabled application security group. There is no `run-task` command in this runbook because network identifiers and authorization evidence must be reviewed at execution time.
10. Wait for task completion; verify exit code zero, matching row/distinct-key counts, zero duplicates/orphans, manifest hashes, timestamp bounds, and sanitized reject counts. A failed or stopped task is not success.
11. Delete staged objects only after integrity acceptance and separate deletion approval. Lifecycle expiration is the backstop. Confirm deletion without printing keys that contain sensitive identifiers.
12. Record final watermark/freshness evidence. Migration success does not authorize live CAD or service activation.

## Cost and retention notes

- S3 cost is bounded by the staged byte volume and three-day lifecycle, but lifecycle expiration is asynchronous and may occur after the nominal deadline. Operators should verify deletion after acceptance.
- The KMS key, CloudWatch log group, bucket, and their metadata are retained when the stack is deleted to avoid accidental evidence loss. They continue to incur small storage/key charges until a separately authorized teardown.
- Each object upload and importer read incurs S3 and KMS request charges. Bucket keys reduce repeated KMS request cost. Incomplete multipart uploads expire after one day.
- CloudWatch logs retain seven days. Logs must contain counts, hashes, timestamps, and reason classes only, never row content or secrets.
- Fargate charges accrue only while the explicitly started task runs. The template creates no continuously running service, NAT gateway, scheduler, or retry loop.
- RDS load and storage growth are not capped by this stack. Confirm capacity and expected import duration before execution.

## Failure boundary

On schema, authorization, checksum, count, orphan, duplicate, freshness, encryption, or field-scope failure: stop the task, preserve sanitized evidence, leave the source untouched, and request review. Deleting cloud records or staged objects requires separate authorization. Do not weaken the contract or add source/CAD permissions to make a run pass.
