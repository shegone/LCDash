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

## How per-user identity reaches the application

The load balancer authenticates the user; the application must still learn
*which* user, because a single shared role cannot support differentiated access.
Identity is recovered in `app/core/alb_identity.py` and requires two independent
verifications, since neither forwarded header is both trustworthy and complete:

| Header | Signed by | Verified how | Supplies |
| --- | --- | --- | --- |
| `x-amzn-oidc-data` | The load balancer (ES256) | Signature checked against the regional ALB public key named by `kid`, **and** the `signer` field asserted equal to this deployment's load-balancer ARN | Provenance, `sub`, `email` |
| `x-amzn-oidc-accesstoken` | The Cognito user pool (RS256) | Signature checked against the pool JWKS; issuer, `token_use=access`, expiry, and `client_id` all confirmed | `cognito:groups` |

Both are required. The ALB assertion is what proves a request actually arrived
through the load balancer rather than being forged by anything else able to
reach the container; the `signer` check is the load-bearing part of that proof.
The Cognito access token is the only carrier of group membership, because the
ALB does not forward ID-token claims and the Cognito userInfo endpoint omits
`cognito:groups`.

Constraints that this design deliberately keeps:

- The tenant binding remains fixed by `LCDASH_TENANT` and is never read from a
  request. Only `subject` and `roles` are request-derived.
- Group claims are mapped by `resolve_pilot_role`, which is deny-by-default: an
  unrecognized group denies the request outright rather than degrading to
  `viewer`. A verification failure, a missing group claim, and a missing
  configuration value all deny for the same reason.
- Identity headers travel unencrypted between the load balancer and the task,
  because the target group is HTTP. Ingress on the task port is restricted to
  the load balancer's security group, which is the compensating control.
- The capability ships behind `LCDASH_ALB_IDENTITY_ENABLED`, default off. While
  disabled, every request keeps the previous deployment-wide `viewer` identity,
  so enabling it is a deliberate and reversible step.

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
- The ALB authentication session lasts twenty-four hours and redirects unauthenticated
  requests back through Cognito.

## Required acceptance evidence

Before deployment, the Package 5A gate must reference the synthesized user-pool,
client, group, listener, and fixed-tenant assertions. After an authorized
deployment, a human reviewer must record sanitized evidence for MFA enrollment,
group assignment, failed unauthenticated access, successful authenticated access,
logout/session termination, and confirmation that no identity pool or bypass
listener exists. Do not record tokens, cookies, codes, user passwords, client
secrets, or personal recovery details.
