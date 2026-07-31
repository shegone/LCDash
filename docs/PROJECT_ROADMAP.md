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

## Phase 3 - Mapping
- GIS map
- Unit locations

## Phase 4 - Analytics
- Response times
- Supervisor dashboards

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
- Controlled end-to-end production CAD event test - deferred

## Phase 7 - Local AI Quality

- MAE read-only tool routing - completed
- MAE Reliability Center - completed
- JACK product-focused document retrieval - completed
- JACK 30-question baseline evaluation - completed, 30/30 passed
- JACK supervisor feedback controls - completed
- Approved local learning and correction workflow

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
- Use the standard announcement pattern:
  `Station {station}, respond to {address} for a {call type}. Time is {24-hour time}.`
- Example:
  `Station 100, respond to 911 Mark Spurlock Drive for a structure fire. Time is 1523.`
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
- Do not make NGA911 cloud intelligence a dependency of call routing, CAD,
  radio, station alerting, or other emergency operations
- Replace the mock provider only after NGA911 supplies approved API,
  authentication, tenant, retention, audit, and service-level requirements
