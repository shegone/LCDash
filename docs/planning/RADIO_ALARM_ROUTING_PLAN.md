# Radio silent-listener alarm: routing it to a phone

Status: proposal for Ted Sparks, 2026-10-26. Nothing in AWS or in the CDK has
been changed. Destination not yet supplied.

## Problem

On 2026-10-25 `.227` froze at 07:21 EDT. The alarm
`lcdash-p1-logan-use1-radio-listener-silent` went to ALARM at 07:42 and
published to the alerts topic, but nobody noticed for 28 hours (see
`handoffs/session-handoff-2026-10-26-227-freeze.md`, recommendation 2). The
alarm works; its only subscriber is not something that interrupts a person.

## Verified facts (read-only, profile `lcdash-phase1-deployment`, 2026-10-26)

- Topic `arn:aws:sns:us-east-1:862772137583:lcdash-p1-logan-use1-alerts`
  (`foundation_stack.py:1627`, `_add_identity_rejection_alarm`).
- One subscription: protocol `email`, endpoint is the `BudgetSubscriberEmail`
  address (an `@911logan.com` mailbox), **confirmed** (`SubscriptionsConfirmed
  = 1`, `SubscriptionsPending = 0`). The topic has no KMS key.
- The alarm (`foundation_stack.py:1669`, `_add_radio_listener_silent_alarm`)
  has the topic as both its ALARM and OK action; it is `OK` now (since
  11:54 EDT 2026-10-26).
- So the 07:42 email was almost certainly delivered to a mailbox that does
  not push to a phone. I cannot confirm delivery from here.
- The allowlist (`infrastructure/phase1_deployment_allowlist.json`,
  `sns_alerts_exception` plus its 2026-10-17 grant) permits exactly one topic
  and **one email subscription** ("no new subscription, no other protocol");
  resource counts pin `AWS::SNS::Subscription: 1`, and
  `test_cdk_template.py` asserts the same twice (lines 51 and 98).
- **Could not check:** SMS sandbox status, SMS monthly spend limit and
  registered origination numbers in us-east-1. The deployment profile is
  denied `sns:GetSMSAttributes`, `sns:GetSMSSandboxAccountStatus`,
  `sns:ListOriginationNumbers`, `sns:ListSMSSandboxPhoneNumbers`,
  `sms-voice:DescribeAccountLimits` and
  `servicequotas:GetServiceQuota`. Ted would have to check these in the
  console or with `lcdash-sandbox-admin`.

## Options

### A. Make the existing mailbox interrupt (no AWS change)

Turn on push notifications for the sender `no-reply@sns.amazonaws.com`
with subject beginning `ALARM:` in the phone mail app (VIP/priority sender,
or a rule that flags it as urgent). Zero risk, zero change set, minutes.
Weakness: still one mailbox, still depends on the mail client's notification
settings. Do this regardless.

### B. Second email subscription to an address that pages (recommended)

Add a second `EmailSubscription` on the same topic to an address that
reliably alerts a phone: a mailbox with its own loud push notification, a
shared on-call mailbox several people have on their phones, or a paging
service's inbound email address. This is the smallest AWS change: one
`AWS::SNS::Subscription`, no new topic, no new protocol, no number
registration, no spend. It also covers more than one person if the
destination is a shared box.

### C. SMS subscription (not recommended as the first step)

AWS documents that SMS to US numbers has required a dedicated origination
identity (10DLC with brand and campaign registration, or a verified toll-free
number) since 2023-08-31
(<https://docs.aws.amazon.com/sns/latest/dg/channels-sms-originating-identities-long-codes.html>).
A new account also starts in the SMS sandbox (destination numbers must be
verified first) with a low default spend limit. Registration is a console
process that takes days to weeks, is not expressible in this stack, and needs
a new allowlist protocol exception ("SMS") on top. If SMS is wanted later, the
subscription is one more line in the same place, after the sandbox exit, spend
limit and origination identity are confirmed by someone with console access.

Carrier email-to-text gateways (an email address that arrives as a text) are
a cheap variant of B, but carriers have been dropping them; if the chosen
carrier's gateway is used, prove it with a test message before relying on it.
I have not verified which gateways currently work.

## Proposed CDK change (option B), to be made once Ted supplies the address

In `foundation_stack.py`:

1. New `CfnParameter` `RadioAlarmPagingEmail` (`type="String"`, `min_length=3`)
   next to `BudgetSubscriberEmail` (line 1031). A parameter keeps the address
   out of git, the template tests and the handoffs. It follows the existing
   pattern for the budget address.
2. In `_add_identity_rejection_alarm`, after the existing subscription, one
   more `topic.add_subscription(sns_subscriptions.EmailSubscription(
   parameters["paging_email"].value_as_string))`.

This subscribes the new address to **both** alarms on the topic, the identity
rejection alarm and the radio alarm, and to their OK messages. That matches
the allowlist's single-topic design and avoids a second topic. If Ted wants
only the radio alarm to page, the alternative is a second topic, which needs
a larger allowlist change; I do not recommend it for the pilot.

Allowlist (`phase1_deployment_allowlist.json`), needs Ted's approval text:

- `sns_alerts_exception.additional_grants`: a third grant, scope "one second
  email subscription on the existing alerts topic to an operator paging
  address supplied as a stack parameter; no new topic, no other protocol, no
  SMS".
- Resource count `AWS::SNS::Subscription` 1 -> 2.
- Update the exception's `reason` text, which currently says "its only
  subscription".

Tests (`infrastructure/tests/test_cdk_template.py` and the allowlist tests):

- Subscription count 2 in both places it is asserted; both are
  `Protocol: email`; one `Endpoint` is `{"Ref": "BudgetSubscriberEmail"}` and
  the other `{"Ref": "RadioAlarmPagingEmail"}`; no literal `@` address
  appears anywhere in the template JSON; protocol `sms` never appears.
- Topic count still 1.
- Allowlist count test updated to match.

## Change-set review scope (for the owner to check before typing the name)

Named change set on the foundation stack, built with
`npx --yes aws-cdk@2.1145.0 ... --method=prepare-change-set
--change-set-name <name>`, with the new parameter supplied and every other
parameter left on its previous value (I will confirm exactly how parameters
are passed when preparing it). Expected contents, nothing else:

- Parameters: `RadioAlarmPagingEmail` added.
- Resources: **Add** one `AWS::SNS::Subscription`; no Modify, no Remove, no
  replacement. The topic, both alarms, both metric filters, the ECS service
  and the task definitions must not appear. `PilotImageDigest` is not
  changing in this change set, so the usual five-resource scope does not
  apply; any ECS resource in the list means stop.

Afterwards:

1. SNS sends a confirmation email to the new address; the subscription stays
   `PendingConfirmation` (and receives nothing) until someone clicks the
   link. Verify with `aws sns list-subscriptions-by-topic`, expecting two
   confirmed subscriptions.
2. Prove the path end to end, once, with a test alarm state change or a
   console "Publish message" on the topic. The deployment profile is not
   permitted `sns:Publish`, so Ted would do this with a role that can, or
   the next real ALARM serves as the proof. Check that the message arrives
   as a phone notification, not just in the mailbox.
3. Rollback: a change set removing the parameter and the subscription.

## What I need from Ted

1. Which option: A now (always), plus B, and the **destination address** to
   subscribe. I will not invent one. Or, if SMS is wanted, the number, and
   confirmation (console) of sandbox exit, spend limit and origination
   identity.
2. Approval text for the allowlist amendment above.
3. Whether one shared topic (both alarms page) is acceptable.

Nothing is pushed. No change set exists yet.
