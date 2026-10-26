# Session handoff, 2026-10-26: `.227` froze for 28 hours, radio listener recovered after a hard reboot

Follows `session-handoff-2026-10-26-release-120.md`. Verified facts first; no
credentials, no transcript text.

## State at a glance

| Item | State | Last verified |
| --- | --- | --- |
| `.227` | up since 11:53:29 EDT after Ted's hard reboot (it was powered on but the display showed static and it answered nothing on the LAN). Kernel 6.8.0-147, NVIDIA 595.91.07, RTX 3090 at 45 C, 5.5 GiB of 125 GiB used, disk 16%. | 2026-10-26 11:55 EDT |
| Radio services | `mindshare-radio-capture`, `-transcribe`, `-upload` all `active (running)` since 11:53:36, 0 restarts. Capture: 36 listeners, traffic on 6. Transcriber: `large-v3`, cuda, glossary_mode off. Uploader: heartbeat 200, segments 201. | 2026-10-26 11:55 EDT |
| Backlog | none: 0 audio, 0 queued, 0 parked. Nothing was captured while the host was frozen, so there was nothing to drain. The one transcript without an upload marker is `pipeline_check_law_tx_20261016`, the intentionally skipped prefix. | 2026-10-26 11:55 EDT |
| Pilot | `radio-heartbeat ok listeners=36` at 60 s cadence from 11:54:36; 4 ingests in the first 10 minutes, all 201; no 4xx/5xx. Alarm `lcdash-p1-logan-use1-radio-listener-silent` was still `ALARM` at 11:58 and clears on its own after two clean 5-minute periods. | 2026-10-26 11:58 EDT |
| Outage window | last ingest 2026-10-25 07:21:49 EDT, last heartbeat 07:21:01; alarm fired 07:42:30; first heartbeat after recovery 2026-10-26 11:53:36. About 28.5 hours of radio audio were never captured. | |

## What the evidence says about the freeze

- `journalctl --list-boots`: boot `-1` ran 2026-10-16 14:52:48 to 2026-10-25
  07:21:49 and has no shutdown entry. Its last lines are ordinary capture
  stats, a transcription and two `status=201` uploads at 07:21:49. Logging
  simply stops: a hard freeze, not a crash that got to write anything.
- No NVRM/Xid, MCE, hung-task or lockup lines anywhere in that boot; the
  only kernel errors (7) are at its 14:52 start on 10-16. No fwupd or other
  warning near the time. The cause is not in the logs.
- No hardware watchdog: `/dev/watchdog` absent and `RuntimeWatchdogSec` not
  set in `/etc/systemd/system.conf`. That is why a frozen box stayed frozen
  for 28 hours instead of rebooting itself.
- The silent-listener alarm did its job (fired within 21 minutes), but
  nobody acted on it until the page looked wrong a day later.

## Recommendations (separate work packages, each needs Ted's approval)

1. Hardware watchdog on `.227`: load the platform watchdog driver (`iTCO_wdt`
   or whatever `dmesg` shows for this board) and set
   `RuntimeWatchdogSec=60` in `/etc/systemd/system.conf`, so a future freeze
   reboots the host within a minute and the radio services come back on
   their own (they are enabled and started cleanly today). Needs a planned
   reboot to prove it.
2. Route the `lcdash-p1-logan-use1-radio-listener-silent` alarm somewhere
   someone will see it at 07:42 on a Saturday (phone-visible subscription on
   the alerts SNS topic, or a second subscriber).
3. Watch for a repeat. If it freezes again, before rebooting note whether
   the keyboard LEDs toggle and whether the GPU fans spin; that separates a
   kernel/GPU hang from a hardware or power fault. Consider a
   `journalctl` persistent `Storage=persistent` check (it is persistent today,
   which is how boot `-1` was readable).

## Not verified

- The exact cause of the freeze (nothing in the logs).
- Whether Hermes (Ollama, ComfyUI, n8n) came back; not checked, out of this
  package's scope.

## Open items

1. Push this handoff (Ted's yes).
2. ~~Decide on the watchdog~~ **Done 2026-10-26 16:40 EDT**: recommendation 1
   is applied and proven from a cold boot; see the Applied table in
   `docs/PC227_WATCHDOG_PLAN.md`. Alarm routing (recommendation 2) is still
   open.
3. Carried from the release-120 handoff.

Services on `.227` were left running; nothing was changed there or in AWS
during this check (read-only SSH and CloudWatch reads only).
