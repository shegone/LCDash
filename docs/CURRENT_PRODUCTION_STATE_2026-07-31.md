# LCDash Production State - July 31, 2026

This is a non-secret stopping-point record for the Logan County on-premises
LCDash platform. It does not replace the protected credential record or the
backup restore procedure.

## Source and deployment

- Repository: `shegone/LCDash`
- Production branch: `deployment/ubuntu-nvidia-227`
- Production commit: `554783e` (`Add NGA911 event acknowledgment actions`)
- GitHub `main`, the production branch, and the Windows working copy were
  aligned at `554783e` when this record was created.
- The deployed NGA911 event acknowledgment assets on `.227` contain the latest
  feature and were installed July 31, 2026.
- The Windows working tree was clean before this documentation update.

## Production host

- Hostname: `lcdash-server`
- Address: `14.1.1.227/24` on `eno1`
- Default gateway: `14.1.1.1`
- Address source: DHCP from the local gateway; Ubuntu does not currently have
  a local static address configuration.
- Follow-up: confirm or create an authorized DHCP reservation for `.227` so a
  lease change cannot disrupt production access.

LCDash and Open WebUI remain bound to server loopback. Cloudflare Tunnel and
Cloudflare Access remain the external access boundary. The narrow webhook
bypass remains limited to the CentralSquare integration callback path, where
LCDash performs its own authentication and validation.

## Service health

The following production containers were running during the closing audit:

- LCDash web, PostgreSQL, analytics, knowledge, and Mindshare workers
- EMS delay worker, still subject to its documented safe-delivery controls
- Ollama, Open WebUI, and Speaches
- Cloudflared
- PostgreSQL backup and encrypted off-site backup sync
- Google Drive knowledge synchronization services

LCDash web, PostgreSQL, Open WebUI, and Speaches reported healthy. No service
restart or production configuration change was made during this audit.

## Storage and recovery

- System volume: approximately 1.8 TB total, 6 percent used
- Data volume at `/srv/lcdash-data`: approximately 3.6 TB total, 1 percent used
- A current compressed PostgreSQL backup was present on July 31, 2026.
- The encrypted off-site synchronization status was `complete` for
  `lcdash-backup:server-227`.
- The isolated restore rehearsal remains documented in
  `docs/BACKUP_RESTORE_TEST_2026-07-30.md`.

The encrypted backup scope continues to exclude secrets, the protected
credential record, raw CAD payloads, recordings, and model files.

## Planned continuation

The natural next infrastructure step is to inventory and prepare the second
Windows RTX 3090 workstation as `mae-avatar-01`. Unreal Engine and MetaHuman
rendering stay off `.227`, and the static MAE portrait remains the required
fallback.

The station-alert roadmap remains separate: after authoritative alert tones
finish, MAE may provide one concise local spoken dispatch sentence. Speech must
never delay or block the tones, and the existing visual alert remains the
fallback.

The on-premises LCDash/MAE platform remains operationally separate from the AWS
GovCloud NGA911 upgrade path. Neither cloud intelligence nor demonstration
features may become a dependency of call routing, CAD, radio, station alerting,
or other emergency operations.
