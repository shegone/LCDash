# LCDash Project Roadmap

## Vision
Build a secure web-based operations dashboard for Logan County 911 using the CentralSquare ProSuite APIs.

## Phase 1 - Foundation
- OAuth authentication
- API connectivity
- Token management

## Phase 2 - CAD
- Active Calls
- Call Details
- Units
- Dashboard browser refresh resiliency, visible freshness, and manual recovery - completed

## Phase 3 - Mapping
- GIS map
- Unit locations

## Phase 4 - Analytics
- Response times
- Supervisor dashboards
- MAE allowlisted custom chart previews and matching aggregate PDF exports - completed and deployed
- Supervisor-saved custom Analytics widgets using fresh aggregate data - completed and deployed
- Pre-Built Reports catalog and read-only County Commission Monthly Report - completed locally, pending production deployment

## Phase 5 - Integrations
- CommsCoach
- GovWorx
- RapidSOS
- NGA911
- Mindshare technical library - completed
- JACK technical assistant - completed
- JACK Reliability Center - completed
- Mindshare document coverage review - completed
- Mindshare multicast radio transcription - awaiting isolated radio-network connection

## Phase 6 - Secure Remote Access
- Cloudflare Tunnel for the supervisor portal
- Cloudflare Access exact-email supervisor allowlist
- Email one-time PIN authentication
- Google Workspace authentication for `@911logan.com` accounts
- Keep email one-time PIN available as a backup login method
- Create a managed LCDash Supervisors Google Group
- Role-based access and redacted department views
- CentralSquare CFS and unit webhook subscriptions - completed
- Protected browser event stream with 30-second reconciliation - completed
- Metadata-only integration health page - completed
- Controlled receiver and browser-delivery CAD event acceptance - completed

## Phase 7 - Local AI Quality

- MAE read-only tool routing - completed
- MAE Reliability Center - completed
- JACK product-focused document retrieval - completed
- JACK 30-question baseline evaluation - completed, 30/30 passed
- JACK supervisor feedback controls - completed
- Approved local learning and correction workflow - completed for MAE and JACK

## Phase 8 - Mindshare Radio Intelligence

- Confirm dedicated host and radio-network interface
- Inventory multicast addresses, UDP ports, channel names, and codecs
- Prove listen-only packet capture without transmitting
- Decode one approved test channel
- Add private speech-to-text transcription
- Define access, retention, audit, and recording rules
- Keep technical assistant and radio intelligence permissions separate

## Phase 9 - Station Alert Voice Announcements

- Immediately after the audible station alert finishes, have MAE speak one
  concise dispatch announcement through the local text-to-speech service
- Use the standard announcement pattern with natural spoken 24-hour local time:
  `Station {station}, respond to {address} for a {call type}. Time is {spoken 24-hour time}.`
- Example:
  `Station 100, respond to 911 Mark Spurlock Drive for a structure fire. Time is fifteen twenty-three.`
- Keep tones authoritative and never delay, interrupt, or block the initial
  station alert
- Let supervisors configure which fields may be spoken, such as call type,
  assigned station or units, general location, and approved response notes
- Exclude sensitive, restricted, or unverified narrative fields by default
- Provide pronunciation rules for road names, unit identifiers, abbreviations,
  and Logan County place names
- Allow repeat, mute, volume, voice, and speaking-speed controls at each
  authorized station
- Log announcement generation and delivery status without storing unnecessary
  spoken call details
- Fall back to tones and the existing visual alert whenever voice generation
  or station audio is unavailable

## Phase 10 - Modular Products and NGA911 Intelligence

- Build LCDash as one modular platform with independently deployable product
  profiles rather than separate code copies
- Support full LCDash, NGA911 Intelligence-only, Station Alerts-only, supervisor
  operations, and county-selected product profiles
- Keep authentication, authorization, auditing, visual components, integration
  contracts, and deployment tooling in a shared core
- Keep CentralSquare Operations, Station Alerts, MAE, Analytics, Mindshare, and
  NGA911 Intelligence as separately permissioned feature modules
- Use a versioned normalized NGA911 intelligence contract so the demonstration
  provider can later be replaced by approved AWS GovCloud APIs
- Mark every mock NGA911 record and metric as synthetic demonstration data
- Preserve county data isolation and allow only authorized regional roll-ups
- Provide an NGA911-branded standalone shell using the same intelligence module
  when a county does not want the full LCDash product
- Provide county-isolated drill-down views for PSAP health, resilient call
  paths, location-source quality, session trends, and related service events
- Expose the county list and county detail through versioned normalized APIs so
  approved GovCloud providers can replace the synthetic source without
  rebuilding either user interface
- Provide a director-friendly live network view for diverse ESInet paths and
  cloud call-handling positions, with plain-language health states
- Retain at least 14 days of queryable demonstration history for presentations
  and map production retention to NGA requirements before live integration
- Deliver permission-aware visual, audible, and browser alerts with a safe
  synthetic disruption test and detailed interruption investigation pages
- Use current official NGA and NEXiS brand marks sourced from nga911.com for
  demonstrations, retain source attribution, and replace them with the approved
  partner brand package when NGA supplies it
- Provide NOVA, a separately permissioned read-only NGA911 Intelligence
  assistant, for plain-language questions, disruption analysis, spoken answers,
  and director-ready report generation grounded only in authorized NGA data
- Do not make NGA911 cloud intelligence a dependency of call routing, CAD,
  radio, station alerting, or other emergency operations
- Replace the mock provider only after NGA911 supplies approved API,
  authentication, tenant, retention, audit, and service-level requirements

## Phase 11 - Offline Development Operator

- Use the existing Open WebUI as the persistent portal, Open WebUI Computer as
  the primary project workspace, and Open Terminal for quick one-off shell work
- Provide separate Computer workspaces for isolated LCDash development on
  `.227` and Windows/Unreal/MetaHuman work on `.15`
- Give the operator a complete writable development clone while initially
  keeping the deployed checkout, production secrets, databases, backups, and
  Docker control outside the agent mount
- Use the installed `qwen3.5:27b` Ollama model for initial acceptance testing
- Use OpenCode as the first native coding backend, then benchmark it against
  Open Interpreter, Goose, Qwen Code, OpenHands, Cline, and Aider
- Benchmark Qwen3.5 27B, Qwen3-Coder 30B-A3B, Devstral Small 2 24B,
  gpt-oss-20b, and GLM-4.7-Flash using the same local acceptance suite
- Keep production `.227` workloads higher priority than coding inference
- Use PC `.15` for Unreal, MetaHuman, video generation, rendering, Pixel
  Streaming, and portrait LED output
- Prefer structured browser DOM/accessibility tools; allow general Windows or
  Unreal control only as a supervised, explicitly enabled `.15` capability
- Keep credentials out of model memory and add a narrow credential broker only
  after the core workspace passes acceptance testing
- Require confirmation for deployments, GitHub production-branch pushes,
  network/security changes, software installation, credentials, backups, and
  operational outputs
- Keep the agent in an isolated clone or worktree with no direct writable mount
  of production current, secrets, or backups
- Pass the ten-task acceptance benchmark before expanding permissions
- Maintain `LATEST_PC15.md`, `LATEST_PC227.md`, dated snapshots, and a concise
  Codex catch-up section at every meaningful stopping point
- Expand the benchmark to twenty tasks before enabling unattended schedules,
  messaging bots, credential retrieval, or read-only operations inspection
- Follow the detailed target design and phased gates in
  `docs/OFFLINE_AGENT_ARCHITECTURE_RESEARCH_2026-08-01.md`
- Escalate repeated failures, security decisions, architecture changes, and
  production deployment work to hosted Codex
