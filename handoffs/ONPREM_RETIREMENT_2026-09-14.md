# On-premises LCDash retirement

Date: 2026-09-14
Decision: Ted Sparks, 2026-09-14. The AWS pilot is the only LCDash. Server
`14.1.1.227` keeps only the Hermes link's LLM (the two Hermes stacks, n8n and
its backup sidecar). Everything LCDash on it is to be removed.

This record has two parts. Part A is done and verified. Part B is the
deletion itself, which is irreversible and is run by a person, not an agent.

## A. Archive taken and verified (done)

The on-premises PostgreSQL database is the only LCDash data that exists
nowhere else: the cloud pilot never imported it (Phase 2 import was never
authorized). The automatic backups had failed twice; the last good one was
2026-08-29 and the database kept collecting until the stack stopped on
2026-09-09. So a final dump was taken directly from the stopped volume.

| Item | Value |
| --- | --- |
| Method | throwaway `postgres:17` container, no network, `pg_dump --no-owner --no-privileges`, gzip -9 |
| File | `lcdash-final-20260914T122213Z.sql.gz`, 62,790,645 bytes, 22 tables, 22 COPY blocks, gzip integrity verified |
| sha256 | `d3df99961f67ae94ced7df3d993077e776ea985e3efa7e1ea426cb294b479768` |
| Local copy | `/srv/lcdash-data/backups/postgresql-final/` on `.227` (mode 700 directory) |
| Offsite copy | encrypted rclone remote `lcdash-backup:server-227/postgresql-final/`, same 62,790,645 bytes |
| Content check | strict superset of the 2026-08-29 dump in every table; for example calls 4,573 vs 4,542, unit_responses 5,425 vs 5,393, webhook_events 6,176 vs 6,114; newest call timestamp 2026-08-29 20:54 UTC |

Also already offsite on the same remote from July: `knowledge-20260729T153539Z.tar.gz`
(the document library) and `openwebui-pre-v0.11.0-20260729T161713.tar.gz`.
Everything under `documents/` is a synced copy of Google Drive folders and is
re-creatable from Drive.

Nothing was deleted in Part A.

## B. Deletion runbook (a person runs this)

Run as `administrator` on `.227`, in order. Each step is separately
reversible until step 4, which is the point of no return. Read the whole
list first.

### 1. Confirm the archive is where you expect

```bash
sha256sum /srv/lcdash-data/backups/postgresql-final/lcdash-final-20260914T122213Z.sql.gz
docker run --rm --user 1000:1000 -v lcdash-platform_lcdash_rclone_config:/config/rclone rclone/rclone lsl lcdash-backup:server-227/postgresql-final/
```

Both must show 62790645 bytes and the sha256 above. Optionally copy the file
to a second place you control (a USB drive or your workstation, never a Git
repository) before continuing.

### 2. Remove the compose project, its containers, and every LCDash volume

This deletes the live database volume (229 MB), the Open WebUI data (chat
history, 1 GB), the model caches (about 78 GB), and the rclone config volume.

```bash
cd /srv/lcdash-platform/current/deploy
docker compose -p lcdash-platform down --volumes --remove-orphans
docker volume ls | grep lcdash
```

The second command must print nothing.

### 3. Remove the LCDash images (about 110 GB)

```bash
docker image ls --format '{{.Repository}}:{{.Tag}}' | grep -E '^(lcdash-|ghcr.io/open-webui/|ghcr.io/speaches-ai/|ollama/ollama|postgres:17|rclone/rclone|cloudflare/cloudflared)' | xargs -r docker image rm
docker image prune -f
```

Hermes uses its own images under its own compose projects; this pattern does
not touch them. Check with `docker compose ls` afterwards: `hermes-brain`,
`hermes-media`, and `n8n` must still be running.

### 4. Remove the deployment tree and the data folders (point of no return)

The deployment tree holds the secrets directory. The data folders hold the
synced document libraries, the GIS reference layers, agent workspaces, and 68
audio benchmark recordings, some of which are real call audio.

```bash
sudo find /srv/lcdash-platform/secrets -type f -exec shred -u {} +
sudo rm -rf /srv/lcdash-platform
sudo find /srv/lcdash-data/recordings -type f -exec shred -u {} +
sudo rm -rf /srv/lcdash-data/recordings /srv/lcdash-data/documents /srv/lcdash-data/agent-workspaces /srv/lcdash-data/gis-public /srv/lcdash-data/gis-source /srv/lcdash-data/exports /srv/lcdash-data/models
```

### 5. Decide about the old dumps

`/srv/lcdash-data/backups/postgresql/` holds 26 daily dumps (two of them
empty), all superseded by the final archive and all already offsite. Remove
them or keep them; they are harmless either way.

**Keep** `/srv/lcdash-data/backups/n8n/` and `/srv/lcdash-data/backups/postgresql-final/`.
The n8n backup sidecar still writes to the first; the second is the archive.
The data disk itself stays mounted for those.

### 6. Outside the server (Cloudflare and credentials)

These are account-level changes and need you in the respective dashboards:

- Cloudflare: **done 2026-09-14** (Ted approved; performed in his browser session,
  each item confirmed in Cloudflare's own dialog): Access applications
  "LCDash Supervisor Portal" and "LCDash CentralSquare Webhooks" deleted,
  DNS record `supervisor.logan911.com` deleted (zone 33 -> 32 records),
  tunnel `lcdash-supervisor` deleted. The tunnel token in the secrets
  directory is now useless and goes with step 4.
- CentralSquare: the read credential and webhook secret that lived in the
  secrets directory are no longer used anywhere; ask the vendor to revoke
  them.
- Google Cloud: the OAuth client used by the rclone Drive syncs can be
  deleted once you no longer need to read the offsite archive through it.
  Do that last, and only after copying the archive somewhere else if you
  want to keep it beyond Drive.
- Open WebUI and Open Terminal API keys die with the containers; nothing to
  revoke elsewhere.

### 7. Repository

After step 4, the `deployment/ubuntu-nvidia-227` branch and the
`E:\Projects\LCDash` checkout describe a server that no longer exists. Keep
the branch as history; delete the checkout folder when convenient.

## What stays on .227

`hermes-brain`, `hermes-media`, `n8n` and `n8n-workflow-backup`, the data
disk at `/srv/lcdash-data` with `backups/n8n/` and `backups/postgresql-final/`.
