# Phase 2 analytics source inventory and import plan

Status: **INVENTORY COMPLETE - EXPORT AND UPLOAD NOT STARTED**

## Verified source

The source is the production PostgreSQL 17.10 database `lcdash`, schema
`lcdash_analytics`, in container `lcdash-postgres` on the `.227` LCDash host.
The schema source of truth is `E:\Projects\LCDash\database\analytics_schema.sql`.
The database is not published to the host network.

One count-only inventory ran through the existing SSH key and container-local
database identity. It began `REPEATABLE READ READ ONLY`, confirmed
`transaction_read_only=on`, selected aggregate counts/bounds only, and rolled
back. It did not read row content, credential files, backups, binaries, models,
or output/control records.

| Candidate table | Rows | Distinct approved key | Observed timestamp bounds (UTC) |
| --- | ---: | ---: | --- |
| `lcdash_analytics.calls` | 1,761 | 1,761 | 2026-07-23 15:24:03 to 2026-08-05 18:47:03 |
| `lcdash_analytics.units` | 104 | 104 | 2026-07-23 21:12:52 to 2026-08-05 18:47:03 |
| `lcdash_analytics.call_agency_times` | 727 | 727 | 2026-07-22 15:54:01 to 2026-08-05 18:14:42 |
| `lcdash_analytics.unit_responses` | 2,041 | 2,041 | 2026-07-22 12:17:47 to 2026-08-05 18:14:42 |
| `lcdash_analytics.saved_analytics_widgets` | 1 | 1 | 2026-08-03 21:08:21 to 2026-08-03 21:08:21 |

Observed orphan counts were zero for agency-time to call, unit-response to
call, and unit-response to unit relationships.

## Candidate scope

Only the five tables above are candidates. Target views
`call_response_metrics` and `unit_response_metrics` are rebuilt and not copied.
Sync state/runs, MAE/JACK interactions and memory, realtime/webhook data,
alerts, messages, acknowledgements, subscriptions, EMS/paging/station-alert or
public-warning records, credentials, raw payloads, caller/contact fields,
addresses, narratives, medical details, recordings, backups, binaries, and
models remain excluded.

## Approved field policy

The data owner explicitly authorized full operational historical analytics for
this same LCDash system. The exact allowlisted projections in
`app/tools/phase2_analytics_import.py` therefore preserve CFS numbers, unit
numbers, dispatcher display/stable identifiers, latitude, and longitude needed
for joins, timelines, dispatcher workload, mapping, and analytics parity.

These fields remain sensitive. They require the private encrypted target,
tenant-bound least privilege, audit logging, approved retention, and encrypted
transport/staging described below. The authorization does not expand the table
or field allowlist and does not permit unrestricted free text.

Credentials and secrets, backups, binaries and models, raw CAD payloads,
recordings and transcripts, caller/contact data, street addresses, narratives,
medical details, realtime/webhook bodies, sync/control state, messages,
acknowledgements, subscriptions, CAD commands, alerts, paging, radio/ESInet,
station alerts, public warnings, and all other output/control records remain
excluded.

## Proposed one-way encrypted path

1. An authorized human supplies an independently created database-enforced
   read-only source role and approves an exact UTC half-open retention window.
2. A source-side exporter executes only the reviewed projections in a single
   repeatable-read/read-only transaction. It emits deterministic chunks and
   count/checksum evidence without logging row values.
3. Chunks are compressed and encrypted client-side with AES-256-GCM; the data
   key is wrapped by a dedicated AWS KMS key. No plaintext staging file is
   permitted.
4. The encrypted artifact is uploaded over TLS to a dedicated private S3
   migration prefix with least-privilege write-only source access, short
   retention, audit logging, and no public access.
5. A one-off ECS import task in the existing private application network reads
   that prefix, unwraps/decrypts in memory, validates the final admission
   manifest, and uses TLS to idempotently upsert only the five approved tables
   into private RDS. The task has no CAD or operational-output permissions.
6. Source/target counts, distinct keys, timestamp bounds, checksums, duplicate
   counts, orphan counts, rejected reason classes, and the final watermark are
   compared before the encrypted staging artifact is deleted under a separate
   approved cleanup action.

The dedicated KMS key, S3 migration prefix/policies, source upload identity,
and ECS import role/task are not currently verified as existing. They require a
separate reviewed infrastructure package before export.

## Exact access gaps

- The present SSH/container path can perform aggregate read-only inventory, but
  a separate database-enforced read-only migration role has not been evidenced.
- The UTC retention window and consistent watermark are not yet approved.
- No real export or per-table checksum exists; the inventory JSON is deliberately
  not an executable admission manifest.
- The AWS CLI session expired before this inventory could re-verify the exact
  RDS instance identifier and import role. The known target contract remains
  account `862772137583`, region `us-east-1`, database `lcdash`, schema
  `lcdash_analytics`, private and TLS-required.
- No encrypted staging/import infrastructure has been approved or created.

The local inventory manifest is
`work/phase2_analytics_source_inventory_2026-08-05.json`. It explicitly records
`execution_authorized=false`, `export_created=false`, and
`upload_started=false`.
