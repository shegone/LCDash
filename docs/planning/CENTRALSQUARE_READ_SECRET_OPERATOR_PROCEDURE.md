# CentralSquare cloud read-only secret operator procedure

Status: **PROCEDURE ONLY - DO NOT ACTIVATE CONNECTOR**

## Fixed non-secret contract

- AWS account: `862772137583`
- Region: `us-east-1`
- Secret name: `lcdash-p1-logan-use1/centralsquare/read-only`
- Required JSON keys: `username` and `password`
- Reference passed to later reviewed infrastructure/application configuration:
  the complete Secrets Manager ARN returned by AWS, beginning with
  `arn:aws:secretsmanager:us-east-1:862772137583:secret:lcdash-p1-logan-use1/centralsquare/read-only`

The ARN is non-secret metadata. The values stored under `username` and
`password` are secret and must never be pasted into chat, a ticket, Git, a
handoff, a screenshot, a command line, CloudFormation parameters, or logs.

## Non-secret endpoint configuration

Endpoints do not belong in the secret. A later reviewed activation package must
supply these three non-secret configuration fields separately:

- `token_url`: approved CentralSquare HTTPS token endpoint
- `cad_base_url`: approved CentralSquare HTTPS CAD API base
- `system_base_url`: approved CentralSquare HTTPS system API base

Each must use an independently reviewed DNS hostname and HTTPS, with no embedded
username/password, query string, fragment, IP literal, localhost target, or
redirect to an unapproved host. Do not copy endpoint values from an on-premises
secret or `.env`; use the vendor-approved cloud access record.

The remaining dormant fields are `mode=centralsquare-read-poll`, the trusted
tenant identifier, `poll_seconds` from 15 through 300,
`reconciliation_overlap_seconds` from the poll interval through 900, and
`webhooks_enabled=false`. Recording these fields does not authorize activation.

## Short AWS Console procedure

1. Sign in through the approved administrator workflow and confirm the console
   shows account `862772137583` and region **US East (N. Virginia) us-east-1**.
2. Open **AWS Secrets Manager**, choose **Store a new secret**, then choose
   **Other type of secret**.
3. In key/value mode, create exactly two keys: `username` and `password`.
   Personally enter the dedicated, vendor-approved cloud inquiry-account values.
   Do not use or copy the on-premises credential unless a separate authorization
   explicitly approves that exact credential for concurrent cloud use.
4. Keep encryption on the organization-approved KMS choice. If no specific
   customer-managed key has been approved, stop and obtain that decision rather
   than selecting a new key or changing KMS policy.
5. Name the secret exactly
   `lcdash-p1-logan-use1/centralsquare/read-only`. Add the existing pilot tags
   required by the infrastructure review, but do not add credential values or
   operational data as tags or descriptions.
6. Leave automatic rotation disabled until the vendor and security owner approve
   a tested rotation method. Store the secret.
7. On the secret overview page, copy only the complete **Secret ARN** into the
   non-secret activation review record. Do not use **Retrieve secret value**, do
   not copy either value back out, and do not attach broad resource policies.
8. Stop. Do not edit the ECS task, IAM roles, CDK parameters, deployment mode,
   endpoints, polling, webhooks, or connector. Those changes require a separate
   reviewed activation package.

## Later least-privilege IAM reference

A future approved task role or execution-time injection path should receive only
`secretsmanager:GetSecretValue` for the one exact secret ARN, including the
AWS-generated ARN suffix. Do not grant `Resource: *`, list/read access to other
secrets, or permission to create, update, rotate, tag, or delete secrets.

The application must select the two JSON keys by name and expose neither value
in environment dumps, settings representations, health responses, exceptions,
audit records, or logs. The current pilot task role has no Secrets Manager read
permission, and this procedure intentionally does not change that state.

## Agent knowledge boundary

No credential value is needed by an agent to prepare, review, test, deploy, or
audit the dormant configuration boundary. The operator enters values directly
in the AWS Console. Agents may receive only the secret name, ARN, version/rotation
status metadata, and sanitized success/failure state after a separate approval.
