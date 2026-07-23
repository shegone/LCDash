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
