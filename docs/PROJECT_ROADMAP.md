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
