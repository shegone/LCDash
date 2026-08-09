# SES email sender setup runbook

Cognito needs to email one-time sign-in codes. It cannot use its own built-in
sender for that (roughly 50 messages a day, and a code goes out on every single
sign-in), so it sends through Amazon SES instead. This is the one-time setup that
makes that work.

**Plain version of what you are doing:** telling Amazon "I own logan911.com, and
you may send mail as that domain," then asking Amazon to lift the training-wheels
limits on a brand-new SES account.

Nothing in the LCDash stack can be deployed with email sign-in until this is
done, so this is the first thing on the critical path. Expect roughly a day,
almost all of it waiting on step 3.

Two things that must be exactly right, because both fail *silently* rather than
with an error:

- The AWS **region must be us-east-1**. SES verification records are per-region.
  A domain verified in the wrong region looks fine in the console and still
  cannot send.
- Verify the **domain** `logan911.com`, not the single address
  `no-reply@logan911.com`. Those are two different things to SES, and the stack
  is wired to the domain. Verifying only the address means Cognito accepts the
  configuration and then quietly delivers nothing.

---

## Step 1 — Create the domain identity in SES

1. Sign in to the AWS console for account **862772137583**.
2. Confirm the region selector, top right, says **N. Virginia (us-east-1)**.
3. Go to **Amazon SES** → in the left menu under **Configuration**, choose
   **Identities**.
4. Choose **Create identity**.
5. For identity type, select **Domain**.
6. In the **Domain** field enter exactly:

   ```
   logan911.com
   ```

   No `www.`, no `aws.`, no `https://`. Just the bare domain.
7. Leave everything else alone. The defaults are what we want — Easy DKIM at
   2048 bits. Do **not** tick "Use a custom MAIL FROM domain."
8. Check that **DKIM signatures** shows as **Enabled**.
9. Choose **Create identity**.

You will land on a page for the new identity showing status **Pending**. That is
expected — it stays pending until step 2 is done.

## Step 2 — Publish three DNS records in Cloudflare

SES now needs proof you control the domain, which you give it by adding three
records to DNS. Our DNS lives in **Cloudflare** (Hostinger is only the
registrar — do not add these there, it will not work).

1. Still on the identity page in SES, open the **Authentication** tab and expand
   **Publish DNS records**. You will see a table of **three CNAME records**.
2. Choose **Download .csv record set** so you have them saved, or keep the tab
   open.
3. In a second tab, open **Cloudflare** → select `logan911.com` → **DNS** →
   **Records**.
4. For each of the three rows, choose **Add record** and enter:
   - **Type**: `CNAME`
   - **Name**: the SES "Name" value, *with `.logan911.com` removed from the end*
     — see the warning below
   - **Target**: the SES "Value" value, copied exactly
   - **Proxy status**: **DNS only** (the cloud icon must be grey, not orange)
   - **TTL**: Auto
5. Save each one.

> **This is the step that usually goes wrong.** Cloudflare automatically adds
> `.logan911.com` to whatever you type in the Name field. SES shows the full
> name. So if SES shows:
>
> ```
> abc123xyz._domainkey.logan911.com
> ```
>
> you type only:
>
> ```
> abc123xyz._domainkey
> ```
>
> If you paste the whole thing, Cloudflare stores
> `abc123xyz._domainkey.logan911.com.logan911.com` and verification never
> completes. After saving, look at the record list and confirm each name ends in
> `._domainkey.logan911.com` exactly once.
>
> Also: keep the underscore in `_domainkey`, and do **not** add an extra
> underscore at the front.

Verification is usually done in a few minutes, though AWS allows up to 72 hours.
Refresh the SES identity page until **DKIM configuration** reads **Successful**
and **Identity status** reads **Verified**.

To check the records independently of SES — which distinguishes "I typed them
wrong" from "SES has not polled yet" — query public DNS directly:

```bash
nslookup -type=CNAME <selector>._domainkey.logan911.com 1.1.1.1
```

Each should answer with `canonical name = <selector>.dkim.amazonses.com`. If all
three answer correctly, the DNS side is done and only SES's own polling remains.

## Step 3 — Request production access

A new SES account starts in the "sandbox," which allows only 200 messages a day
*and* only to addresses you have individually verified. That cannot work for
sign-in codes, so this limit has to be lifted.

> **This step is gated on step 2 finishing.** AWS disables the **Request
> production access** button until the domain reports **Verified** — the card
> reads "Domain verification needed" and the button is genuinely disabled, not
> just greyed. So the roughly 24-hour AWS review clock does not start until
> verification completes. Plan the two waits as sequential, not overlapping.

1. In SES, go to **Get set up** (or **Account dashboard**).
2. Find the **Request production access** card and choose the button once it is
   enabled.
3. Fill in the request:
   - **Mail type**: Transactional
   - **Website URL**: `https://aws.logan911.com`
   - **Use case description** — say what it actually is, which is the strongest
     possible case. Something like:

     > Logan County 911 operates an internal operations dashboard. Amazon SES
     > will send one-time multi-factor authentication codes and administrator-
     > created temporary credentials to a small, fixed list of named county staff
     > accounts in an Amazon Cognito user pool. All recipients are county
     > employees whose addresses are added by an administrator; there is no
     > public sign-up, no marketing mail, and no mailing list. Expected volume is
     > well under 100 messages per day.
   - **Additional contacts / compliance**: confirm you only send to addresses an
     administrator has explicitly added, and that there is no public sign-up.
4. Submit.

AWS usually replies within 24 hours. Approval is normally straightforward for
this kind of internal transactional use.

## When you are done

Tell me, and I will confirm from my side that:

- the identity `logan911.com` is **Verified** with DKIM **Successful**, in
  us-east-1
- the account is out of the sandbox
- the SES identity ARN matches what the stack expects,
  `arn:aws:ses:us-east-1:862772137583:identity/logan911.com`

Then the pool change can deploy and email sign-in becomes live.

## Notes

- The address the system sends *from* is `no-reply@logan911.com`
  (`PILOT_MAIL_FROM_ADDRESS` in `infrastructure/lcdash_pilot/config.py`). Once
  the domain is verified this address needs no separate setup, but if it is a
  real mailbox somewhere, expect it to receive bounces.
- If you later want the sender to be a different address, it only has to be at
  `logan911.com` — but change it in `config.py`, not in the console, or the next
  deploy will revert it.
- These three DNS records are permanent. Deleting them un-verifies the domain and
  breaks sign-in for everyone.
