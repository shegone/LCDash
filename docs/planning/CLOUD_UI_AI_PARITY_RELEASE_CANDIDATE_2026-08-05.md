# Cloud UI and advisory AI parity release candidate

Status: **LOCAL RELEASE CANDIDATE - DEPLOYMENT HOLD**

## Completed release slices

- Dashboard and Units use one canonical cloud presentation and verified, stale,
  or awaiting source semantics.
- Active Calls detail uses the sanitized command-center hierarchy and only the
  approved normalized read-only field projection.
- Analytics saved widgets are scoped to an immutable deployment tenant; legacy
  unscoped rows are invisible and cross-tenant retirement fails closed.
- MAE, Knowledge, Voice, Mindshare/JACK, and NGA surfaces retain advisory or
  browser-only simulation boundaries. They cannot dispatch, write or acknowledge
  CAD, send alerts/pages/tones/radio traffic, control ESInet, or take operational
  action.
- Polly synthesis is lazy, neural MP3 only, bounded, non-persistent, limited to
  Joanna or Matthew, and remains behind the separately documented one-call gate.
  Conversational voice stays disabled until both synthesis and transcription are
  ready; transcription remains unavailable.
- GIS, heatmap, and reports retain polished unavailable states where broad
  coordinates or approved imported history do not exist.
- Shared responsive command-center rules cover desktop, tablet, and field-phone
  layouts. The standalone NGA shell now has the same accessible mobile drawer as
  the authenticated LCDash shell.

## Security and truthfulness closure

- Cloud Active Calls and Units JSON APIs derive connectivity from the shared
  verified source state rather than snapshot construction success.
- Synthetic/cloud mode denies CentralSquare webhook ingestion before secret
  validation or request-body processing.
- Trusted tenant context is composed only from `LCDASH_TENANT`, which is bound by
  the deployment definition and never accepted from request input.
- Legacy MAE/JACK chat, feedback, evaluation, and memory mutation routes fail
  closed in cloud mode until tenant-scoped advisory repositories exist. Their
  review/list surfaces return empty unavailable views instead of global state.
- The task role remains free of dispatch, alert, page, tone, radio, ESInet, and
  CAD-write permissions. Polly has synthesis-only permission; Transcribe is absent.

## Local evidence

- Cloud contracts plus infrastructure policies: **414 passed, 1 skipped, 204
  subtests passed**.
- Focused AI/Knowledge/Voice contracts: **28 passed**, plus direct readiness and
  component checks.
- MAE/Mindshare/NGA simulation parity: **6 passed**.
- Dashboard/Units/source/webhook/tenant presentation: **6 passed**.
- Mobile structural baseline: **5 passed**.
- Python compilation and scoped diff integrity pass.
- A repository `pytest.ini` now limits discovery to `tests` and
  `infrastructure/tests`; executable helpers under `infrastructure/work` cannot be
  collected as tests.

The broad legacy application suite currently records 528 passes and 21 failures.
Those failures are older on-prem expectations for direct service calls, legacy
live wording, unscoped advisory mutations, and pre-cloud NGA labels. They do not
invalidate the cloud safety contract, but they must be reconciled or explicitly
split into an on-prem suite before this complete UI bundle is deployed.

## Provider and deployment gates

No provider smoke call is authorized by this package. The separate
`CLOUD_AI_KNOWLEDGE_VOICE_RELEASE_READINESS_2026-08-05.md` gate requires a named
approver, bounded window, verified account/region/task role/revision/image digest,
and exactly one short Joanna-or-Matthew neural MP3 Polly request. It forbids retry,
persistence, logging of content, Transcribe, Bedrock generation, CAD, and actions.

Deployment remains a separate decision after review of the immutable image,
CloudFormation change set, rollback digest, and authenticated visual checks.

## Operational evidence and open safety item

Production `.227` was checked read-only using the documented SSH identity. The
host, Docker, LCDash, PostgreSQL, Open WebUI, computer workspace, local speech, and
Qwen TTS services were healthy; expected loader jobs had exited successfully.
No CAD/provider generation/write/restart/backup/output action occurred.

One on-prem exposure must be reconciled separately: Compose currently publishes
Ollama at `14.1.1.227:11434`, while server documentation says Ollama is not
host-published. This may be the later PC `.15` gateway design, but firewall and
authentication intent must be confirmed before the exposure is called safe.

## Contained test-discovery incident

A broad recursive pytest command collected an executable helper under
`infrastructure/work` and started one CodeBuild job. The job was immediately
stopped during `PRE_BUILD`; AWS phase history confirms no `BUILD` or `POST_BUILD`
phase occurred, so no image build, push, or deployment ran. The new pytest
discovery boundary prevents recurrence.

No commit or push is included in this release candidate.
