# ADR 0004: County Data Isolation

Status: Accepted for planning

Production defaults to a database cluster, KMS key, object storage boundary,
queues, secrets, logs, and backups per county cell. Tables still carry an
immutable tenant identifier. Lower-cost synthetic environments may use logical
separation only when every cross-tenant negative test fails closed.

The shared control plane receives metadata and approved de-identified
aggregates only; it never stores raw operational CAD records.
