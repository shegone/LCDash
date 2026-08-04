# ADR 0003: CAD Read-Only Capability Boundary

Status: Accepted for planning

Read capabilities and optional write capabilities use separate interfaces.
Read-only deployments omit write interfaces, deny operational-output flags,
limit the adapter task role to the county's read-only secret, prefer vendor-
scoped read-only credentials, allowlist egress, and alarm on denied capability
attempts.

IAM cannot by itself prevent an external API operation performed with an
over-privileged vendor credential. Provider contract tests and vendor-side
scope remain required controls.
