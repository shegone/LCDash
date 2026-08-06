# CentralSquare v1 cloud read connector wiring plan

Status: local configuration and test plan only. Deployment and activation are
not authorized by this document. The cloud pilot remains
`synthetic-disconnected` until this evidence is reviewed.

## Public documentation evidence

On 2026-08-05, credential-free HTTPS GET requests were limited to the
user-provided CentralSquare documentation host and these published pages:

- `/home/`
- `/home/api_administration/`
- `/api/cad/v1/docs`
- `/api/cad/v1/openapi.json`
- `/api/system/v1/docs`
- `/api/system/v1/openapi.json`

No token, data, search, write, action, webhook, or operational endpoint was
called. No credential or secret value was read or supplied.

Both v1 OpenAPI documents publish the `OAuth2PasswordBearer` password flow with
an empty scope set and exact relative `tokenUrl` `/api/token`. They publish
server bases `/api/cad/v1` and `/api/system/v1`. Therefore the reviewed
nonsecret configuration candidates are:

| Setting | Exact candidate |
| --- | --- |
| Token URL | `https://api-wv-logan-911.centralsquarecloudgov.com/api/token` |
| CAD base | `https://api-wv-logan-911.centralsquarecloudgov.com/api/cad/v1` |
| System base | `https://api-wv-logan-911.centralsquarecloudgov.com/api/system/v1` |

The existing approved secret reference remains metadata-only. Secret values
must be resolved only by the future runtime injection boundary and must never be
placed in source, manifests, test fixtures, logs, evidence, or command output.

## Closed runtime allowlist

Only these documented operations are needed by the current dashboard adapter:

| Method and path | Purpose |
| --- | --- |
| `POST /cfs_core/search` | Read/search calls |
| `GET /cfs_core/{CFSNumber}` | Read one call |
| `POST /units/search` | Read/search units |
| `GET /configurations` | Read system display configuration |

CentralSquare's API Administration page defines `Open` permission as covering
GET and `POST /search`; `Edit` covers POST/PUT create or update behavior. The
connector must use only the exact read allowlist above under the same-purpose
account's minimum `Open` permissions. POST is allowed only for the two published
`/search` paths and never as general write authority.

PUT, PATCH, DELETE, non-search POST, subscriptions, webhooks, commands,
acknowledgements, dispatch, alerts, paging, tones, messages, record creation,
record updates, and every other action endpoint are permanently out of scope.
The transport must fail closed before making any request not in the allowlist.

## Pagination, polling, and rate behavior

The CAD v1 search definitions publish `skip` with minimum 0 and `limit` from 1
through 100, default 10. The adapter may request at most 100 and must advance by
the returned item count without skipping reconciliation overlap.

The reviewed public documentation does not publish a numeric request-rate limit
or recommended polling cadence. Initial configuration therefore remains the
existing conservative 30-second poll with 120 seconds of reconciliation overlap.
The transport must honor HTTP 429 and `Retry-After`, apply bounded backoff and
jitter, and stop rather than increase request frequency. These are local safety
limits, not claims about a vendor limit.

## Next implementation review

Before deployment, wire a cloud-only transport that:

1. accepts only the three exact endpoint constants and approved secret ARN;
2. resolves username/password without returning or logging either value;
3. obtains a bearer token at the exact documented token URL without logging the
   request body, token, or response;
4. requires the documented `From` header and invokes only the four allowlisted
   read operations;
5. caps search pages at 100, preserves reconciliation/deduplication, and logs
   only sanitized counts/status;
6. rejects every non-allowlisted method/path before network I/O; and
7. leaves `activation_authorized=false` until local tests and the deployment
   diff are separately reviewed.

## Post-test activation and deployment checklist

This checklist describes the intended later operating state; it does not
authorize activation in the current task.

1. Confirm the reviewed release contains the exact endpoint constants, four
   allowlisted operations, page cap 100, 30-second poll, 120-second overlap,
   bounded retry behavior, redacted errors, and no startup activation default.
2. Review the deployment and IAM diff. Permit retrieval of only the approved
   tenant-scoped secret reference; keep secret values out of task definitions,
   parameters, logs, outputs, and operator evidence.
3. Confirm the same-purpose CentralSquare account has only the minimum `Open`
   permissions needed for CFS, Units, and configuration reads. Stop if any
   create, edit, action, acknowledgement, dispatch, alert, page, tone, message,
   command, subscription, webhook, or administration permission is present.
4. In a separately approved window, deploy with the connector still disabled.
   Verify application health and the synthetic empty-state rollback before
   enabling any live read.
5. Enable one cloud connector instance at a 30-second poll interval with 120
   seconds of reconciliation overlap. Do not enable webhooks or another poller.
6. Verify the runtime invokes only `POST /cfs_core/search`,
   `GET /cfs_core/{CFSNumber}`, `POST /units/search`, and
   `GET /configurations`, plus authentication at the exact token endpoint.
   Any other method/path is a stop condition.
7. Verify the cloud display becomes current using sanitized evidence only:
   successful poll count, last-success time, record counts, normalization reject
   count, duplicate count, and display freshness. Do not capture raw CAD
   payloads, narratives, caller/patient data, tokens, or credentials.
8. Treat display age greater than two completed poll intervals (60 seconds) as
   stale. Confirm retries honor 429 `Retry-After`, remain bounded, and do not
   increase normal polling frequency.
9. Observe at least two reconciliation windows (four minutes) and confirm calls
   and units refresh without duplicates or unexpected fields. No cloud read may
   block or affect CAD, dispatch, alerts, paging, radio, tones, or any other
   authoritative operation.
10. Stop and disable the connector on stale data, repeated authentication or
    upstream failures, unexpected response fields, unexpected permissions, raw
    payload logging, or any non-allowlisted request. Verify the display returns
    to the synthetic disconnected state without changing an authoritative
    system.
11. Record only the reviewed release identifier, operator/approver, activation
    window, sanitized freshness/count evidence, allowlist verification, and
    rollback result. Continuous 30-second read polling may remain enabled only
    after this evidence is accepted.
