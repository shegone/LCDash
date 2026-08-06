# Cloud inbound CAD read activation assessment

## Decision

The cloud pilot is not ready for a live CentralSquare read path. It must remain
`synthetic-disconnected`. No approved cloud-only read credential, vendor endpoint
authorization, stable egress decision, or live-data classification and retention
approval is present in the repository or deployed Phase 1 design.

This package adds only a dormant, fail-closed configuration contract at
`app/integrations/cad/cloud_read_config.py`. Nothing imports it into application
startup, no transport is created, no secret is resolved, and no network call,
poll, webhook, or AWS change occurs.

## Existing boundary

`CentralSquareCadAdapter` already provides tenant-bound normalized reads for
call search, call detail, and unit search. Its provider capabilities omit call
updates, messages, acknowledgements, and subscription registration, and its
tests prove those operations deny by default. Existing inherited application
consumers still use a compatibility transport configured by legacy environment
variables, so that transport must not be enabled in the cloud by copying on-prem
values.

The deployed pilot deliberately sets `LCDASH_DEPLOYMENT_MODE` to
`synthetic-disconnected`; Dashboard, Units, Map, and Heatmap exit before legacy
CAD initialization. The task definition contains no CentralSquare endpoint or
credential settings, and the task role has no permission to retrieve a CAD
secret.

## Exact missing prerequisites

1. A dedicated vendor-approved cloud-only inquiry account with the minimum
   search/detail/unit permissions. It must have no create, update, dispatch,
   acknowledge, command, message, page, alert, tone, subscription, or
   administrative permission.
2. A new Logan-specific `us-east-1` Secrets Manager secret entered directly by
   an authorized human or approved broker. The application receives only its
   ARN/reference during review; no secret value may enter source, parameters,
   prompts, logs, or handoffs.
3. Written vendor confirmation for concurrent cloud access, commercial-AWS
   processing, allowed source addresses, rate limits, token lifetime, search
   limits, polling expectations, and whether a stable egress IP is required.
4. Reviewed HTTPS token, CAD, and system base URLs. DNS names and certificates
   must be independently verified; userinfo, query credentials, IP literals,
   redirects to unapproved hosts, and plaintext HTTP are rejected.
5. A network design. The pilot has no NAT gateway and currently assigns public
   IPs to tasks, so it does not provide a stable allowlistable egress IP. Choose
   and cost a controlled egress path only after the vendor states its
   requirement; do not add networking speculatively.
6. A narrowly scoped task execution/runtime injection path for the dedicated
   secret, with rotation and sanitized health behavior. Do not grant general
   Secrets Manager reads and do not reuse the RDS secret path.
7. A data-owner-approved live-data classification, retention, audit, incident
   response, breach notification, and deletion plan. The current synthetic
   profile explicitly forbids protected data.
8. A polling contract and maintenance window. Initial activation should be
   bounded read polling only, 15-300 seconds, with reconciliation overlap and
   deduplication. Do not create or register another webhook. Webhook ownership,
   callback authentication, retry behavior, and vendor single-writer behavior
   require a separate gate.
9. A cloud-only rollback owner and success evidence: request counts, sanitized
   status/error metrics, no raw payload logging, freshness threshold, duplicate
   rate, normalization rejects, and proof that disabling the provider restores
   the synthetic empty state without affecting any authoritative system.

## Vendor-published documentation candidates

The unauthenticated CentralSquare ProSuite/IJ6 portal visibly publishes these
nonsecret v1 documentation routes on the candidate Logan host:

- `https://api-wv-logan-911.centralsquarecloudgov.com/api/cad/v1/docs#/`
- `https://api-wv-logan-911.centralsquarecloudgov.com/api/system/v1/docs#/`

These routes are evidence that versioned CAD and System API documentation is
published for the environment. They are documentation UI routes only. The
`/docs` path and browser fragment must not be configured as a runtime API base,
and their presence does not establish an authentication method, token URL,
resource paths, permissions, rate limits, source-IP rules, or cloud-access
approval. The separately published `latest` aliases are not selected because an
activation contract must pin a reviewed version.

The closed preflight still requires vendor-provided runtime token, CAD, and
System endpoint records. Do not infer the token endpoint, strip `/docs`, or
construct an API base from these documentation URLs. The three reviewed runtime
URLs may share a hostname, but each exact URL/path and its TLS evidence must be
supplied independently. `activation_authorized` remains false.

## Data minimization

The dormant contract permits only normalized dashboard fields. Calls are limited
to CFS number, incident code/description, priority, agency, status, timestamp,
display location label, and assigned unit numbers. Units are limited to unit
number, agency, type, status, station, and assignment CFS number.

Do not persist or log raw payloads, command logs, narratives, caller or patient
details, contact information, dispatcher identifiers, coordinates, attachments,
audio, or fields not approved for the named dashboard feature. Location and
incident fields still require data-owner classification before live use; they
are not approved merely because the normalizer supports them.

## Poll, webhook, and reconciliation position

The first safe live path is polling plus reconciliation, not webhook activation.
The configuration contract requires at least one poll interval of overlap and
rejects webhooks. Reconciliation must use stable event/call identifiers,
idempotent upserts, bounded pages, rate-limit backoff, timeouts, freshness
monitoring, and minimized audit metadata. A webhook may be assessed later only
after vendor and operator approval; it must never replace reconciliation.

## Activation sequence after prerequisites

1. Record the vendor, data-owner, security, network, credential, and maintenance
   approvals without secret values.
2. Add an infrastructure parameter for the reviewed secret ARN, exact endpoint
   host allowlist, and explicit provider mode. Review the CDK diff and IAM diff.
3. Construct a cloud-specific transport from the validated configuration while
   keeping the existing read-only provider contract and deny-by-default writes.
4. Run synthetic adapter, tenant-isolation, minimization, timeout, retry,
   pagination, reconciliation, and no-raw-logging tests.
5. In an approved window, test only health/authentication and one bounded read
   with the cloud-only account. Stop on any unexpected permission or data field.
6. Activate dashboard polling only after evidence review. Keep webhook and every
   operational output disabled.

No step above is authorized by this assessment.
