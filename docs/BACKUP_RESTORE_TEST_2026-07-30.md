# Encrypted Backup Restore Test - July 30, 2026

## Result

Passed. The encrypted Google Drive backup was downloaded, decrypted, validated,
and restored into an isolated PostgreSQL 17 container on `lcdash-server`.
Production services and the production database were not modified.

## Backup tested

```text
lcdash-backup:server-227/postgresql/lcdash-20260730T145435Z.sql.gz
```

- Encrypted remote size after replacement: 58,364,069 bytes
- Local source size: 58,364,069 bytes
- Remote decrypted gzip test: passed
- PostgreSQL restore with `ON_ERROR_STOP=1`: passed
- Unvalidated constraints after restore: 0

## Restored data checks

The restored database contained all 20 application tables across the alerting,
analytics, knowledge, and real-time schemas. Representative restored counts:

| Table | Restored rows |
| --- | ---: |
| `lcdash_analytics.calls` | 998 |
| `lcdash_analytics.unit_responses` | 1,180 |
| `lcdash_knowledge.documents` | 311 |
| `lcdash_knowledge.chunks` | 14,375 |
| `lcdash_realtime.webhook_events` | 200 |
| `lcdash_alerting.ems_delay_alerts` | 36 |

Stable knowledge and configuration table counts matched production exactly.
Live operational tables were higher in production because LCDash continued
collecting calls, unit responses, alerts, sync runs, and webhook events after
the backup timestamp.

## Isolation and cleanup

- The test container had no published ports and used Docker network mode
  `none`.
- The temporary PostgreSQL container was removed after validation.
- LCDash and Open WebUI health checks passed after cleanup.
- The Cloudflare tunnel retained four active connections.

