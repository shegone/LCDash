# Session handoff, 2026-09-14

Scope: everything that changed on 2026-09-14 for LCDash (the AWS pilot),
the NGA911 academy repository, and the on-premises server `.227`. Written
from Git, live read-only AWS state, and SSH inspection, not from memory.
Read the September 10 handoff first if the cards page is the topic; this one
is about platform state.

## Headline

1. **A three-day identity outage was found and fixed.** From 2026-09-11 15:10
   every request to `aws.logan911.com` failed ALB identity verification and
   all 39 pilot users sat in the "Not identified / Restricted (fallback)"
   state. Cause: PyJWT 2.14.0, published that morning, rejects the padded
   base64 segments the load balancer emits, and the open pin let it into
   that day's build. Fixed in revision 100.
2. **Ted's decision: the AWS pilot is the only LCDash.** The on-premises
   platform on `.227` is retired and deleted; `.227` now hosts only the
   Hermes link's LLM. The repository documents say so.
3. **Everything is pinned now**, and identity failures page the operator.

## LCDash-AWS: commits (all on `main`, GitHub and Bitbucket in sync)

| Commit | What |
| --- | --- |
| `9cfffe5` | First identity fix: strip padding before PyJWT. Shipped as revision 99. **Wrong**: the ALB signs the padded bytes, so signatures then failed. |
| `d3a2641` | Real fix: verify the ES256 signature over the signing input exactly as sent, decode segments tolerantly, cap `PyJWT<2.14`. Regression test builds the token the way the ALB does. Revision 100. |
| `fac304b` | `requirements.txt` pins all 15 direct dependencies to the revision 100 image; new `constraints.txt` pins all 41 packages; both Dockerfiles install with `--constraint`; the release manifest requires the file. |
| `fd02c2d` | Metric filter on the web log + alarm `lcdash-p1-logan-use1-alb-identity-rejections` (3 rejections in 5 min) + SNS topic `lcdash-p1-logan-use1-alerts` emailing the operator. `sns_alerts_exception` recorded in the Phase 1 allowlist (Ted approved). Deployed by change set, four new resources, digest unchanged. |
| `8cdb573` | IAM: `LCDashPhase1Boundary` v4 and `LCDashPhase1Deployment` v2 published. The boundary never allowed `sts:AssumeRole` on the CDK bootstrap roles, so the deployment permission set could not release. Now it can: release-builder stack in scope, CodeBuild start/watch, read-only ECS/ELB/ECR/logs/alarm/topic. `ecs:UpdateService` still denied by design. |
| `fa9dd17` | AGENTS.md rewritten for cloud-only LCDash and Hermes-only `.227`; retirement banners on the on-premises docs; README, workspace, roadmap updated. |
| `bf64437` to `00263d9` | `ONPREM_RETIREMENT_2026-09-14.md`: archive evidence, deletion runbook, completion record. |

Tests at HEAD: application suite 1,183 passed; infrastructure suite 146 passed.

## LCDash-AWS: live state (verified 2026-09-14 morning)

- ECS `lcdash-p1-logan-use1-web` revision 100, image tag
  `release-d3a26412939a`, digest `sha256:2d61fd43…`, rollout completed,
  1/1 running, ALB target healthy. Ted confirmed the badge shows his name
  and admin role.
- Foundation stack `UPDATE_COMPLETE`; the identity alarm is in state OK and
  its email subscription is confirmed.
- Release recipe (works as `lcdash-phase1-deployment` now): release-builder
  `cdk deploy` -> CodeBuild `IMAGE_TAG=release-<hash12>` -> ECR scan (the
  standing util-linux baseline, 4 HIGH + 1 MEDIUM, is the only accepted
  finding set) -> `cdk deploy --method=prepare-change-set` on the foundation
  stack with `PilotImageDigest` -> five-resource scope review -> execute.
  `MSYS_NO_PATHCONV=1` is required in Git Bash. Rollback is a change set
  with an older digest; replaced revisions are deregistered and cannot be
  re-pointed to.
- Read-only status checks also work from the `nga911` IAM profile in the
  same account without an SSO login.

## Academy repository (`nexis-training-academy`, commit `5623abf`)

- `image/pins.env` pins the Moodle image's inputs: php base by digest,
  redis extension 6.3.0, the exact Moodle 4.5.13+ (Build 20260903) tarball
  by sha256 and release string, each plugin by md5 and version. `build.sh`
  reads it, fetches from the bucket's `provisioning/image/artifacts/` cache
  first, verifies everything, and has a `NGA_VERIFY_ONLY=1` mode. Proven by
  a verify build on the demo host; the live `:45-stable` image was not
  touched.

## On-premises server `.227` (all verified over SSH)

- Three LCDash n8n workflows deactivated (cloudflared version check, alarm
  triage, tester onboarding check); n8n restarted and healthy.
- Final PostgreSQL dump `lcdash-final-20260914T122213Z.sql.gz`
  (62,790,645 bytes, sha256 `d3df9996…`) taken from the stopped volume, a
  strict superset of the last good 2026-08-29 backup. Copies: `.227`
  `/srv/lcdash-data/backups/postgresql-final/`, the workstation at
  `E:\Projects\_archive\lcdash-onprem-final\`, and the encrypted Drive
  remote. **The Drive copy is probably unreadable**: its crypt password and
  salt were shredded with the secrets directory. Treat the two plain copies
  as the archive.
- Deleted: all 24 LCDash containers and every LCDash image (agent), the 12
  volumes, the secrets directory (shredded), the recordings (shredded), the
  data folders, and `/srv/lcdash-platform` (Ted). `ollama/ollama:latest`
  kept, it is the Hermes brain image. Disk 451 GB -> 327 GB.
- Cloudflare (Ted's browser session, each confirmed): Access apps "LCDash
  Supervisor Portal" and "LCDash CentralSquare Webhooks", DNS record
  `supervisor.logan911.com`, and tunnel `lcdash-supervisor` deleted.
- Remaining on `.227`: `hermes-brain`, `hermes-media`, `n8n` and its backup
  sidecar, and `/srv/lcdash-data/backups/` (n8n backups + the archive).

## Open items

1. **CentralSquare**: send the revocation request for the old read
   credential and webhook secret (draft is in the 2026-09-14 conversation;
   Ted sends it).
2. **Google OAuth client** for the retired rclone syncs: delete it from the
   Google Cloud project owned by the 911logan.com Workspace account. Chrome
   was signed into the personal Gmail, which has no project.
3. **Moodle**: browser login as `nga911admin` with the rotated password, and
   as `tsparks`, to close the 2026-09-11 rotation.
4. **Cards page gates** unchanged: NGA911 operational-use authorization,
   medical review, AED and birth takes.
5. `deployment/ubuntu-nvidia-227` branch and `E:\Projects\LCDash` checkout
   describe a server that no longer exists; keep the branch, remove the
   checkout when convenient.
6. Nice-to-have: a weekly drift report comparing pins against newest
   releases, so a security fix is noticed without ever changing a build.

## Exact next action

Send the CentralSquare revocation request, then sign into the 911logan.com
Google account in Chrome and delete the rclone OAuth client. Nothing on the
platform is waiting on an agent.

## Codex catch-up

2026-09-14: PyJWT 2.14.0 broke ALB identity for all pilot users from 09-11
15:10; fixed in revision 100 (`d3a2641`, manual signature verification over
the padded signing input, `PyJWT<2.14`). All dependencies pinned
(`fac304b`), an alarm plus SNS email now fires on identity rejections
(`fd02c2d`, allowlist exception approved), and the deployment permission set
can run releases (`8cdb573`, boundary now allows assuming the CDK roles).
Ted declared the AWS pilot the only LCDash; the `.227` platform was archived
(final DB dump in two plain copies) and deleted, Cloudflare tunnel, Access
apps, and DNS removed, docs rewritten (`fa9dd17`). Academy repo's Moodle
image inputs pinned (`5623abf`). Live: ECS revision 100, healthy, alarm OK.
Open: CentralSquare credential revocation and the Google OAuth client.
