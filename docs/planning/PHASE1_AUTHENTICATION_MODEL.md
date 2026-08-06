# Phase 1 cloud authentication model

This document describes the local infrastructure design only. It does not
authorize creation of a user pool, group, user, domain, client, certificate, or
DNS record.

## Trust boundary

- The only application hostname is `aws.logan911.com`.
- The ALB HTTPS listener always runs `authenticate-cognito` before forwarding.
  Unauthenticated requests are redirected to Cognito; there is no unauthenticated
  listener rule or application bypass path.
- HTTP performs only a permanent redirect to HTTPS.
- The ECS task fixes `LCDASH_TENANT=logan-synthetic`. Neither URL/query/body
  values, Cognito custom attributes, group names, nor forwarded claims can select
  or replace the tenant.
- The pool permits administrator-created accounts only. There is no public
  self-sign-up, social login, SAML provider, identity pool, guest identity,
  browser AWS credential, or custom Lambda trigger.

## Group and role model

The groups are application roles, not IAM roles. Neither group has a `RoleArn`,
and no identity pool exists.

| Cognito group | Precedence | Permitted Phase 1 purpose |
| --- | ---: | --- |
| `lcdash-pilot-viewer` | 20 | View the authenticated synthetic pilot, approved public/reference content, and read-only advisory results. |
| `lcdash-pilot-reviewer` | 10 | Perform the same read-only review plus evaluation and acceptance review. It adds no tenant, CAD, output, AWS, or administrative authority. |

Every future pilot account must be named, administrator-created, assigned only
to an approved group, reviewed against the current operator record, and removed
when no longer needed. Group precedence resolves presentation of the higher
review role when both are assigned; it never changes the fixed tenant. No group
authorizes CAD access, paging, station alerts, acknowledgements, subscriptions,
public warning, EMS delivery, or another operational output.

## Login and session policy

- Email is the sign-in identifier and verified email is the only account-recovery
  mechanism.
- MFA is mandatory and software TOTP is the only enabled second factor. SMS MFA
  is disabled.
- Passwords require at least 14 characters with uppercase, lowercase, number,
  and symbol characters. Temporary passwords expire after one day.
- The ALB uses a confidential Cognito client because ALB performs the server-side
  authorization-code exchange. The only OAuth grant is authorization code;
  implicit and client-credentials grants are absent.
- Access and ID tokens last 15 minutes. Refresh tokens last one day, token
  revocation is enabled, and refresh-token rotation has no retry grace period.
- The ALB authentication session lasts one hour and redirects unauthenticated
  requests back through Cognito.

## Required acceptance evidence

Before deployment, the Package 5A gate must reference the synthesized user-pool,
client, group, listener, and fixed-tenant assertions. After an authorized
deployment, a human reviewer must record sanitized evidence for MFA enrollment,
group assignment, failed unauthenticated access, successful authenticated access,
logout/session termination, and confirmation that no identity pool or bypass
listener exists. Do not record tokens, cookies, codes, user passwords, client
secrets, or personal recovery details.
