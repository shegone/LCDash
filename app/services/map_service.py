from datetime import datetime, timezone
from math import isfinite

from app.config.settings import settings
from app.core.county_profiles import resolve_county_profile
from app.core.tenancy import TenantContext
from app.core.tenant_authorization import authorize_tenant_action
from app.integrations.contracts import ModuleCapability
from app.services.operations_service import get_live_unit_snapshot
from app.services.unit_service import classify_unit


FRESH_LOCATION_SECONDS = 120
AGING_LOCATION_SECONDS = 300
STALE_LOCATION_SECONDS = 900
FUTURE_CLOCK_SKEW_SECONDS = 300


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None

    try:
        cleaned_value = str(value)
        if cleaned_value.endswith("Z"):
            cleaned_value = cleaned_value[:-1] + "+00:00"

        parsed_value = datetime.fromisoformat(cleaned_value)
        if parsed_value.tzinfo is None:
            parsed_value = parsed_value.replace(tzinfo=timezone.utc)

        return parsed_value.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def valid_coordinates(latitude, longitude) -> bool:
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (TypeError, ValueError):
        return False

    return (
        isfinite(latitude_value)
        and isfinite(longitude_value)
        and -90 <= latitude_value <= 90
        and -180 <= longitude_value <= 180
        and not (latitude_value == 0 and longitude_value == 0)
    )


def location_freshness(observed_at: str, now: datetime | None = None) -> dict:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    observed_time = _parse_datetime(observed_at)
    if observed_time is None:
        return {
            "freshness": "expired",
            "age_seconds": None,
            "is_mappable": False,
        }

    age_seconds = int((current_time - observed_time).total_seconds())
    if age_seconds < -FUTURE_CLOCK_SKEW_SECONDS:
        return {
            "freshness": "invalid",
            "age_seconds": age_seconds,
            "is_mappable": False,
        }

    age_seconds = max(age_seconds, 0)
    if age_seconds <= FRESH_LOCATION_SECONDS:
        freshness = "fresh"
    elif age_seconds <= AGING_LOCATION_SECONDS:
        freshness = "aging"
    elif age_seconds <= STALE_LOCATION_SECONDS:
        freshness = "stale"
    else:
        freshness = "expired"

    return {
        "freshness": freshness,
        "age_seconds": age_seconds,
        "is_mappable": freshness in {"fresh", "aging"},
    }


def _call_feature(call: dict) -> dict | None:
    latitude = call.get("latitude")
    longitude = call.get("longitude")
    if not valid_coordinates(latitude, longitude):
        return None

    cfs_number = str(call.get("cfs_number") or "")
    return {
        "type": "Feature",
        "id": f"call:{cfs_number}",
        "geometry": {
            "type": "Point",
            "coordinates": [float(longitude), float(latitude)],
        },
        "properties": {
            "kind": "call",
            "cfs_number": cfs_number,
            "incident_code": str(call.get("incident_code") or ""),
            "incident_description": str(call.get("incident_description") or ""),
            "priority": str(call.get("priority") or ""),
            "agency": str(call.get("agency") or ""),
            "status": str(call.get("status") or ""),
            "location_label": str(call.get("location") or ""),
            "call_datetime": str(call.get("call_datetime") or ""),
            "detail_url": f"/calls/{cfs_number}",
        },
    }


def _unit_feature(unit: dict, now: datetime) -> tuple[dict | None, str]:
    if classify_unit(unit) not in {"active", "available"}:
        return None, "excluded"

    position = unit.get("position") or {}
    latitude = position.get("latitude")
    longitude = position.get("longitude")
    if not valid_coordinates(latitude, longitude):
        return None, "unmapped"

    freshness = location_freshness(position.get("observed_at") or "", now=now)
    if not freshness["is_mappable"]:
        return None, freshness["freshness"]

    unit_number = str(unit.get("unit_number") or "")
    cfs_number = str(unit.get("cfs_number") or "")
    status = str(unit.get("roster_status") or unit.get("status") or "Unknown")
    status_group = str(unit.get("status_group") or status)

    properties = {
        "kind": "unit",
        "unit_number": unit_number,
        "agency": str(unit.get("agency") or ""),
        "unit_type": str(unit.get("unit_type") or ""),
        "status": status,
        "status_group": status_group,
        "cfs_number": cfs_number,
        "station": str(unit.get("station") or ""),
        "location_source": str(position.get("source") or ""),
        "location_observed_at": str(position.get("observed_at") or ""),
        "location_age_seconds": freshness["age_seconds"],
        "freshness": freshness["freshness"],
        "speed": position.get("speed"),
        "direction": position.get("direction"),
    }
    if cfs_number:
        properties["detail_url"] = f"/calls/{cfs_number}"

    return {
        "type": "Feature",
        "id": f"unit:{unit_number}",
        "geometry": {
            "type": "Point",
            "coordinates": [float(longitude), float(latitude)],
        },
        "properties": properties,
    }, freshness["freshness"]


def build_map_snapshot(unit_snapshot: dict, now: datetime | None = None) -> dict:
    generated_at = now or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)

    calls = unit_snapshot.get("calls") or []
    units = unit_snapshot.get("all_units") or []
    features = []
    mapped_calls = 0
    unmapped_calls = 0
    mapped_units = 0
    unmapped_units = 0
    stale_units = 0
    excluded_units = 0

    for call in calls:
        feature = _call_feature(call)
        if feature is None:
            unmapped_calls += 1
            continue

        features.append(feature)
        mapped_calls += 1

    for unit in units:
        feature, result = _unit_feature(unit, now=generated_at)
        if feature is not None:
            features.append(feature)
            mapped_units += 1
        elif result == "excluded":
            excluded_units += 1
        elif result in {"stale", "expired", "invalid"}:
            stale_units += 1
        else:
            unmapped_units += 1

    return {
        "type": "FeatureCollection",
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "cad_connected": True,
        "roster_connected": bool(unit_snapshot.get("roster_connected")),
        "roster_warning": str(unit_snapshot.get("roster_warning") or ""),
        "summary": {
            "total_calls": len(calls),
            "mapped_calls": mapped_calls,
            "unmapped_calls": unmapped_calls,
            "total_units": len(units),
            "mapped_units": mapped_units,
            "unmapped_units": unmapped_units,
            "stale_units": stale_units,
            "excluded_units": excluded_units,
        },
        "features": features,
    }


def build_empty_map_snapshot(error: str = "") -> dict:
    return {
        "type": "FeatureCollection",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cad_connected": False,
        "roster_connected": False,
        "roster_warning": "Full unit roster unavailable.",
        "error": error,
        "summary": {
            "total_calls": 0,
            "mapped_calls": 0,
            "unmapped_calls": 0,
            "total_units": 0,
            "mapped_units": 0,
            "unmapped_units": 0,
            "stale_units": 0,
            "excluded_units": 0,
        },
        "features": [],
    }


def get_live_map_snapshot(
    tenant_context: TenantContext | None = None,
) -> dict:
    if settings.deployment_mode == "synthetic-disconnected":
        return build_empty_map_snapshot()

    if tenant_context is None:
        return build_map_snapshot(get_live_unit_snapshot())

    county_profile = resolve_county_profile(tenant_context)
    authorize_tenant_action(
        tenant_context,
        county_profile,
        ModuleCapability.GIS,
        "read",
    )
    authorize_tenant_action(
        tenant_context,
        county_profile,
        ModuleCapability.ACTIVE_CALLS,
        "read",
    )
    return build_map_snapshot(
        get_live_unit_snapshot(tenant_context=tenant_context)
    )
