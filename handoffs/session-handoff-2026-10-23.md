# Session handoff, 2026-10-23: radio, analytics, PHP patch, NexisAlert alarm

Written at the end of a long thread (2026-10-20 to 2026-10-23). Start the next
thread by reading this file, then `handoffs/session-handoff-2026-10-20-radio-vhf-channels.md`
(the detailed running log for the radio and analytics work) and `AGENTS.md`.
Verified facts are separated from recommendations below. Nothing here contains a
credential.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| LCDash repo | branch `main`, HEAD `1b01239`, working tree clean, GitHub and Bitbucket both at `1b01239` (checked with `git ls-remote`) | 2026-10-23 |
| LCDash pilot (AWS) | web task definition revision 118, image tag `release-fdf657f7a404`, digest `sha256:d2ecc4f39f88f2b5a0569e4cb2e03102b2bb68b0e716901b610d11657eb66ced`; rollout COMPLETED 1/1, ALB target healthy | 2026-10-21 16:05 EDT (the SSO session has since expired, so this was not re-read today) |
| Rollback target | change set with digest `sha256:d85f5bea27d7402916398b6e01e391d18a3c8de7da48077eb6116b65815c6cc1` (revision 117). A rollback does not undo the DMR relabel migration. | |
| `.227` radio listener | `mindshare-radio-capture`, `-transcribe`, `-upload` all active; 18 channels = 36 listeners; capture uptime 42.9 h; pending and failed queues both 0 | 2026-10-23 |
| Moodle training host | patched to PHP 8.3.36 (see below); site up | 2026-10-22 |
| `nexis-training-academy` repo | commit `69cca20` (the PHP pin) is LOCAL ONLY, not pushed | 2026-10-23 |
| Tests (LCDash) | application 2,049 passed with a real PostgreSQL 17.6 (`LCDASH_TEST_PG_DSN`), 2,044 passed + 5 skipped without; infrastructure 151 passed | 2026-10-21 |

## What was done in this thread

Radio (`.227` listener and the `/mindshare/radio` page), detail in the 2026-10-20 log:
- 18 channels captured: SIRN (`law`, `ems`, `fire`), 12 analog VHF district channels,
  3 LMARS. The 3 DMR channels were removed from the listener (bridged onto their
  analog pairs, so they transcribed every call twice). Stored DMR history keeps
  its labels through three catalog lines in `static/js/lcdash-admin-radio.js`.
- Listener drops any segment whose start-to-close span is 2.1 s or less
  (`skip_segments_up_to_seconds`), because the data showed about 97% of those
  are stock phrases ("Thanks for watching!"). The span includes the 1.5 s silence
  wait, so 2.1 s is about half a second of sound.
- Page: Law / EMS / Fire / Other group tabs with channel chips, discipline colours
  (law blue, EMS green, fire red), Listener health folded behind a button with a
  one-line status, "Auto-play new calls" scanner mode, "Key-up or beep" badge.
- A one-time schema migration relabelled earlier DMR audio stored as
  `ems_north_vhf` / `fire_north_vhf`.

Analytics (`/analytics`):
- Dispatcher table: 90th percentile column, the "> 3 Min" number opens the list of
  those calls (`GET /api/analytics/dispatcher-slow-calls`, opaque dispatcher
  reference, no location or narrative), Average header explained.
- New "Dispatcher Metrics by Discipline" section: Fire, EMS and Law tables per
  dispatcher plus an ISO fire card (2016 NFPA 1221: 90% within 64 s, 95% within
  106 s). Limits are stated on the page (clock starts at CAD call received, not
  the 911 answer; longer-window call types are not marked in CAD).

PHP "PHP2Shell" (CVE-2026-109618 / CVE-2026-109619, unauthenticated RCE, fixed in
PHP 8.5.12 / 8.4.27 / 8.3.36 / 8.2.35, no fix for 8.1 and older):
- LCDash (Python 3.13 on Alpine) and `.227` (Hermes Ollama and ComfyUI, n8n, the
  radio services) run no PHP: not affected.
- The NGA911 Moodle training site (`nexisacademy.logan911.com`, one EC2 host
  `i-0364020cbec9180a0`, account 862772137583, AWS profile `nga911`) ran PHP 8.3.33
  inside Apache (mod_php): affected. Patched 2026-10-22 about 12:28 EDT by bumping the
  pin in `image/pins.env` to `php:8.3.36-apache-trixie@sha256:756c2884677b...`,
  `build.sh` (verify, then real), then `cutover-image.sh`; about 25 s of downtime.
  Verified: container on image `45-stable` (799353603bfc), CLI and Apache module
  report 8.3.36, site HTTP 200 through the ALB, assets load, cron up.
- Full recipe, rollback images and checks: memory note
  `nga911-moodle-php-patch-2026-10-22.md`. Rollback compose file
  `/opt/moodle/docker-compose.yml.pre-php83-36`; rollback image
  `nga911/moodle:45-20260929` (NOT `45-20260907`: its plugin is v1.3 but the
  database records v1.5).
- Compromise check (Ted asked): no evidence of compromise (host logins, accounts,
  persistence, outbound connections, new PHP in moodledata, Moodle admins and
  config, GuardDuty, instance CloudTrail activity all clean). LIMITS: no
  request-level logs exist for Oct 17 to 22 (ALB access logs off, no WAF, no log
  shipping, and the old container's Docker logs were discarded by the cutover).
  A bot has tried `admin`, `admin1`, `admin2`, `administrator` about 100 times a
  day since Oct 10 to 12; those users do not exist, so none could succeed. Ted
  chose "not now" on adding ALB access logs and a login rate limit.

NexisAlert alarm emails (read-only investigation; nothing changed there):
- The emails are one CloudWatch alarm, `nexis-alert-logan-delivery-alarm-high`
  (metric `NexisAlert/logan` `DeliveryAlarmHigh`, from a log metric filter on
  `/nexis-alert/logan/api`: lines with `delivery_alarm` and `severity=high`).
- From Oct 16 to 22 there were 12 high alerts, ALL `condition=no_recipients` with
  `matched_unit_rows: false`: the dispatch named a unit that is not in NexisAlert's
  `units` roster, so nobody could be alerted. Units: `DPS564` x6, `DPS605` x5,
  `STATEMS` x1. None was the real failure type (`no_receipt`, a push sent and never
  confirmed). "Informational" `no_recipients` lines are expected (beta, one enrolled
  user).
- NexisAlert is a separate project (`E:\Projects\NexisAlert`, own AGENTS.md; deploys
  need a named change set and approval). Ted has not decided whether to add those
  units to the roster, downgrade unknown law units to informational, or leave it.

## Open items and risks (most important first)

1. The signed-in `/analytics` and `/mindshare/radio` pages have not been seen by an
   agent (no sign-in possible). Ted should check: 90th % column, the "> 3 Min"
   button, the discipline section and ISO card, the radio "Key-up or beep" badge,
   and DMR history under the EMS and Fire tabs.
2. Moodle: no request logging. Recommended, not applied, awaiting Ted: ALB access
   logs to S3, a WAF rate limit on `/login`, Moodle real client IPs
   (`$CFG->reverseproxy`), `session.upload_progress.enabled=0` in
   `image/php-moodle.ini`, and shipping container logs off the host. Before any
   container recreate, copy `docker logs` off first. Optional: rotate the Moodle
   admin and database passwords (procedure in the academy `HANDOVER.md`).
3. `nexis-training-academy` commit `69cca20` is not pushed (Bitbucket `nga911rnd`).
4. NexisAlert roster decision (above).
5. LMARS voice has never been heard; multicast signalling arrives. Confirm on a real
   transmission.
6. About 2026-11-20 (30 days after the listener change), delete the three DMR lines
   from `CHANNEL_CATALOG` (`ems_dmr`, `fire_dmr`, `law_dmr`); the test
   `test_every_configured_channel_is_in_the_catalog` pins exactly those three.
7. ISO figures come from secondary sources. ISO's schedule and the NFPA text were
   not reachable; check the edition ISO uses for Logan County before quoting the
   64 s / 106 s targets. Phone-answer times (NGA911) would let the ISO view start at
   the 911 answer instead of CAD call received.
8. Vendor PHP exposure not checked by us: CentralSquare, Mindshare, Sound
   Communications, Aladtec, NGA911 products. Cisco is still investigating; the rack
   switch is a Cisco SF300 and was not seen in their advisory.
9. Unrelated, long-standing: the Moodle host's `ecs-agent` container retries every
   ~15 s because it cannot register with any ECS cluster (benign). GuardDuty has one
   low finding (2026-10-03, root credentials used for `ListManagedNotificationEvents`);
   Ted should confirm it was him.
10. Operators call the first three radio cards "SIRN" (confirmed). The old
    "IRP" wording no longer appears.

## How to work (lessons that cost time)

- Releases: AGENTS.md "Production deployment rules". Verified command details are in
  memory `lcdash-release-traps.md`: CDK needs `-c account=862772137583 -c region=us-east-1`;
  the NoEcho CAD secret ARN is readable from the task definition's
  `LCDASH_CLOUD_CAD_SECRET_ARN`; the SSO session expires overnight
  (`aws sso login --sso-session lcdash --use-device-code --no-browser`, then relay
  the code); a change set is executed only after Ted types its exact name. Pushing
  to `main` needs his explicit yes each time.
- The deployment profile cannot `ecs:RunTask`, `cloudformation:ListChangeSets`,
  `secretsmanager:ListSecrets` or `ec2:DescribeSecurityGroups`. One-off database
  fixes ship as a guarded marker-row migration in `database/radio_schema.sql`.
- Schema or SQL changes need a real PostgreSQL 17 test. There is no local Postgres:
  Ted approved a one-time download of the EnterpriseDB 17.6 binaries twice (to the
  scratchpad, used, then deleted). Ask again each time. The live tests run with
  `LCDASH_TEST_PG_DSN="host=127.0.0.1 port=55432 user=postgres"`.
- Moodle host commands run over SSM `AWS-RunShellScript` with profile `nga911`
  (recipe in memory `nga911-moodle-php-patch-2026-10-22.md`). Compare the S3 copy of
  a server-side script with the repo copy before running it.
- Radio files on `.227`: take them from `git show HEAD:radio_listener/<file>` (LF
  endings), back up the old ones, validate with the listener's own `load_config`,
  restart only the capture service.
- The Mindshare workbook `MS3042_SI_LoganCountyWVSystemInformation_v117.xlsx` is
  Ted's source for channels, is edited by hand and renames cards between versions
  (a stale read mislabelled live audio for about 90 minutes), and holds console
  passwords: keep it out of Git and out of replies.

## Exact next action

Ask Ted which of items 1, 2, 4 or 5 he wants first. Nothing is half-done or
deployed in an unverified state. If he wants a release, start from a clean tree at
`1b01239`, run both test suites, then follow the guarded path.

Services were left running; nothing was stopped or disabled.

## Prompt to start the new thread

> Read `E:\Projects\LCDash-AWS\handoffs\session-handoff-2026-10-23.md`, then report
> branch, HEAD and changed files per AGENTS.md. Then ask me which open item to start with.
