# AWS Package 1A dependency inventory

This inventory records the inherited direct service dependencies before provider
interfaces are introduced. It is a source-code inventory, not evidence that any
service is configured or reachable. The scan covered Python files under `app/`,
`scripts/`, and `deploy/` on 2026-08-04. No service was contacted and no secret
or operational data was read.

## CentralSquare CAD

- Transport and authentication: `app/auth/oauth.py` posts the credential grant;
  `app/services/centralsquare.py` directly performs HTTP GET, POST, and PUT
  requests and obtains its bearer token from that OAuth module.
- Normalized application reads: `app/services/cad_service.py` calls CFS search
  and detail; `app/services/unit_service.py` calls paginated unit search;
  `app/services/operations_service.py`, `app/services/map_service.py`, and
  `app/services/heatmap_service.py` build operational views from those reads.
- Analytics and reports: `app/services/analytics_collector.py` calls CFS search,
  CFS analytics, and unit search; `app/services/county_commission_report_service.py`
  performs paginated CFS search.
- Assistant access: `app/services/mae_service.py` directly constructs a
  `CentralSquareClient` for bounded recent-call lookup and imports normalized
  CAD/operations functions. JACK (`app/services/mindshare_service.py`) does not
  import or call CentralSquare.
- Operational paths inherited but outside AWS activation scope:
  `app/services/ems_delay_alert_service.py` can call the CAD command endpoint;
  `app/services/station_alert_service.py` reads CAD for alert preparation;
  `app/services/realtime_service.py` receives already-delivered webhook data;
  `scripts/register_centralsquare_subscriptions.py` directly performs
  subscription POST/PUT operations. These paths must remain disabled.
- Other direct importers/entry points: `app/main.py`,
  `scripts/analytics_worker.py`, `scripts/backfill_dispatcher_names.py`,
  `scripts/ems_delay_alert_worker.py`, `scripts/inspect_subscription_agencies.py`,
  `scripts/sync_analytics.py`, and `scripts/test_ems_delay_page.py`.

## Ollama and local inference

- `app/services/mae_service.py` directly uses `httpx.get`, `httpx.post`, and
  `httpx.stream` against `settings.ollama_base_url` for status, chat, and
  streaming chat.
- `app/services/mindshare_service.py` uses the same direct HTTP patterns for
  JACK. Its policy boundary can return deterministic refusals before retrieval
  or inference.
- `app/services/nga911_nova_service.py` directly calls Ollama model-list and
  chat endpoints.
- `app/services/knowledge_service.py` and `scripts/index_knowledge.py` directly
  call the Ollama embeddings endpoint.
- `scripts/jack_reliability_baseline.py` directly calls a configured local chat
  endpoint for offline evaluation.

## Knowledge and retrieval

- `app/services/knowledge_service.py` combines direct PostgreSQL access,
  full-text SQL, local-file resolution, semantic candidates, and direct Ollama
  embedding requests. It has no provider boundary.
- `app/services/mae_service.py` and `app/services/mindshare_service.py` directly
  import knowledge search functions; JACK also imports document-passage and
  status functions.
- `scripts/index_knowledge.py` directly reads approved source documents, writes
  the knowledge schema through psycopg, and requests embeddings. The
  `knowledge_worker.py` and `mindshare_knowledge_worker.py` scripts invoke it.
- `app/main.py` directly exposes knowledge status, listing, and approved file
  retrieval routes.

## Speech

- `app/services/voice_service.py` directly constructs `httpx.Client` instances
  for STT/TTS health, model discovery, speech synthesis, and transcription.
  Endpoint selection comes from `app/config/settings.py`.
- `app/main.py` directly imports voice status, synthesis, and transcription.
- `app/services/station_alert_service.py` imports only deterministic spoken-time
  formatting from the voice service; live station audio remains out of scope.
- Local service implementations are under `deploy/parakeet-stt/`,
  `deploy/parakeet-benchmark/`, `deploy/qwen3-tts-canary/`,
  `deploy/qwen3-tts-jack/`, and `deploy/chatterbox-canary/`. They are deployment
  artifacts, not provider adapters.

## GIS and maps

- `app/services/gis_reference_service.py` directly reads allowlisted local
  GeoJSON from the directory configured by `GIS_REFERENCE_DIR`.
- `app/services/map_service.py` directly consumes the normalized live unit
  snapshot; `app/services/heatmap_service.py` directly calls CentralSquare CFS
  search and normalizes coordinates.
- `app/main.py` directly imports GIS reference, map, and heatmap services.
  There is no external managed GIS client or identity-aware GIS provider yet.

## Database

- Direct psycopg connection owners are `app/services/analytics_database.py`,
  `app/services/ems_delay_alert_database.py`,
  `app/services/knowledge_service.py`, `app/services/realtime_service.py`, and
  `scripts/index_knowledge.py`.
- `AnalyticsRepository` is directly imported by analytics reporting, MAE/JACK
  audit, evaluation, memory, and visualization services, plus MAE query logic
  and database setup/backfill scripts. It is a shared concrete repository, not
  a tenant/provider abstraction.
- EMS-delay and realtime services own separate concrete repository classes.
  Database configuration is read directly from `settings.database_url`.

## Identity and authorization

- The only authentication client is `app/auth/oauth.py`, and it authenticates
  the server to CentralSquare; it is not human login or tenant identity.
- `app/main.py` directly calls that CAD OAuth function for its token diagnostic
  route and uses a configured shared secret/basic-auth compatibility path for
  webhook authorization.
- No Cognito, OIDC user federation, JWT verification, immutable tenant context,
  role authorization service, or tenant-aware identity provider exists in the
  scanned application code. Personnel identity normalization in
  `app/services/analytics_models.py` is CAD data normalization, not access
  control.

## Package 1A boundary conclusion

CentralSquare transport, Ollama inference, knowledge retrieval, speech, GIS,
database access, and identity are all concrete dependencies today. Package 1B
must place new protocols around them without activating the inherited CAD
write/subscription, webhook, station-alert, EMS, paging, or other operational
paths. Package 1A makes no such refactor.
