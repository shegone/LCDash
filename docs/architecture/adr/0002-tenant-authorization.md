# ADR 0002: Immutable Tenant Context and Deny-by-Default Authorization

Status: Accepted for planning

Authentication middleware derives immutable tenant context from trusted
identity and deployment bindings. Client URL, body, query, and header values
cannot select or override the tenant.

One authorization contract evaluates tenant, role, module, action, data class,
and resource at APIs, repositories, objects, queues, reports, caches, and AI
tools. Amazon Verified Permissions may implement the contract where supported,
but application correctness cannot depend on its availability.
