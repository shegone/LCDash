# Phase 2 pre-activation data migration plan

Status: **PLAN ONLY - NOT AUTHORIZED**

This plan describes a future, one-way migration of the full approved historic LCDash
analytics snapshot into the independent cloud pilot before any service
activation. It is not Phase 1 work, does not authorize Phase 2, and does not
authorize access to a source database, `.227`, PC `.15`, `E:\Projects\LCDash`,
CentralSquare, credentials, or AWS. The machine-readable contract is
`infrastructure/phase2_data_migration_contract.json`.

## Scope and classification

The migration is limited to the four analytics base tables `calls`, `units`,
`call_agency_times`, and `unit_responses`, plus
`saved_analytics_widgets`. The two response metric views are rebuilt from the
base tables and are never copied. MAE/JACK
questions, answers, feedback, memory, user email, knowledge documents/chunks,
raw payloads, realtime state, alerts, webhook records, messages,
acknowledgements, subscriptions, pages, station alerts, public warnings,
recordings, caller contact fields, street addresses, narratives, and medical
details are excluded because no current analytics calculation requires them.

The included rows are sensitive operational analytics data. Exact CFS and unit
identifiers, dispatcher display and stable identifiers, and coordinates are
retained because existing joins, timelines, dispatcher workload, mapping, and
parity evidence depend on them. They require a private encrypted target,
least-privilege tenant-bound access, auditable reads, an approved retention
window, and a named data owner. No unrestricted narrative or other free-text
field is permitted.

## Source-preserving snapshot

The repository implementation is `app.tools.phase2_analytics_import`. It accepts
already-open source and target connections; it never discovers connection
settings, handles credentials, or writes an export file. Its source projections
and target columns are fixed as follows:

- `calls`: preserved `cfs_number`; `dispatch_agency`, `response_agency`;
  preserved `call_taker` and `call_taker_unique_identifier`;
  controlled-category `incident_code`,
  `incident_description`, `priority`, `disposition_code`,
  `disposition_description`, `beat`, `zone`, `city`; preserved `latitude` and
  `longitude`; `is_scheduled`, `incident_at`, `call_received_at`, `closed_at`,
  and `source_collected_at`.
- `units`: preserved `unit_number`; `agency`, `unit_type`, `station`, and
  `last_seen_at`. Only units referenced by an eligible response are selected.
- `call_agency_times`: preserved `cfs_number`; `agency_ori`; and the
  controlled timestamps `dispatched_at`, `enroute_at`, `staged_at`,
  `on_scene_at`, `at_patient_at`, `backup_enroute_at`, `backup_arrived_at`,
  `leaving_at`, `transporting_at`, `arrived_at`, `available_at`, and
  `in_quarters_at`.
- `unit_responses`: preserved `cfs_number` and `unit_number`; `unit_type`,
  `station`, `beat`; and the same controlled response timestamps.
- `saved_analytics_widgets`: identifier and timestamps, creator, title,
  allowlisted view key, and active/retired status.

Before an authorized run, the human operator must record the approved UTC
half-open time window, retention decision, source replica identity, database-
enforced read-only account evidence, target identity, operator and maintenance
window, encrypted network path, approved connection-secret entry mechanism,
target schema version, and confirmation that the target five-table scope is
empty or its prior batch
manifest is the authorized resume point. The operator must also confirm that
both connections use TLS, autocommit is disabled, source and target PostgreSQL
versions are compatible, sufficient target capacity exists, service desired
count remains zero, and sanitized count-only evidence storage is ready.

1. A human approves the exact source replica/connection, target, date range,
   retention, operator, and maintenance window. The source connection must be
   database-enforced read-only and use a consistent repeatable-read snapshot.
2. The operator records a source watermark and per-table key/count summary,
   without exporting row values into logs or evidence.
3. Export applies the field map before data leaves the protected source
   environment. Every encrypted batch receives a deterministic manifest hash,
   row count, key range, and schema-contract version.
4. Transfer must use authenticated encryption in transit. Any temporary
   artifact must also be encrypted at rest, access-limited, and securely
   deleted after verification. Plaintext transfer or shared-drive staging is a
   stop condition.
5. Target loading uses idempotent upserts on the recorded primary/composite
   keys. A successfully recorded batch may be replayed without adding rows or
   changing already-equal values. Rejected rows remain quarantined as counts
   and reason classes only; protected row content is not logged.

## Final delta catch-up and integrity

The source remains authoritative and online. After the historic snapshot, the
same read-only export is repeated from the last recorded watermark until a
final delta produces stable counts. The process never polls CAD and never
creates a webhook, subscription, acknowledgement, or operational output.

Before activation review, source and target must have matching per-table row
and distinct-key counts for the approved window; zero duplicate keys; zero
foreign-key orphans; matching timestamp bounds; verified manifest hashes; and
documented rejected-row counts. The final source watermark, target maximum
`source_collected_at`, completion time, and calculated lag must be recorded.
The proposed maximum freshness lag is 15 minutes, but it becomes binding only
if the Phase 2 approver accepts it. Any later source change requires another
delta and a new freshness record.

## Secrets, authorization, and activation hold

Source and target connection secrets and transfer keys are entered directly by
an authorized human into the separately approved runtime mechanism. An AI agent
must never request, receive, copy, print, log, or store them. Secret values never
enter Git, manifests, commands, screenshots, or handoffs.

Successful migration does not activate the application or authorize live CAD.
Service desired count remains zero until the separate image/activation release,
and the pilot remains synthetic/disconnected until the full Phase 2 gate has
vendor, security, data-handling, freshness, cost, and named-human approval
evidence.

## Failure and rollback

Any schema mismatch, unapproved field/object, count mismatch, orphan, duplicate,
hash failure, stale delta, plaintext path, secret exposure, or attempted source
write stops the migration. The source is never modified or rolled back. After
separate destructive-action approval, rollback deletes only the cloud copy and
its temporary migration artifacts, verifies deletion, and leaves the source
database authoritative and untouched.

## Evidence required before Phase 2 activation review

- approved table/field/date-range/retention classification;
- source read-only enforcement and consistent-snapshot reference;
- authorized-human secret-entry procedure reference;
- encrypted transport and temporary-artifact controls;
- batch manifests, counts, key uniqueness, orphan checks, and reject summary;
- historic snapshot and final delta watermarks with calculated freshness;
- confirmation that excluded operational-output objects and raw fields are zero;
- cloud-only rollback approval path and named owner;
- explicit Phase 2 decision. Migration success alone is never that decision.
