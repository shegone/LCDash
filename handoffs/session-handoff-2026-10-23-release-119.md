# Session handoff, 2026-10-23 (end of day): release 119 live, uploader parking on .227

Closes the three earlier 2026-10-23 handoffs' release items. Facts first.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | `main`, HEAD `cedac81` pushed to GitHub and Bitbucket; this handoff commit is local only. | 2026-10-23 |
| LCDash pilot (AWS) | foundation stack `UPDATE_COMPLETE` 14:40 EDT via change set `release-cedac81-20261023`; web task definition **revision 119**, image tag `release-cedac81dff5d`, digest `sha256:1e0fc3a58636e210baac21d851380a8136a62bf3b31d5f683ab2dc1144023780`; rollout COMPLETED 1/1; ALB target healthy; `/mindshare/radio` 302 (sign-in); 0 errors in the web log. | 2026-10-23 14:42 EDT |
| Rollback target | change set with digest `sha256:d2ecc4f39f88f2b5a0569e4cb2e03102b2bb68b0e716901b610d11657eb66ced` (revision 118). | |
| ECR scan (new digest) | 9 findings, exactly the accepted baseline (util-linux CVE-2026-78408/78409/78410/76642/53612/53613/53614/27456, zlib CVE-2026-85091). | 2026-10-23 |
| `.227` uploader | running the NEW `uploader.py` (md5 `3b5b6370...`, backup `uploader.py.bak-20261023-pre-parking`); `upload_config.json` has `park_after_server_errors: 12` and the skip list is back to `pipeline_check_` only (backup `upload_config.json.bak-20261023-unskip`). Restarted 14:41:21 EDT, heartbeat 200, no restarts. Capture and transcribe untouched. | 2026-10-23 14:42 EDT |
| Proof of the fix | the clip that failed all day (`ems_tx_20261023T074358_566354Z_54ebe071`, 1,142,050 bytes) uploaded `status=201` at 14:41:21 from the new task. | 2026-10-23 |
| Tests | application 2058 passed, 5 skipped; infrastructure 151 passed (on `cedac81`). | 2026-10-23 |

## What shipped in revision 119 (commits `f7522c6`..`cedac81`)

- Ingest keeps the audio part in memory (no temp file): clips over 1 MiB work.
- Heartbeat accepts optional `head_failing_since`, `head_attempts`, `parked_total`;
  the radio page's Queues card warns on a head-of-queue stall and on parked
  segments. Cache-buster bumped to `20261023-radio12` (script and stylesheet).
- Listener side (now on `.227`): the uploader parks a segment after 12
  consecutive 5xx replies and carries on; re-queue = delete the `.parked` marker.

## Not verified

- The page's new warning lines have not been seen in a browser (no agent
  sign-in) and will only render when a stall actually happens.
- Parking has not fired in production (nothing has failed since the release).

## Open items

1. Push this handoff (Ted's yes).
2. Carried: Moodle request logging; LMARS voice; DMR catalog cleanup about
   2026-11-20; ISO edition; vendor PHP exposure; GuardDuty root finding;
   `HOME`/`XDG_CACHE_HOME` pointing into the unwritable `/tmp` in the task
   definition (latent); NexisAlert: watch for the first informational
   `no_recipients` on DPS564/DPS605/STATEMS.

## Release notes learned today

- `aws ecr describe-image-scan-findings` takes `--image-id` (singular).
- The foundation stack has 23 parameters including CDK's `BootstrapVersion`;
  pass the other 22 explicitly.
- A cached CodeBuild finishes in under a minute; trust the printed
  `source_asset_sha256` in its log, not the duration.

Services were left running; nothing was stopped or disabled.
