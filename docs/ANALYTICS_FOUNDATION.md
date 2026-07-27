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

CAD processing is measured from the CFS `CallDateTime` to the earliest valid
agency dispatch timestamp. It is not 911 phone-answer time. NGA911 integration
can later add answer time, call duration, abandoned-call, transfer, queue-delay,
and callback metrics.

## Tables

- `lcdash_analytics.calls`
- `lcdash_analytics.units`
- `lcdash_analytics.call_agency_times`
- `lcdash_analytics.unit_responses`
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
