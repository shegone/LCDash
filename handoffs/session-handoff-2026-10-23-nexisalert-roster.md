# Session handoff, 2026-10-23 (later): NexisAlert roster, academy push, IAM exception

Follows `handoffs/session-handoff-2026-10-23.md` (read that first; this file
closes its open items 1, 3 and 4). Verified facts are separated from
recommendations. Nothing here contains a credential.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | branch `main`, three local commits after `13bab10`: `5c0df82` (IAM exception applied), `c6992ab` (removed), and this handoff. NOT pushed; GitHub and Bitbucket are still at `13bab10`. | 2026-10-23 |
| LCDash pilot (AWS) | unchanged this session: web task definition revision 118, image `release-fdf657f7a404`. Not re-read today. | 2026-10-21 |
| IAM policy `LCDashPhase1Deployment` | default version `v2` (2026-09-14), identical to the repo file; `v3` deleted. `ecs:ExecuteCommand` denied again for the deployment set (checked). | 2026-10-23 16:07 UTC |
| NexisAlert roster (production RDS, tenant `logan`) | 272 units (was 266): `DPS564`, `DPS605`, `DPS108` under `WVSP` from CAD; `STATEMS`, `JANCARE`, `Z100` under the hand-made `OUT OF COUNTY` station. No responders attached to any of them. | 2026-10-23 16:05 UTC |
| `nexis-training-academy` repo | PHP pin pushed to Bitbucket `nga911rnd` as `6254c64` on `main` (cherry-pick of the detached `69cca20`). | 2026-10-23 |
| Tests | infrastructure 151 passed after the removal (152 while the exception existed). Application suite not run (no application code changed). | 2026-10-23 |

## What was done

1. **Open item 1 (page checks):** Ted checked `/analytics` and `/mindshare/radio`
   by eye and confirmed them correct. Closed.
2. **Open item 3 (academy push):** `69cca20` was on a detached HEAD in
   `C:\projects\nexis-training-academy`; cherry-picked onto `main` in the
   `C:\projects\_worktrees\academy-main` worktree as `6254c64` and pushed.
   `git ls-remote` confirms. The detached `69cca20` is left in place.
3. **Open item 4 (NexisAlert alarms):** Ted chose "add the units to the roster".
   - There is no admin console UI yet (only the admin API), the RDS is VPC-only,
     and the deployment set cannot `ecs:RunTask`, so the only path was ECS Exec,
     which the deployment set lacked. With Ted's typed approval a temporary,
     cluster-scoped `ecs:ExecuteCommand` statement was applied as policy `v3`
     (documented in `docs/planning/PHASE1_IAM_REVIEW.md`, "Approved exceptions")
     and removed the same hour. The `ssm:StartSession` deny did not block ECS Exec.
   - Inside the container, `DATABASE_URL` is not in the exec shell's
     environment; it was assembled from the container's `DB_*` variables exactly
     as `backend/entrypoint.sh` does. Nothing was printed.
   - Read-only look: 22 agencies, 34 stations, 266 units, all CAD-synced.
   - `sync-roster --dry-run`, then (approved) the real run: 3 units created
     (`DPS564`, `DPS605`, `DPS108`, all `WVSP` / `PATROL CAR`), 0 updated, 266
     unchanged; 3 skipped because CAD gives them no station (`STATEMS`,
     `JANCARE`, `Z100`).
   - Ted said `STATEMS` is a unit from another county and chose an out-of-county
     station, with the other two skipped units placed there as well. Inserted
     (approved): agency `Out of County Mutual Aid` (short name `OOC`, discipline
     `other`), station `OUT OF COUNTY`, units `STATEMS`, `JANCARE`, `Z100`
     (`unit_type` empty). Verified by a read after commit.
   - Effect: the delivery sweep's `no_recipients` check now finds a matching
     `units` row for all three alarmed units, so those dispatches log at
     `informational`, not `high`. No deploy was needed; the sweep reads the
     table live. NOT yet observed on a real dispatch.
4. The AWS Session Manager plugin was needed for ECS Exec. With Ted's approval
   the official zip was downloaded to the session scratchpad and the binary run
   from there; the installer was NOT run and nothing was added to `PATH`.

## Open items and risks

1. **Push `main`**: three local commits (`5c0df82`, `c6992ab`, this handoff) are
   not on GitHub or Bitbucket. Needs Ted's explicit yes.
2. **Confirm the alarm change on a real dispatch**: next time CAD names `DPS564`,
   `DPS605` or `STATEMS`, the `/nexis-alert/logan/api` log line should read
   `severity=informational` and no `nexis-alert-logan-delivery-alarm-high` email
   should arrive. Until then this is expected, not observed.
3. `sync-roster` will keep reporting `STATEMS`, `JANCARE`, `Z100` as "skipped: no
   resolvable station" (it matches units by number only for updates when the
   station resolves; these never will). Harmless; the hand rows persist because
   the sync never deletes.
4. The `Out of County Mutual Aid` agency appears last in the mobile app's
   Departments list (`GET /api/agencies`, discipline `other`). Cosmetic; if it
   bothers users, the app could hide `other`.
5. Carried from the earlier handoff: Moodle request logging (item 2), LMARS voice
   (5), DMR catalog cleanup about 2026-11-20 (6), ISO edition check (7), vendor
   PHP exposure (8), GuardDuty root-credential finding (9).

## How to work (lessons)

- Identity Center permission set `LCDashPhase1Deployment` has NO inline policy;
  it references the customer-managed IAM policy `LCDashPhase1Deployment` (plus
  `LCDashPhase1Boundary` as boundary). A temporary grant is a new policy
  version set as default; removal is `set-default-policy-version v2` then
  `delete-policy-version`. IAM allows 5 versions.
- ECS Exec needs the Session Manager plugin locally and `ecs:ExecuteCommand` on
  the caller; the task's `ExecuteCommandAgent` was already `RUNNING`. The
  container name is `ApiContainer`, not `backend` as `docs/DEPLOYMENT.md` says.
- NexisAlert management commands in the exec shell need
  `DATABASE_URL` exported first (see `backend/entrypoint.sh`, line 37).
- `git -c core.safecrlf=false commit` avoids the CRLF warning aborting nothing
  but cluttering output on this checkout.

## Exact next action

Ask Ted whether to push `main` (three commits). Then wait for a real
`DPS564`/`DPS605`/`STATEMS` dispatch and read the log line to close item 2.

Services were left running; nothing was stopped or disabled. The temporary IAM
grant is gone.
