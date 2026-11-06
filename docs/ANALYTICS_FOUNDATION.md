# LCDash Analytics Foundation

LCDash uses CentralSquare for live operations and PostgreSQL for historical analytics.

## Data flow

```text
Completed CentralSquare CFS
        |
        v
GET /cfs_analytics/{CFSNumber}
        |
        v
Privacy-minimized normalization
        |
        v
PostgreSQL lcdash_analytics schema
        |
        +--> LCDash analytics pages
        +--> Grafana
        +--> Scheduled reports
        +--> Future AI summaries
```

The collector stores operational fields and timestamps. It intentionally excludes caller
names, telephone numbers, narratives, command logs, street addresses, RapidSOS content,
and raw API payloads.

Coordinates are rounded to four decimal places for operational demand analysis.

The calls table also stores the CentralSquare call-taker identifier (call sign or
username) so supervisors can review CAD-entry workload without storing caller or
patient information.

## Dispatcher and CAD-entry metrics

The Analytics page includes a Dispatcher / CAD Entry section for:

- Calls entered by call taker
- Share of attributed calls
- Average and median CAD processing time
- Percentage dispatched within 90 seconds
- Calls taking longer than three minutes to reach first dispatch
- Call-taker data coverage for the selected reporting window

CAD processing is the public yardstick: the call being added to CAD to someone
being added to the call. It is measured from the CFS `CallDateTime` to the
earliest action by any unit of any agency on the call (Stacked, Assigned, En
route, Staged or On scene), read from `unit_status_events` (the CAD command
log's status rows, the only place Stack appears), `unit_responses` and
`call_agency_times`. A call is timed only when its call type is not on the
not-timed list, it is not scheduled, it has such a stamp at or after the
received time, and the interval is within the 60-minute cap; the four excluded
buckets are counted on the page (summary line and the "Not timed" column) and
calls over the cap are listed in the slow-call drill-down as "not averaged".
The not-timed call types (`EXCLUDED_CALL_TYPES` in
`app/services/analytics_reporting.py`, Ted's list of 2026-11-05) are natures
that are never dispatched as an emergency: Roundtrip, Take Home, Transfer /
Inter-Facility, Contact, Move-Up, Shoplifting, Complaint, 911 Hang Up, Call
Through Office, 911 Open Line, Information and Be On the Lookout. They are
matched on the CAD incident description ignoring case, spaces and punctuation,
still count as calls entered, and are left out of the discipline tables too. A Stack (the call queued on a busy unit) ends
the interval like an Assign; the unit's wait from that Stack to its first
Assign or En route is the hold, reported apart (the "Stacked" column with the
average hold, the "Stacked first" summary line, the drill-down's "Unit hold")
and never added to the dispatcher's time. The drill-down names the first
action (event and unit or agency) so an interval can be checked against the
CAD report. Status rows are collected from the day the table was introduced;
earlier calls have no Stack and time to their first Assign. See
`docs/planning/ANALYTICS_PROCESSING_AUDIT_2026-11-05.md` for the audit, the
options and the data-model change. CAD processing is not 911
phone-answer time. NGA911 integration can later add answer time, call duration,
abandoned-call, transfer, queue-delay, and callback metrics.

The dispatcher section (its KPIs, the table and the slow-call drill-down) can
be narrowed to one discipline with the All / EMS / Fire / Law links beside the
coverage chip (`?discipline=ems|fire|law` on `/analytics`,
`/api/analytics/overview` and `/api/analytics/dispatcher-slow-calls`; any
other value is a 400). The filtered view keeps the calls that discipline's
units acted on, using the same unit-roster rule as the discipline tables, so
a call with EMS and fire units counts under both. The discipline chooses the
calls only: each call is still timed from the CAD call-received time to the
first unit action on the call by any discipline, because the first unit
assigned is what stops the dispatcher's clock (decided 2026-11-06, after a
medical call whose fire units were added half an hour later showed the whole
wait as fire processing). The by-discipline tables use the same rule. The
page-level metrics and charts stay county-wide.

Response times (average and median response, coverage, busiest-unit average)
use the earliest on-scene stamp from either record and exclude scheduled calls.
The discipline tables classify a call's units by the unit roster agency or unit
type (FC departments fire, LEASA EMS, sheriff and police law), with the
per-agency ORI as a fallback.

Kim Maynard is excluded by exact normalized name from this Dispatcher / CAD
Entry section because she is an ambulance-service employee rather than a Logan
County dispatcher. This reporting rule does not delete her records or alter
overall call, unit, station, response, or EMS analytics.

For the Calls by Agency display, the stored `LCEOC` agency code is presented as
`911 Center / Administrative`. This is a display-only label for 911-center and
administrative call categories; counts and stored source values are unchanged.

## Time-based demand

Calls by Hour and Calls by Day of Week use the completed call's received time,
converted to Logan County local time. The day-of-week chart includes Sunday
through Saturday so supervisors can identify the busiest recurring calendar day
within the selected historical reporting window.

## Tables

- `lcdash_analytics.calls`
- `lcdash_analytics.units`
- `lcdash_analytics.call_agency_times`
- `lcdash_analytics.unit_responses`
- `lcdash_analytics.unit_status_events` (command-log status rows: timestamp,
  unit, status and its kind; no narrative, creator or destination)
- `lcdash_analytics.sync_state`
- `lcdash_analytics.sync_runs`

Calculated views:

- `lcdash_analytics.call_response_metrics`
- `lcdash_analytics.unit_response_metrics`

## Local or server setup

1. Create a PostgreSQL database and a dedicated LCDash database user.
2. Put the real connection string in the local `.env` file as `DATABASE_URL`.
3. Install project requirements.
4. Initialize the schema:

```powershell
py scripts\init_analytics_db.py
```

5. Run the first completed-call synchronization:

```powershell
py scripts\sync_analytics.py --hours 24 --max-calls 250
```

The collector searches only completed calls. Successful runs save a high-water mark.
Later runs overlap the prior window to catch delayed updates. Upserts prevent duplicates.
If any per-call analytics request fails, the high-water mark does not advance, allowing a
later run to retry.

## Production schedule

On the Linux server, run the collector every five minutes or trigger it from a completion
webhook. Retain a scheduled reconciliation run as a safety net.

## Docker deployment

LCDash now runs in a dedicated Compose platform with its own PostgreSQL database,
five-minute analytics worker, daily backup service, local Ollama AI service, and
authenticated Open WebUI. The web interfaces remain published only on server loopback
until LCDash has authentication and role-based access.

Build and start the web application:

```bash
docker compose -f deploy/compose.yaml up -d --build
```

Initialize the analytics schema:

```bash
docker compose -f deploy/compose.yaml exec lcdash python scripts/init_analytics_db.py
```

Run a controlled completed-call synchronization:

```bash
docker compose -f deploy/compose.yaml exec analytics-worker python scripts/sync_analytics.py
```

From an authorized workstation, use an SSH tunnel to view the protected application:

```powershell
ssh -i "$env:USERPROFILE\.ssh\lcdash_server_ed25519" `
    -L 8010:127.0.0.1:8010 `
    -L 3000:127.0.0.1:3000 `
    ted@14.1.1.177
```

Then open `http://127.0.0.1:8010` locally.
