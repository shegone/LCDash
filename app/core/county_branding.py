"""Pure, fail-closed county branding configuration helpers."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Mapping

from app.core.county_profiles import (
    CountyProfileValidationError,
    resolve_county_profile,
)
from app.core.tenancy import CountyProfile, TenantContext


_BRANDING_KEYS = frozenset(
    {"short_name", "logo_asset", "primary_color", "accent_color", "background_color"}
)
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LOGO_ASSET = re.compile(r"^synthetic/[a-z0-9/_-]+\.(?:svg|png)$")
_INHERITED_BRANDING: Mapping[str, str] = MappingProxyType({})


def county_branding(county_profile: CountyProfile | None = None) -> Mapping[str, str]:
    """Return an immutable, presentation-only branding snapshot.

    No profile deliberately leaves existing callers on their inherited branding
    path.  Profile-derived values are accepted only when the complete,
    synthetic branding contract is present.
    """
    if county_profile is None:
        return _INHERITED_BRANDING
    if not isinstance(county_profile, CountyProfile):
        raise ValueError("CountyProfile is required for county branding.")

    branding = county_profile.branding
    if set(branding) != _BRANDING_KEYS:
        raise ValueError("County profile branding is incomplete or unsupported.")

    snapshot = {key: branding[key] for key in _BRANDING_KEYS}
    if any(not isinstance(value, str) or not value.strip() for value in snapshot.values()):
        raise ValueError("County profile branding values must be non-empty strings.")
    if len(snapshot["short_name"]) > 40:
        raise ValueError("County profile branding short name is too long.")
    if not _LOGO_ASSET.fullmatch(snapshot["logo_asset"]):
        raise ValueError("County profile branding logo must be synthetic.")
    if any(
        not _COLOR.fullmatch(snapshot[key])
        for key in ("primary_color", "accent_color", "background_color")
    ):
        raise ValueError("County profile branding colors must be hex values.")
    return MappingProxyType(snapshot)


def branding_for_tenant_context(
    tenant_context: TenantContext | None = None,
) -> Mapping[str, str]:
    """Compose presentation branding only from an immutable trusted context."""
    if tenant_context is None:
        return county_branding()
    if not isinstance(tenant_context, TenantContext):
        raise CountyProfileValidationError("Trusted tenant context is required.")

    county_profile = resolve_county_profile(tenant_context)
    if county_profile.tenant_id != tenant_context.tenant_id:
        raise CountyProfileValidationError("County branding tenant binding mismatch.")
    return county_branding(county_profile)
