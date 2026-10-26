# Session handoff, 2026-10-26: release 120 live (Google directions and Street View links)

Follows `session-handoff-2026-10-23-release-119.md`. Facts first.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main`, HEAD `7d6ef31` pushed to GitHub and Bitbucket (fetch-verified); this handoff commit is local only. | 2026-10-26 |
| LCDash pilot (AWS) | foundation stack `UPDATE_COMPLETE` 11:32:55 EDT via change set `release-7d6ef31-20261026`; web task definition **revision 120**, image tag `release-7d6ef3183fd8`, digest `sha256:4d02e77f96ca0414b02e06ce25ad04f6243d760a0af4adaee41173d9111d45d1`; rollout COMPLETED 1/1; ALB target `10.42.1.170:8000` healthy; `/active-calls` 302 to Cognito; 0 errors in 41 web-log events since the rollout. | 2026-10-26 11:36 EDT |
| Rollback target | change set with digest `sha256:1e0fc3a58636e210baac21d851380a8136a62bf3b31d5f683ab2dc1144023780` (revision 119). | |
| Release build | CodeBuild `lcdash-p1-logan-use1-release-builder:6d91a57f-0302-4e1f-82db-2e46aff5c89a`, SUCCEEDED 11:28:40 to 11:29:30 EDT, `source_asset_sha256` `52bfc6655731cfe748d7a91b141c2513e4d4e5af4a68b5457d80be44805c8451` (matches the release-builder deploy output). | 2026-10-26 |
| ECR scan (new digest) | 9 findings, exactly the accepted baseline (util-linux CVE-2026-78408/78409/78410/76642/53612/53613/53614/27456, zlib CVE-2026-85091). | 2026-10-26 |
| Tests | application 2059 passed, 5 skipped; infrastructure 151 passed (on `7d6ef31`). | 2026-10-26 |

## What shipped in revision 120 (commit `7d6ef31`)

- Incident detail page: two buttons under the location map, on both
  `call_detail_cloud.html` (pilot) and `call_detail.html` (legacy):
  **Google driving directions** (`maps/dir/?api=1&destination=<lat>,<lon>&travelmode=driving`)
  and **Google Street View** (`maps/@?api=1&map_action=pano&viewpoint=<lat>,<lon>`,
  the same form the Station Alerts page already used).
- Rendered only inside the existing verified-coordinate guard, so they
  disappear with the map. Only the validated latitude/longitude goes to
  Google: no address text, CFS number or narrative. `target="_blank"
  rel="noopener noreferrer" referrerpolicy="no-referrer"` so the LCDash URL
  (which carries the CFS number) is not sent as a referrer.
- Tests: `test_cloud_detail_map_renders_only_for_verified_coordinates`
  extended and `test_legacy_detail_offers_the_same_directions_link_only_with_coordinates`
  added in `tests/contracts/test_cloud_cad_display_bridge.py`.

## Not verified

- No signed-in page check (release rule 6): the agent does not hold Cognito
  credentials. Ted should open a live call at `https://aws.logan911.com`,
  confirm the caller name and role render, and that both buttons appear
  under the map and open Google in a new tab.
- The local synthetic server has no incident data (CAD disconnected), so the
  buttons were verified by the Jinja render test, not in a browser.

## Open items

1. Push this handoff (Ted's yes).
2. Ted's signed-in check above.
3. Carried from 2026-10-23: Moodle request logging; LMARS voice; DMR catalog
   cleanup about 2026-11-20; ISO edition; vendor PHP exposure; GuardDuty root
   finding; `HOME`/`XDG_CACHE_HOME` pointing into the unwritable `/tmp` in the
   task definition (latent); NexisAlert: watch for the first informational
   `no_recipients` on DPS564/DPS605/STATEMS; uploader parking has still not
   fired in production.

## Release notes learned today

- `cdk deploy --method=prepare-change-set` with only
  `--parameters PilotImageDigest=<digest>` is the right call: CDK keeps the
  other 22 parameters from the stack. Re-passing them explicitly fails,
  because `describe-stacks` returns the NoEcho `CloudCadReadSecretArn` as a
  mask and the template's constraint rejects it. (Corrects the 2026-10-23
  "pass the other 22 explicitly" note.)
- In PowerShell never splat a variable named `$args` (automatic variable);
  it passed nothing and produced an empty FAILED change set, which had to be
  deleted before the name could be reused.
- `cloudformation:ListChangeSets` is not in the deployment permission set;
  `DescribeChangeSet` by name is.
- The ECS cluster is `lcdash-p1-logan-use1-cluster`, service
  `lcdash-p1-logan-use1-web`; read them from `list-stack-resources`.

Services were left running; nothing was stopped or disabled. The
release-builder stack was updated (source asset only), as every release does.
