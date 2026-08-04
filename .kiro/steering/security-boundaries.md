---
inclusion: always
---

# Security and operational boundaries

1. Production `.227` and PC `.15` are out of scope. Never connect to them from
   this workspace or from an AWS resource.
2. Never read, print, copy, request, or store secret values in source control,
   prompts, model memory, CloudFormation output, logs, test reports, or
   handoffs.
3. Logan County's existing CentralSquare username and password may be reused
   only after explicit approval and direct entry into a Logan-specific AWS
   Secrets Manager secret. The adapter task role alone may read it.
4. Before using those credentials, confirm vendor permission for concurrent
   use, expected API rate limits, stable AWS egress IP requirements, and any
   contractual restrictions on hosting or processing CAD data in the selected
   AWS partition and region.
5. The AWS environment begins read-only. CAD create/update actions,
   subscription changes, acknowledgments, command messages, EMS delivery,
   paging, station alert audio, and public-warning outputs are denied by both
   feature configuration and IAM/application policy.
6. Do not register a second CentralSquare webhook until the vendor confirms
   multiple callback/subscription behavior and an operator approves the
   ownership model. Start with bounded polling and reconciliation.
7. Never store raw CAD payloads in logs. Persist only fields required by the
   approved feature and documented retention policy. Tests use synthetic data.
8. Use a county silo by default: county-specific AWS account or deployment
   cell, KMS key, database, buckets, queues, secrets, logs, and backup scope.
   Shared control-plane services store tenant metadata and deployment health,
   not operational CAD records.
9. Human access uses federation, MFA, least privilege, and auditable roles.
   Authentication alone is not tenant isolation; tenant authorization must be
   enforced at every API and data boundary.
10. AI tools remain read-only and allowlisted. Model output is untrusted until
    validated by deterministic code. AI failure cannot block core operations.
11. Station tones remain authoritative. Cloud speech is not placed on the live
    Logan County alert path during the AWS sandbox phase.
12. Every deployment must support health verification, rollback, backup, and a
    documented restore test. Destructive operations require explicit approval.

