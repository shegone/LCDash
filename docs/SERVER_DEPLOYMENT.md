# LCDash Server Deployment

LCDash runs as a private Docker Compose platform on the Logan County server.

## Production services

- `lcdash-web` - FastAPI/Jinja2 operations dashboard
- `lcdash-postgres` - dedicated PostgreSQL analytics database
- `lcdash-analytics-worker` - recurring CentralSquare completed-call collector
- `lcdash-postgres-backup` - daily compressed database backup
- `lcdash-ollama` - private local AI API with Vulkan acceleration
- `lcdash-open-webui` - authenticated browser interface for local AI

## Security model

- LCDash is published only on server loopback port `8010`.
- Open WebUI is published only on server loopback port `3000`.
- PostgreSQL and Ollama are not published to the host network.
- CentralSquare, database, and Open WebUI secrets are mounted as owner-only files.
- Live public-safety data remains inaccessible from the LAN until LCDash has login,
  role-based permissions, redaction, and audited HTTPS access.

## Server paths

```text
/home/ted/lcdash
/home/ted/lcdash-platform/secrets
/home/ted/lcdash-platform/backups
/home/ted/lcdash-platform/legacy-reference
```

The exact credential record is:

```text
/home/ted/lcdash-platform/secrets/platform-credentials.txt
```

It must remain mode `600` and must never be committed to GitHub.

## Start or update

```bash
cd /home/ted/lcdash
docker compose -f deploy/compose.yaml up -d --build
```

## One-click Windows deployment

After committing and pushing with GitHub Desktop, run:

```powershell
E:\Projects\LCDash\scripts\deploy_server.ps1
```

The deployment tool:

1. Requires the `feature/authentication` branch.
2. Refuses to deploy uncommitted files.
3. Confirms Windows and GitHub point to the same commit.
4. Packages only Git-tracked files.
5. Transfers the release through the dedicated SSH key.
6. Validates and rebuilds the private Docker platform.
7. Checks LCDash and Open WebUI.
8. Automatically restores the previous release if health checks fail.

## Check status

```bash
cd /home/ted/lcdash
docker compose -f deploy/compose.yaml ps
```

## Protected Windows access

Create an SSH tunnel from the Windows workstation:

```powershell
ssh -i "$env:USERPROFILE\.ssh\lcdash_server_ed25519" `
    -L 8010:127.0.0.1:8010 `
    -L 3000:127.0.0.1:3000 `
    ted@14.1.1.177
```

Then open:

```text
LCDash:     http://127.0.0.1:8010/dashboard
Open WebUI: http://127.0.0.1:3000
```

## Analytics

The analytics worker runs every five minutes. It initializes the database schema,
overlaps the previous collection window, and upserts records to avoid duplicates.

Database backups are created daily under:

```text
/home/ted/lcdash-platform/backups/postgresql
```

The default retention period is 30 days.

## Local AI

Open WebUI connects to Ollama only over the private Docker network. Ollama uses the
server's Radeon integrated GPU through Vulkan when supported.

Installed baseline models:

- `qwen3:8b` - fast general questions and future dashboard assistance
- `gpt-oss:20b` - higher-quality reasoning and tool-oriented workflows

Open WebUI provides the general-purpose browser interface. A dedicated API key is
stored in the protected credential record for future authorized integrations.

## JACK reliability records

JACK evaluation results are stored in:

```text
lcdash_analytics.jack_evaluation_runs
```

The complete manual-grounded baseline can be run from the server with:

```bash
cd /home/ted/lcdash
python scripts/jack_reliability_baseline.py \
    --output /home/ted/lcdash-platform/backups/jack-baseline.json
```

The runner calls the local LCDash API sequentially and does not change
Mindshare equipment or source documents.
