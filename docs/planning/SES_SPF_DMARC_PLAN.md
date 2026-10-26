# SES sender authentication plan: SPF, custom MAIL FROM, DMARC

Date: 2026-10-26. Status: **PROPOSAL. Nothing in DNS or SES has been changed.**
Every change below is a Cloudflare DNS change or an SES identity change, so each
needs Ted's explicit approval (AGENTS.md, Approvals). Ted applies the records, or
approves the agent applying them.

Related: `docs/planning/SES_EMAIL_SENDER_SETUP_RUNBOOK.md` (how the identity was
created). The sender is `no-reply@logan911.com`
(`PILOT_MAIL_FROM_ADDRESS` in `infrastructure/lcdash_pilot/config.py`). The SES
identity is `logan911.com` in us-east-1, created in the console and not managed by
CDK, so the MAIL FROM change in Part 2 is also a console/CLI change outside
`infrastructure/`.

## 1. Verified facts

Public DNS queried 2026-10-26 through 1.1.1.1 (`nslookup`).

| Name | Type | Current value |
|---|---|---|
| `logan911.com` | TXT (SPF) | `"v=spf1 include:_spf.mail.hostinger.com ~all"` |
| `_dmarc.logan911.com` | TXT | `"v=DMARC1; p=none"` |
| `logan911.com` | MX | 5 `mx1.hostinger.com`, 10 `mx2.hostinger.com` |
| `hostingermail-a/b/c._domainkey.logan911.com` | CNAME | Hostinger DKIM (`hostingermail-x.dkim.mail.hostinger.com`) |
| `bounce.logan911.com` | any | does not exist (NXDOMAIN) |

From the brief, not re-checked here: the SES identity `logan911.com` is verified,
Easy DKIM is enabled, the account has production access, and no custom MAIL FROM
domain is set. A read-only `ses:GetEmailIdentity` from the
`lcdash-phase1-deployment` profile was denied, so the SES side is unconfirmed
until a test message header is read (Part 4).

Hostinger's SPF chain, expanded: `_spf.mail.hostinger.com` is
`v=spf1 include:relay.mail.hostinger.com include:relay.mailchannels.net ~all`.
`relay.mail.hostinger.com` and `relay.mailchannels.net` contain only `ip4:` and
`ip6:` terms. `amazonses.com` contains only `ip4:` terms and `-all`.

## 2. What actually decides inbox placement (read this first)

**Adding `include:amazonses.com` to the apex SPF record will almost certainly not
change how SES mail is treated.** SPF is evaluated against the *envelope sender*
(the Return-Path, also called MAIL FROM), not the visible From address. SES with
no custom MAIL FROM uses an Amazon-owned envelope domain
(`<id>@us-east-1.amazonses.com`), so receivers check *that* domain's SPF, which
already passes. The `logan911.com` apex SPF record is never consulted for these
messages. The apex SPF only matters for mail whose envelope sender is
`@logan911.com`, which is Hostinger mail from staff mailboxes.

Current state, honestly: DKIM passes and is aligned with `logan911.com`, so DMARC
passes. SPF passes but is not aligned (envelope domain is `amazonses.com`). That
is a legitimate configuration. It is also a weaker reputation signal than "SPF and
DKIM both aligned", which is what Part 2B buys.

Authentication is also not the only reason Yahoo files mail as spam. A new sender
domain with little sending history, an HTML message carrying a link and a
temporary password (the invitation template), and no reply path can all push mail
to spam even when SPF, DKIM and DMARC pass. Expect the records below to help
reputation at the margin; do not expect a guarantee. See Part 5.

### 2A. Apex SPF (the change requested in the brief)

Harmless, low value, cheap. Do it only as part of keeping the record accurate.

- Name: `logan911.com`, type TXT, replaces the existing SPF TXT value (there must
  be exactly one `v=spf1` record on the name).
- New value:

  ```
  v=spf1 include:_spf.mail.hostinger.com include:amazonses.com ~all
  ```

- DNS-lookup count (limit 10, RFC 7208): `include:_spf.mail.hostinger.com` = 1,
  its `include:relay.mail.hostinger.com` = 2, its `include:relay.mailchannels.net`
  = 3, `include:amazonses.com` = 4. **Total 4 of 10.** All nested records are
  `ip4`/`ip6` only, so no further lookups and no void lookups. The record is
  well under 255 characters per string.
- Keep `~all` (soft fail). Do not move to `-all` until DMARC reports (Part 3) show
  every legitimate sender is covered.

### 2B. Custom MAIL FROM subdomain (the change that makes SPF align)

With a custom MAIL FROM, SES uses `bounce.logan911.com` as the envelope sender, so
SPF is checked against a name under `logan911.com` and aligns with the From
address. DMARC then passes by *both* SPF and DKIM.

Subdomain: `bounce.logan911.com`. Rules: it must be a subdomain of the verified
identity, it must not be the apex, and it must not be used for anything else. It
does not exist today. Using a dedicated subdomain means the Hostinger MX at the
apex is untouched; incoming mail for `@logan911.com` is not affected.

Records to add in Cloudflare (zone `logan911.com`), both **DNS only** (grey
cloud), TTL Auto. In the Name field type only the part before `.logan911.com`,
per the warning in the setup runbook:

| Type | Name (type this) | Full name | Priority | Content |
|---|---|---|---|---|
| MX | `bounce` | `bounce.logan911.com` | 10 | `feedback-smtp.us-east-1.amazonses.com` |
| TXT | `bounce` | `bounce.logan911.com` | n/a | `v=spf1 include:amazonses.com ~all` |

Lookup count for the MAIL FROM SPF record: 1 (`include:amazonses.com`). It does
not include Hostinger, which is correct: nothing from Hostinger uses this name.

The MX record is how SES receives bounces and complaints for that envelope
domain; it is required, and the region in the hostname must match the SES region
(us-east-1).

Then, **after the DNS records resolve**, set the MAIL FROM domain on the SES
identity. Console: SES, Identities, `logan911.com`, Authentication tab, Custom
MAIL FROM domain, Edit; or CLI:

```
aws sesv2 put-email-identity-mail-from-attributes --email-identity logan911.com --mail-from-domain bounce.logan911.com --behavior-on-mx-failure USE_DEFAULT_VALUE --region us-east-1
```

Use `USE_DEFAULT_VALUE` (not `REJECT_MESSAGE`). If the MX record ever stops
resolving, SES falls back to its own envelope domain and sign-in codes keep
flowing; with `REJECT_MESSAGE` a DNS mistake would stop every sign-in code. The
sign-in path is the reason this choice matters.

Order matters: DNS records first, SES setting second, so SES verification finds
them on the first check. SES can take a few minutes, up to 72 hours, to report the
MAIL FROM status as Successful.

Expected, not yet confirmed: Cognito sends through the account's SES identity, so
the identity's MAIL FROM setting should apply to its mail. Confirm with the test
message in Part 4 (the `Return-Path` must read `...@bounce.logan911.com`). If
Cognito mail still shows `amazonses.com`, stop and report; do not work around it.

No change to `infrastructure/` is needed for any of this. If the project later
brings the SES identity under CDK, record the MAIL FROM domain in code at the
same time.

## 3. DMARC: stay at `p=none` now, add reporting, move to quarantine later

Current: `v=DMARC1; p=none`. This is safe and means "monitor only". It does not
cause spam placement by itself. Do not go straight to `p=quarantine`, because
Hostinger staff mail and any unknown sender (scanners, ticketing tools, vendor
notifications sent as `@logan911.com`) would be quarantined at receivers without
anyone seeing it.

**Step 3A (with the rest of this change): add a reporting address, keep `p=none`.**

```
v=DMARC1; p=none; rua=mailto:<REPORT-MAILBOX>@logan911.com; fo=1
```

`<REPORT-MAILBOX>` needs Ted's decision: a real mailbox on Hostinger that someone
reads, or a free DMARC report processor's address. Aggregate (`rua`) reports are
XML from Google, Yahoo, Microsoft and others, roughly daily; they contain sending
IP addresses and pass/fail counts, no message content. Do not point `rua` at an
address nobody will read. Leave out `rua` entirely rather than guess a mailbox.

**Step 3B (about 4 weeks later, only if reports are clean): `p=quarantine`, ramped.**

```
v=DMARC1; p=quarantine; pct=25; rua=mailto:<REPORT-MAILBOX>@logan911.com; fo=1
```

Then 50, then 100 over several weeks, and `p=reject` only if wanted afterward.
Gate to move from 3A to 3B: the reports show every sending source for
`logan911.com` passing DMARC (Hostinger mailboxes pass via the Hostinger DKIM
above; SES passes via DKIM and, after Part 2B, SPF). Anything failing that is
legitimate must be fixed first.

Expected benefit: a DMARC policy beyond `none` is a modest trust signal to Yahoo
and Gmail and protects the domain from spoofing. It does not by itself move a
legitimate message out of spam.

## 4. Verify afterward

DNS (from any machine; compare against a public resolver so Cloudflare's cache
and your local resolver cannot mislead):

```
nslookup -type=TXT logan911.com 1.1.1.1
nslookup -type=MX bounce.logan911.com 1.1.1.1
nslookup -type=TXT bounce.logan911.com 1.1.1.1
nslookup -type=TXT _dmarc.logan911.com 1.1.1.1
```

Expect: exactly one `v=spf1` TXT at the apex with both includes; the MX
`10 feedback-smtp.us-east-1.amazonses.com` and the SES SPF TXT at `bounce`; the
DMARC value from Part 3. Existing records (Hostinger MX, SES CNAMEs for DKIM,
`hostingermail-*` DKIM) must be unchanged.

SES identity page: SES, Identities, `logan911.com`, Authentication tab. Custom
MAIL FROM domain reads `bounce.logan911.com` with status **Successful**; DKIM
still **Successful**; identity status **Verified**.

End to end:

1. Trigger a sign-in code to a mailbox Ted controls (a Gmail address is best
   because it shows full headers; a Yahoo address shows the spam outcome).
2. In Gmail: Show original. Expect `SPF: PASS` with domain
   `bounce.logan911.com`, `DKIM: PASS` with domain `logan911.com`, `DMARC: PASS`,
   and `Return-Path: ...@bounce.logan911.com`.
3. Send one to the Yahoo recipient who found the invite in spam and ask whether it
   landed in the inbox. If it was filed as spam before, they must mark it
   **Not spam** once; Yahoo's earlier decision follows the sender, not the DNS.
4. Confirm the sign-in code still arrives promptly (the real acceptance test: the
   change must not slow or break sign-in).

## 5. Other levers if Yahoo still files it as spam

Listed so nothing is silently ignored; none is part of this DNS change.

- Review the invitation and sign-in-code templates in
  `infrastructure/lcdash_pilot/foundation_stack.py` for spam signals (all links
  and no text alternative, a temporary password in the body, URL shorteners).
- Ask recipients to add `no-reply@logan911.com` to contacts and mark the first
  message Not spam; low volume plus real engagement is what builds reputation.
- Check the SES Reputation metrics page for bounces and complaints, and the
  suppression list (runbook, "Handling bounces and complaints").
- Possible later: a monitored reply address instead of a no-reply sender. That is a
  config change in `config.py` and a separate decision.

## 6. Rollback

Each part reverts independently. Nothing here deletes the three SES DKIM CNAMEs,
which must stay (deleting them un-verifies the domain and breaks sign-in).

**Part 2A (apex SPF).** Replace the TXT value on `logan911.com` with the previous
value, exactly:

```
v=spf1 include:_spf.mail.hostinger.com ~all
```

**Part 2B (custom MAIL FROM).** In this order: (1) remove the custom domain from
the identity (console: Custom MAIL FROM domain, Edit, clear; CLI
`aws sesv2 put-email-identity-mail-from-attributes --email-identity logan911.com --region us-east-1`
with no `--mail-from-domain`), (2) confirm a test sign-in code still arrives,
(3) delete the `bounce` MX and TXT records in Cloudflare. With
`USE_DEFAULT_VALUE` set, SES falls back to its default envelope domain on its own
if the DNS records are removed first, but unset the identity first to avoid a
Failed status.

**Part 3 (DMARC).** Replace the TXT value on `_dmarc.logan911.com` with the
previous value, exactly:

```
v=DMARC1; p=none
```

If anything stops being delivered after a DMARC step beyond 3A, the immediate
mitigation is to set `p=none` again. DNS caching means receivers can take up to
the record's TTL (Cloudflare Auto is typically 300 seconds) to see a revert.

## 7. The exact records, for Ted to apply or approve

Zone: `logan911.com` in Cloudflare. All records **DNS only**, TTL Auto. Names are
as typed in Cloudflare's Name field.

| # | Action | Type | Name | Priority | Content |
|---|---|---|---|---|---|
| 1 | Edit existing | TXT | `@` | n/a | `v=spf1 include:_spf.mail.hostinger.com include:amazonses.com ~all` |
| 2 | Add | MX | `bounce` | 10 | `feedback-smtp.us-east-1.amazonses.com` |
| 3 | Add | TXT | `bounce` | n/a | `v=spf1 include:amazonses.com ~all` |
| 4 | Edit existing (only after a report mailbox is chosen) | TXT | `_dmarc` | n/a | `v=DMARC1; p=none; rua=mailto:<REPORT-MAILBOX>@logan911.com; fo=1` |

Then, after records 2 and 3 resolve, one SES change: set the identity
`logan911.com` custom MAIL FROM domain to `bounce.logan911.com` with behavior on
MX failure **Use default MAIL FROM domain** (`USE_DEFAULT_VALUE`).

Recommended minimum if Ted wants the smallest change that does real work:
records 2 and 3 plus the SES setting (Part 2B). Record 1 is optional hygiene and
record 4 can wait for a report mailbox.

Decisions needed from Ted: approve rows 1 to 3 and the SES setting; name the
DMARC report mailbox (or defer row 4); say whether the agent may apply them or
Ted will.

## 8. Applied 2026-10-26 (Part 2B only)

Ted approved Part 2B in chat; rows 1 and 4 (apex SPF, DMARC `rua`) were not
approved and remain unchanged.

- Cloudflare: `bounce` MX 10 `feedback-smtp.us-east-1.amazonses.com` and `bounce`
  TXT `v=spf1 include:amazonses.com ~all` added; both resolve through 1.1.1.1.
- SES identity `logan911.com` (account 862772137583, us-east-1): custom MAIL FROM
  domain set to `bounce.logan911.com`, behavior on MX failure "Use default MAIL
  FROM domain". Console showed MAIL FROM configuration **Pending** at the time of
  writing; it must reach **Successful**.
- Not yet done: confirm Successful, then send a test sign-in code and check the
  headers (Part 4). Apex SPF and DMARC `rua` still open.
- Later 2026-10-26: SES console reports MAIL FROM configuration **Successful**
  (confirmed by reloading the identity page). Still to do: test sign-in code
  header check.
- Later 2026-10-26, 17:38: test sign-in code to a Gmail mailbox shows
  `mailed-by: bounce.logan911.com` and `signed-by: logan911.com`, so the custom
  MAIL FROM applies to Cognito mail and SPF and DKIM are both aligned. The
  Yahoo placement result is not yet known. Apex SPF and DMARC `rua` still open.
