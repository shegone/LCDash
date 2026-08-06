"""Pure presentation helpers backed by immutable county configuration."""

from __future__ import annotations

from app.core.tenancy import CountyProfile


INHERITED_AGENCY_DISPLAY_LABELS = {
    "LCEOC": "911 Center / Administrative",
}


def agency_display_label(
    value: object,
    county_profile: CountyProfile | None = None,
) -> str:
    """Resolve an agency label without selecting a tenant or contacting a service."""
    label = str(value or "Unknown").strip() or "Unknown"
    if county_profile is None:
        return INHERITED_AGENCY_DISPLAY_LABELS.get(label.upper(), label)

    matches = tuple(
        agency
        for agency in county_profile.agencies
        if str(agency.get("abbreviation", "")).upper() == label.upper()
    )
    if len(matches) > 1:
        raise ValueError("County profile contains an ambiguous agency abbreviation.")
    if not matches:
        return label

    configured_name = str(matches[0].get("name", "")).strip()
    if not configured_name:
        raise ValueError("County profile agency name cannot be empty.")
    return configured_name
