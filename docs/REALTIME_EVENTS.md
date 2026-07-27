# LCDash Real-Time CentralSquare Events

## Purpose

LCDash can receive CentralSquare CAD change notifications and promptly refresh
an authenticated supervisor's dashboard. The existing 30-second snapshot refresh
remains enabled as a reconciliation and outage-recovery mechanism.

This feature is read-only. It does not create or update CAD records.

## Event flow

```text
CentralSquare subscription
    -> authenticated LCDash webhook receiver
    -> duplicate detection and delivery metadata
    -> generic browser event
    -> authenticated browser fetches a fresh operations snapshot
```

The browser event contains only the source type and receipt time. It does not
contain CAD, caller, patient, address, command-log, or unit details.

## Receiver URLs

```text
POST /api/integrations/centralsquare/webhooks/cfs
POST /api/integrations/centralsquare/webhooks/units
```

CentralSquare should authenticate with HTTP Basic authentication:

```text
Username: lcdash
Password: the protected CentralSquare webhook secret
```

CentralSquare's subscription documentation permits credentials in the callback
URL. The configured callback therefore has this form:

```text
https://lcdash:<protected-secret>@supervisor.logan911.com/api/integrations/centralsquare/webhooks/cfs
```

Never place the real callback URL in GitHub, screenshots, tickets, or ordinary
logs.

## CentralSquare subscription endpoints

```text
POST /api/cad/v1/cfs_core/subscription
POST /api/cad/v1/units/subscription
```

The official CAD schema requires:

- CFS subscription: `CallbackURL` and `DispatchAgency`
- Unit subscription: `CallbackURL`

The CFS `DispatchAgency` values must be selected from the tenant's actual
CentralSquare dropdown data. Do not guess them or activate a broad production
subscription without validating the intended scope.

Example shapes, with placeholders only:

```json
{
  "CallbackURL": "https://lcdash:<protected-secret>@supervisor.logan911.com/api/integrations/centralsquare/webhooks/cfs",
  "DispatchAgency": [],
  "CurrentlyActive": true,
  "ExcludeHistoricalRecordUpdates": true
}
```

```json
{
  "CallbackURL": "https://lcdash:<protected-secret>@supervisor.logan911.com/api/integrations/centralsquare/webhooks/units",
  "AVLOnly": false
}
```

The first response returns a `SubscriptionUniqueIdentifier`. Store each
identifier in the protected server credential record so the subscription can be
audited and updated later.

## Browser stream

Authenticated LCDash pages connect to:

```text
GET /api/operations/events
```

The stream uses Server-Sent Events. On `operations_changed`, the dashboard waits
500 milliseconds to combine a short burst of related CAD notifications, then
fetches `/api/operations/snapshot`. EventSource reconnects automatically.

## Security controls

- A unique random webhook secret is supplied through a Docker secret.
- Receiver bodies are limited to 1 MiB by default.
- Only JSON objects and arrays are accepted.
- Duplicate payloads are identified with a SHA-256 digest.
- Raw webhook payloads are not stored in the real-time metadata table.
- Raw payloads are not sent over the browser event stream.
- Static and API responses use cache controls appropriate to their content.
- Cloudflare Access must bypass only the two exact receiver paths (or the
  narrow receiver prefix), while all human-facing pages and the browser event
  stream remain protected.
- Add Cloudflare rate limiting or equivalent protection to the receiver path.

## Activation checklist

1. Deploy and health-check the receiver.
2. Create and protect the webhook secret.
3. Test each receiver locally with authenticated sample JSON.
4. Configure a narrow Cloudflare Access service-token/bypass policy for only
   the receiver paths.
5. Test the public receiver from outside the protected browser session.
6. Retrieve and validate the tenant's dispatch-agency values.
7. Create one CFS subscription and record its identifier.
8. Confirm a real CAD change reaches LCDash and refreshes the browser.
9. Create the unit subscription and record its identifier.
10. Keep the 30-second reconciliation refresh enabled.

