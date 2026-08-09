"""The restricted ``user`` tier: what it may reach, and what it may see.

Why this exists. Every pilot account currently holds ``supervisor``, which is
unrestricted access to live CAD including patient and reporter PII. That was a
deliberate, informed choice for the pilot cohort -- but the cohort includes
staff from two vendors (nga911.com, css-mindshare.com) and three outside
agencies (boonewv.com, e911.org, leasa.org). This tier is what lets those
people use the product without seeing PII.

Two independent gates, because either alone fails badly:

1. **A path allowlist**, applied deny-by-default. The application has well over
   a hundred routes and grows; gating them one by one means every route added
   later is reachable until somebody remembers. Here the default is DENIED, so
   a new route is invisible to this tier until it is deliberately listed.

2. **A field allowlist** on the data that does get through. Removing known-bad
   fields ("drop reporter, drop narrative") fails the moment CAD returns a
   field nobody anticipated. Only the named fields survive; everything else,
   known or not, is dropped.

Scope decided by Ted, 2026-08-09: dashboard (address, call type, responder
unit numbers, plus aggregate counts), station alerts (address and call type),
GIS map WITHOUT links through to call detail, and the heat map. No active-call
list, no unit list, no call detail, and nothing from Intelligence or
Tools & Quality -- notably not MAE, who answers from live CAD and would
reopen the exact hole this tier closes.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from app.core.cloud_pilot_roles import PilotRole

# --------------------------------------------------------------------------
# Path allowlist
# --------------------------------------------------------------------------

# Exact paths this tier may request.
_USER_ALLOWED_EXACT = frozenset(
    {
        "/",
        "/dashboard",
        "/map",
        "/map/heatmap",
        "/station-alerts",
        "/logout",
        # Data behind the four allowed views. Their payloads are sanitized
        # below -- being listed here is permission to receive the reduced
        # form, never the full one.
        "/api/operations/snapshot",
        "/api/operations/events",
        "/api/operations/map",
        "/api/operations/map/reference",
        "/api/operations/map/tile-styles",
        "/api/operations/map/heatmap",
        "/api/operations/station-alerts",
        "/api/identity/whoami",
        "/health",
    }
)

# Prefixes for the map's tile and reference-layer fetches, which carry
# coordinates or layer names in the path. Tiles are basemap imagery and
# reference layers are static geography -- neither carries call data.
_USER_ALLOWED_PREFIXES = (
    "/static/",
    "/api/operations/map/tiles/",
    "/api/operations/map/reference/",
)


def is_path_allowed_for_user(path: str) -> bool:
    """Deny by default. A path is reachable only if it is named above."""

    clean = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if clean in _USER_ALLOWED_EXACT or f"{clean}/" in _USER_ALLOWED_EXACT:
        return True
    # Prefix check must run against the same normalized `clean` path as the
    # exact-match check above, not the raw input. Matching against the raw
    # `path` let a query string or trailing slash slip a path through here
    # that the exact-match branch would have judged differently -- the two
    # checks disagreeing about what "the path" even is. Caught in review.
    return any(clean.startswith(prefix) for prefix in _USER_ALLOWED_PREFIXES)


def restricts(role: str | PilotRole | None) -> bool:
    """True when this role gets the sanitized tier."""

    return str(role or "") == str(PilotRole.USER)


# --------------------------------------------------------------------------
# Field allowlists
# --------------------------------------------------------------------------

# What a call looks like to this tier: where it is, what kind it is, and who
# is responding. Deliberately absent: reporter (name, phone, how reported),
# narrative and command logs, caller-identifying detail, and the CFS number
# itself -- without it there is nothing to look a call up by, which is what
# keeps "no call detail" true rather than merely unlinked.
#
# The address key differs by source and the lists below must match the shape
# they actually receive: the operations snapshot and station alerts emit
# "location" (operations_service.py:306,545; station_alert_service.py:311)
# while only the map emits "location_label" (map_service.py:113). A single
# shared list silently blanked the address on two of the four allowed views
# -- over-redaction that broke the tier's whole purpose, caught in review.
_CALL_FIELDS = ("location", "incident_code", "incident_description")
_ASSIGNED_UNIT_FIELDS = ("unit_number",)

# Station alerts carry responders as a flat list of unit numbers rather than
# assigned_units objects.
#
# ``event_id`` is NOT passed through: station_alert_service builds it as
# "stations|CFS-NUMBER|units|time", so forwarding it would hand this tier the
# very identifier the rest of the module removes. The UI still needs a stable
# row key, so it gets a digest of that string instead -- same value every
# request for the same alert, no way back to the CFS number.
#
# ``announcement`` is deliberately NOT here. It is free text composed by
# station_alert_service from whatever fields that module chooses, so
# allowlisting it would forward a string this module does not control -- a
# hole shaped like a field, and exactly what a field allowlist exists to
# prevent. Today it happens to contain only address, incident and time; the
# day someone adds the caller to it, this tier would leak silently. The UI
# composes its own line from the reduced fields instead.
_ALERT_FIELDS = (
    "location",
    "incident_code",
    "incident_description",
    "unit_numbers",
    "station_names",
)

# Aggregate counts only. No oldest-call timestamp and no agency breakdown:
# both are operational signal that this tier has no need for.
_DASHBOARD_STAT_FIELDS = (
    "active_calls",
    "assigned_units",
    "on_scene_calls",
    "high_priority_calls",
)


def _pick(source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {}
    return {field: source.get(field, "") for field in fields}


def _rows(value: Any) -> list:
    """Coerce a collection field to a list of items, never raising.

    ``or []`` alone is not total: a scalar where a list was expected (a count
    instead of the calls, say) survives the falsy check and then blows up on
    iteration. That would 500 all four allowed views for restricted accounts
    ONLY -- an outage no supervisor could reproduce. Caught in review.
    """

    return list(value) if isinstance(value, (list, tuple)) else []


def _opaque_key(value: Any) -> str:
    """A stable row key that cannot be read back as a CFS number."""

    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sanitize_call(call: Any) -> dict[str, Any]:
    """One call, reduced to address, type, and responding unit numbers."""

    reduced = _pick(call, _CALL_FIELDS)
    units = call.get("assigned_units") if isinstance(call, Mapping) else None
    reduced["assigned_units"] = [
        _pick(unit, _ASSIGNED_UNIT_FIELDS)
        for unit in _rows(units)
        if isinstance(unit, Mapping)
    ]
    return reduced


def sanitize_operations_snapshot(payload: Any) -> dict[str, Any]:
    """The dashboard payload: reduced calls plus aggregate counts.

    ``unit_rows``/``unit_stats`` are dropped entirely -- the unit view is not
    part of this tier, and shipping its data to a page that does not render it
    would still put it in the browser.
    """

    if not isinstance(payload, Mapping):
        return {}
    safe: dict[str, Any] = {
        key: payload.get(key)
        for key in ("connected", "system_status", "cad_status", "last_updated")
        if key in payload
    }
    safe["calls"] = [sanitize_call(call) for call in _rows(payload.get("calls"))]
    safe["dashboard_stats"] = _pick(
        payload.get("dashboard_stats"), _DASHBOARD_STAT_FIELDS
    )
    safe["sanitized_view"] = True
    return safe


def sanitize_station_alerts(payload: Any) -> dict[str, Any]:
    """Station alerts keep their station structure; alerts lose everything
    except address and call type."""

    if not isinstance(payload, Mapping):
        return {}
    safe = {
        key: payload.get(key)
        for key in (
            "connected",
            "roster_connected",
            "roster_warning",
            "error",
            "generated_at",
            "selected_station",
            "selected_stations",
            "stations",
        )
        if key in payload
    }
    safe["alerts"] = [
        {
            **_pick(alert, _ALERT_FIELDS),
            "row_key": _opaque_key(
                alert.get("event_id") if isinstance(alert, Mapping) else ""
            ),
        }
        for alert in _rows(payload.get("alerts"))
    ]
    # station_units names crews, which is roster information rather than the
    # address-and-type view this tier was scoped to.
    safe["station_units"] = []
    safe["sanitized_view"] = True
    return safe


# Map feature properties this tier may see. ``cfs_number`` and ``detail_url``
# are the two that would turn a pin into a route to call detail, so both go;
# Ted asked for the map without that link, and removing the identifier is
# what makes it true rather than a matter of the template's markup.
_MAP_CALL_PROPERTIES = ("kind", "incident_code", "incident_description", "location_label")
_MAP_UNIT_PROPERTIES = ("kind", "unit_number", "status")

# Top-level keys this tier may see, matched against what map_service.py
# actually emits (build_map_snapshot / build_empty_map_snapshot), not the
# names this list used to guess at. The previous list named "connected" and
# "counts", which do not exist on this payload -- the service emits
# "cad_connected" and "summary" -- so every restricted request silently lost
# both and the /map route only kept working because it merged the reduced
# dict back over the full one (see the route in app/main.py, fixed alongside
# this). type/generated_at/cad_connected/roster_connected/roster_warning/
# error are all structural or status fields, not call data, so they pass
# through unmodified. "summary" is call/unit *counts* -- like dashboard_stats,
# aggregate numbers with no per-agency or per-call breakdown -- so it is safe
# in the same way, but it is still picked field-by-field below rather than
# passed through, in case CAD ever nests something sharper into it.
_MAP_TOP_LEVEL_FIELDS = (
    "type",
    "generated_at",
    "cad_connected",
    "roster_connected",
    "roster_warning",
    "error",
)
_MAP_SUMMARY_FIELDS = (
    "total_calls",
    "mapped_calls",
    "unmapped_calls",
    "total_units",
    "mapped_units",
    "unmapped_units",
    "stale_units",
    "excluded_units",
)


def sanitize_map_snapshot(payload: Any) -> dict[str, Any]:
    """GeoJSON with call pins stripped of any route to call detail."""

    if not isinstance(payload, Mapping):
        return {}
    # Allowlisted top level too, matching the discipline everywhere else: a
    # dict(payload) passthrough would ship any new top-level key unreviewed.
    safe = {
        key: payload.get(key)
        for key in _MAP_TOP_LEVEL_FIELDS
        if key in payload
    }
    if isinstance(payload.get("summary"), Mapping):
        safe["summary"] = _pick(payload["summary"], _MAP_SUMMARY_FIELDS)
    features = []
    for feature in _rows(payload.get("features")):
        if not isinstance(feature, Mapping):
            continue
        properties = feature.get("properties")
        kind = (properties or {}).get("kind") if isinstance(properties, Mapping) else ""
        allowed = _MAP_UNIT_PROPERTIES if kind == "unit" else _MAP_CALL_PROPERTIES
        features.append(
            {
                "type": feature.get("type", "Feature"),
                "geometry": feature.get("geometry"),
                "properties": _pick(properties, allowed),
            }
        )
    safe["features"] = features
    safe["sanitized_view"] = True
    return safe
