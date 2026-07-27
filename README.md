# LCDash

Logan County 911 Operations Dashboard

LCDash is a private public-safety operations platform that combines live
CentralSquare CAD information, historical analytics, station alerting,
supervisor tools, local AI, and controlled technical knowledge libraries.

## Current application areas

- `/dashboard` - live operations overview
- `/active-calls` - active calls and incident detail
- `/units` - active, available, on-duty, and unavailable units
- `/map` - live incident and unit mapping
- `/analytics` - PostgreSQL-backed operational reporting
- `/station-alerts` - selected-station visual and audible dispatch alerting
- `/integrations/health` - metadata-only CentralSquare delivery monitoring
- `/mae` - read-only Mission Assistance Engine
- `/mae/reliability` - MAE quality, feedback, and approved-memory controls
- `/mindshare/technical` - JACK, the read-only Mindshare technical assistant
- `/mindshare/library` - indexed Mindshare source documents
- `/mindshare/reliability` - 30 manual-grounded JACK evaluation questions
- `/mindshare/coverage` - product, document-type, and revision coverage review
- `/voice` - private local speech laboratory

## Safety boundaries

- MAE and JACK begin in read-only inquiry mode.
- Credentials, keys, tokens, and licenses must never be committed to GitHub.
- Mindshare firmware or software must not be selected solely because it is the
  newest version; product, hardware, current version, supported upgrade path,
  backup, rollback, and operational approval must be confirmed.
- The Mindshare Radio Intelligence module remains inactive until its isolated
  multicast network and retention rules are validated.

## Documentation

Operational and technical guides are maintained under `docs/`, including:

- `SERVER_DEPLOYMENT.md`
- `ANALYTICS_FOUNDATION.md`
- `MAE_RELIABILITY.md`
- `MINDSHARE_LIBRARY.md`
- `JACK_RELIABILITY.md`
- `VOICE_STACK.md`
