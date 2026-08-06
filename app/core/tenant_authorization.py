"""Pure deny-by-default tenant module authorization contract."""

from __future__ import annotations

from app.core.tenancy import CountyProfile, TenantContext, TENANCY_CONTRACT_VERSION
from app.integrations.contracts import ModuleCapability


READ_ONLY_ACTIONS = frozenset({"read", "list", "view", "query", "status"})
OPERATIONAL_CAPABILITIES = frozenset(
    {
        ModuleCapability.CAD_MESSAGES.value,
        ModuleCapability.REALTIME_WEBHOOKS.value,
        ModuleCapability.PAGING.value,
        ModuleCapability.PUBLIC_WARNING.value,
        ModuleCapability.STATION_ALERTS.value,
        ModuleCapability.EMS_DELAY.value,
    }
)
WRITE_LIKE_ACTIONS = frozenset(
    {
        "acknowledge",
        "activate",
        "create",
        "delete",
        "deliver",
        "dispatch",
        "ingest_event",
        "page",
        "publish",
        "register_subscription",
        "release",
        "send",
        "update",
        "write",
    }
)


class TenantAuthorizationDenied(PermissionError):
    """Sanitized deny result for an untrusted or unauthorized action."""


def authorize_tenant_action(
    context: TenantContext,
    profile: CountyProfile,
    capability: ModuleCapability | str,
    action: str,
) -> bool:
    """Allow an explicitly enabled read-only pair or raise a sanitized denial."""
    if not isinstance(context, TenantContext) or not isinstance(profile, CountyProfile):
        raise TenantAuthorizationDenied("Trusted tenant context and county profile are required.")
    if (
        context.contract_version != TENANCY_CONTRACT_VERSION
        or profile.contract_version != TENANCY_CONTRACT_VERSION
    ):
        raise TenantAuthorizationDenied("Tenant authorization contract mismatch.")
    if context.tenant_id != profile.tenant_id:
        raise TenantAuthorizationDenied("Tenant and county profile binding mismatch.")

    try:
        capability_name = ModuleCapability(str(capability)).value
    except ValueError as exc:
        raise TenantAuthorizationDenied("Unknown module capability.") from exc

    action_name = str(action or "").strip().lower()
    if capability_name in OPERATIONAL_CAPABILITIES:
        raise TenantAuthorizationDenied("Operational output capability is denied.")
    if action_name in WRITE_LIKE_ACTIONS:
        raise TenantAuthorizationDenied("Write or operational action is denied.")
    if action_name not in READ_ONLY_ACTIONS:
        raise TenantAuthorizationDenied("Unknown or non-read-only action is denied.")
    if capability_name not in profile.capabilities:
        raise TenantAuthorizationDenied("Module capability is not declared for this county.")
    if capability_name not in profile.modules:
        raise TenantAuthorizationDenied("Module capability is not enabled for this county.")
    return True
