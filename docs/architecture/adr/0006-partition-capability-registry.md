# ADR 0006: Region and Partition Capability Registry

Status: Accepted for planning

Infrastructure uses a versioned registry of service, model, voice, identity,
GIS, and delivery capabilities by AWS region and partition. CDK synthesis
selects an approved provider fallback or fails when a required capability is
unavailable.

Commercial AWS success is never evidence of AWS GovCloud readiness. Availability
claims are verified against current authoritative documentation before each
architecture or production gate.
