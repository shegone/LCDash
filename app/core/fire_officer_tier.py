"""The ``fire_officer`` tier: live operations with full call detail, nothing else.

Why this exists. Fire officers need to see the whole call -- address, incident,
priority, status, assigned units, and the call detail page with everything CAD
holds -- but have no business in analytics, reports, the knowledge library,
MAE, Mindshare/JACK, or the tools and quality pages (scoped by Ted).

Why an ALLOWLIST. Like the ``user`` and ``avatar`` tiers, and unlike
``dispatcher``, this role exists to narrow what an account reaches. The
application has well over a hundred routes and grows; with a denylist every
route added later would be open to fire officers until somebody remembered to
block it. Here the default is DENIED, so a new route is invisible to this tier
until it is deliberately listed. The route-table sweep in
tests/contracts/test_fire_officer_tier.py proves that against the real app.

Unlike the ``user`` tier there is NO field-sanitizing half. Seeing the full call
is the whole point of this role, so the operations, units, map, and
station-alert payloads pass through untouched. Do not add this role to
``sanitized_tier.restricts``; that would reduce exactly what this role is for.

What is deliberately NOT on the list, and why:

* ``/map/heatmap`` and its API: historical activity, which is analytics by
  another name. The map page hides its tab for this role.
* ``/callflow-cards``: NGA911 proprietary prototype, supervisor/dispatcher/admin.
* Speech endpoints (``/api/voice/*``, ``/api/cloud-ai/*``): shared with MAE and
  the Voice Lab, so listing them would open both. Station alerts therefore show
  but do not speak for this role, the same as the ``user`` tier.
* ``/api/integrations/*`` and ``/integrations``: delivery-health tooling.
"""

from __future__ import annotations

from app.core.cloud_pilot_roles import PilotRole

# Exact paths this tier may request.
_FIRE_OFFICER_ALLOWED_EXACT = frozenset(
    {
        "/",
        "/dashboard",
        "/active-calls",
        "/units",
        "/map",
        "/station-alerts",
        "/logout",
        "/health",
        "/api/identity/whoami",
        # Data behind the allowed views, unreduced.
        "/api/operations/snapshot",
        "/api/operations/events",
        "/api/operations/active-calls",
        "/api/operations/units",
        "/api/operations/map",
        "/api/operations/map/reference",
        "/api/operations/map/tile-styles",
        "/api/operations/station-alerts",
    }
)

# Prefixes this tier may request.
_FIRE_OFFICER_ALLOWED_PREFIXES = (
    "/static/",
    # Call detail: /calls/{cfs_number}.
    "/calls/",
    # The map's basemap tiles and reference layers.
    "/api/operations/map/tiles/",
    "/api/operations/map/reference/",
)


def is_path_allowed_for_fire_officer(path: str) -> bool:
    """Deny by default. A path is reachable only if it is named above."""

    clean = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if clean in _FIRE_OFFICER_ALLOWED_EXACT or f"{clean}/" in _FIRE_OFFICER_ALLOWED_EXACT:
        return True
    return any(clean.startswith(prefix) for prefix in _FIRE_OFFICER_ALLOWED_PREFIXES)


def restricts(role: str | PilotRole | None) -> bool:
    """True when this role gets the fire-officer tier."""

    return str(role or "") == str(PilotRole.FIRE_OFFICER)
