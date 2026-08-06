"""Immutable tenant and non-secret county profile contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping


TENANCY_CONTRACT_VERSION = "1.0"
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze(item) for item in value)
    return value


def _validate_identifier(label: str, value: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase, stable identifier.")


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Trusted identity and deployment binding; never built from request input."""

    tenant_id: str
    subject: str
    identity_source: str
    roles: frozenset[str]
    request_id: str
    authenticated_at: datetime
    contract_version: str = TENANCY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_identifier("tenant_id", self.tenant_id)
        if not self.subject or not self.identity_source or not self.request_id:
            raise ValueError("TenantContext requires trusted subject, source, and request ID.")
        if not self.roles:
            raise ValueError("TenantContext requires at least one trusted role.")
        object.__setattr__(self, "roles", frozenset(str(role) for role in self.roles))


@dataclass(frozen=True, slots=True)
class CountyProfile:
    """Versioned, immutable, non-secret configuration for one county cell."""

    tenant_id: str
    display_name: str
    timezone: str
    region: str
    cad_provider: str
    capabilities: frozenset[str]
    modules: frozenset[str]
    branding: Mapping[str, Any] = field(default_factory=dict)
    agencies: tuple[Mapping[str, Any], ...] = ()
    unit_status_mappings: Mapping[str, str] = field(default_factory=dict)
    gis_sources: tuple[Mapping[str, Any], ...] = ()
    heatmap_configuration: Mapping[str, Any] = field(default_factory=dict)
    identity_federation: Mapping[str, Any] = field(default_factory=dict)
    retention: Mapping[str, Any] = field(default_factory=dict)
    ai_policy: Mapping[str, Any] = field(default_factory=dict)
    voice_profile: Mapping[str, Any] = field(default_factory=dict)
    alert_permissions: frozenset[str] = field(default_factory=frozenset)
    profile_version: str = "1.0"
    contract_version: str = TENANCY_CONTRACT_VERSION

    def __post_init__(self) -> None:
        _validate_identifier("tenant_id", self.tenant_id)
        if not all((self.display_name, self.timezone, self.region, self.cad_provider)):
            raise ValueError("CountyProfile requires name, timezone, region, and CAD provider.")
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))
        object.__setattr__(self, "modules", frozenset(self.modules))
        object.__setattr__(self, "alert_permissions", frozenset(self.alert_permissions))
        for name in (
            "branding",
            "unit_status_mappings",
            "heatmap_configuration",
            "identity_federation",
            "retention",
            "ai_policy",
            "voice_profile",
        ):
            object.__setattr__(self, name, _freeze(getattr(self, name)))
        object.__setattr__(self, "agencies", _freeze(self.agencies))
        object.__setattr__(self, "gis_sources", _freeze(self.gis_sources))
