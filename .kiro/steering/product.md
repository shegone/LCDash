---
inclusion: always
---

# Product direction

LCDash AWS is a reusable, county-isolated public-safety operations platform.
It provides the same supervisor dashboard, analytics, reporting, GIS,
knowledge assistants, and optional voice capabilities as the Logan County
on-premises platform while using managed AWS services where they improve
availability, scaling, governance, auditability, and maintainability.

The platform serves multiple counties through configuration and provider
adapters, not county-specific forks. Each county may use a different CAD,
identity provider, GIS package, agency taxonomy, retention policy, AI policy,
branding package, and enabled feature set.

Public-safety AI is advisory and optional. It must never become a dependency
of call routing, emergency call handling, CAD availability, ESInet, radio,
station alert tones, paging, or human authorization.

The first environment is a non-production Logan County sandbox in commercial
AWS `us-east-1`. It remains independent of production `.227`. The architecture
must also avoid assumptions that prevent a later AWS GovCloud (US) deployment.

