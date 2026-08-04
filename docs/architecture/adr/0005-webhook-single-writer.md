# ADR 0005: Dormant Webhook and Single-Writer Activation

Status: Accepted for planning

The AWS webhook route remains dormant while `.227` owns the Logan callback and
operational outputs. AWS begins with synthetic data, then separately approved
bounded read-only polling.

Webhook activation requires vendor confirmation, a single-writer/fencing
design, deduplication and reconciliation evidence, rollback, named operator
approval, and proof that duplicate dispatch or alert behavior cannot occur.
