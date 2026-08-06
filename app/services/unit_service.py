from datetime import datetime, timezone
from typing import Mapping

from app.core.county_profiles import resolve_county_profile
from app.core.tenancy import CountyProfile, TenantContext
from app.core.tenant_authorization import (
    TenantAuthorizationDenied,
    authorize_tenant_action,
)
from app.integrations.cad.centralsquare import (
    CentralSquareCadAdapter as CentralSquareClient,
)
from app.integrations.contracts import ModuleCapability
from app.services.centralsquare import CentralSquareAPIError


UNIT_PAGE_LIMIT = 100
MAX_UNIT_PAGES = 20

ACTIVE_STATUS_FLAGS = (
    "ConsiderAsDispatched",
    "ConsiderAsEnrouteToCFSLocation",
    "ConsiderAsArrivedAtCFSLocation",
    "ConsiderAsTransporting",
    "ConsiderAsEnrouteToSecondaryLocation",
    "ConsiderAsArrivedAtSecondaryLocation",
    "ConsiderAsArrivedAtPatient",
    "ConsiderAsBackupResponding",
    "ConsiderAsBackupArrived",
)

AVAILABLE_STATUS_NAMES = {
    "available",
    "at station",
    "in quarters",
    "posted",
    "staged",
    "move up",
    "enroute to move up",
    "returning",
}

ACTIVE_STATUS_NAMES = {
    "assigned",
    "dispatched",
    "enroute",
    "en route",
    "on scene",
    "arrived at",
    "transporting",
}

UNAVAILABLE_STATUS_TERMS = (
    "off duty",
    "out of service",
    "unavailable",
    "mechanical",
    "unstaffed",
    "maintenance",
    "training",
    "detail",
    "administrative",
)


def _safe_text(value, default: str = "") -> str:
    if value is None:
        return default

    return str(value)


def _get_nested(data: dict, *keys, default=""):
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return default

        current = current.get(key)

        if current is None:
            return default

    return current


def _dropdown_text(value: dict, *field_names: str) -> str:
    if not isinstance(value, dict):
        return ""

    for field_name in field_names:
        field_value = value.get(field_name)
        if field_value:
            return _safe_text(field_value)

    return ""


def _parse_datetime(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        cleaned_value = str(value)
        if cleaned_value.endswith("Z"):
            cleaned_value = cleaned_value[:-1] + "+00:00"

        parsed_value = datetime.fromisoformat(cleaned_value)
        if parsed_value.tzinfo is None:
            parsed_value = parsed_value.replace(tzinfo=timezone.utc)

        return parsed_value
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _valid_coordinate_pair(latitude, longitude) -> bool:
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
    except (TypeError, ValueError):
        return False

    if latitude_value == 0 and longitude_value == 0:
        return False

    return (
        -90 <= latitude_value <= 90
        and -180 <= longitude_value <= 180
    )


def _extract_unit_position(unit: dict) -> dict:
    candidates = []
    fallback_timestamp = _safe_text(unit.get("LastLocationUpdateTime"))

    for source_key, source_name, source_rank in (
        ("UnitLocation", "unit_location", 1),
        ("AVL", "avl", 2),
        ("PLT", "plt", 3),
    ):
        source = unit.get(source_key) or {}
        if not isinstance(source, dict):
            continue

        latitude = source.get("Latitude")
        longitude = source.get("Longitude")
        if not _valid_coordinate_pair(latitude, longitude):
            continue

        observed_at = _safe_text(
            source.get("Timestamp")
            or source.get("DateTime")
            or source.get("LocationTime")
            or source.get("LastUpdateTime")
            or source.get("LastLocationUpdateTime")
            or fallback_timestamp
        )

        candidates.append(
            {
                "latitude": float(latitude),
                "longitude": float(longitude),
                "source": source_name,
                "observed_at": observed_at,
                "source_rank": source_rank,
                "speed": source.get("Speed"),
                "direction": source.get("Direction"),
                "avl_source": _safe_text(source.get("AVLSource")),
            }
        )

    if not candidates:
        return {}

    selected_position = dict(max(
        candidates,
        key=lambda candidate: (
            _parse_datetime(candidate.get("observed_at")),
            -candidate["source_rank"],
        ),
    ))
    selected_position.pop("source_rank", None)
    return selected_position

def _configured_unit_status(
    status: dict,
    status_mappings: Mapping[str, str] | None,
) -> str:
    inherited_status = _dropdown_text(status, "Description", "Abbreviation") or "Unknown"
    if not status_mappings:
        return inherited_status

    for source_value in (
        _dropdown_text(status, "Abbreviation"),
        _dropdown_text(status, "Code"),
        _dropdown_text(status, "Description"),
    ):
        if not source_value:
            continue
        configured = status_mappings.get(source_value)
        if configured is None:
            configured = status_mappings.get(source_value.upper())
        if configured:
            return configured

    return inherited_status


def normalize_unit(
    unit: dict,
    status_mappings: Mapping[str, str] | None = None,
) -> dict:
    status = unit.get("Status") or unit.get("CurrentStatus") or unit.get("UnitStatus") or {}
    agency = unit.get("Agency") or {}
    unit_type = unit.get("UnitType") or {}
    responder = unit.get("Responder") or {}
    incident = unit.get("IncidentInformation") or {}
    station = unit.get("Station") or {}
    beat = unit.get("Beat") or {}

    semantic_status = {
        key: bool(status.get(key))
        for key in ACTIVE_STATUS_FLAGS
    }
    semantic_status["ConsiderAsInQuarters"] = bool(status.get("ConsiderAsInQuarters"))
    semantic_status["ConsiderAsStaged"] = bool(status.get("ConsiderAsStaged"))

    return {
        "unit_number": _safe_text(unit.get("UnitNumber")),
        "external_unit_number": _safe_text(unit.get("ExternalUnitNumber")),
        "unit_type": _dropdown_text(unit_type, "Description", "Code"),
        "agency": _dropdown_text(agency, "Abbreviation", "Name"),
        "responder": _dropdown_text(
            responder,
            "FullDescription",
            "CallSign",
            "Username",
        ),
        "responder_unique_identifier": _safe_text(
            responder.get("PersonnelUniqueIdentifier")
            or responder.get("UniqueIdentifier")
        ),
        "responder_username": _safe_text(responder.get("Username")),
        "responder_call_sign": _safe_text(responder.get("CallSign")),
        "status": _configured_unit_status(status, status_mappings),
        "status_abbreviation": _dropdown_text(status, "Abbreviation"),
        "last_status_time": _safe_text(unit.get("LastStatusTime")),
        "last_assigned_time": _safe_text(unit.get("LastAssignedTime")),
        "last_location_update_time": _safe_text(unit.get("LastLocationUpdateTime")),
        "position": _extract_unit_position(unit),
        "station": _dropdown_text(station, "Description", "Name", "Code", "Abbreviation"),
        "beat": _dropdown_text(beat, "Description", "Name", "Code", "Abbreviation"),
        "cfs_number": _safe_text(incident.get("CFSNumber")),
        "incident_code": _dropdown_text(incident.get("IncidentCode") or {}, "Code", "Description"),
        "location": _safe_text(
            _get_nested(incident, "Location", "FullAddress")
            or _get_nested(incident, "Location", "Address")
            or incident.get("LocationDetails")
            or ""
        ),
        "details": _safe_text(unit.get("UnitDetails")),
        "semantic_status": semantic_status,
    }


def classify_unit(unit: dict) -> str:
    status = (unit.get("status") or "").strip().lower()
    semantic_status = unit.get("semantic_status") or {}

    if unit.get("cfs_number"):
        return "active"

    if status in ACTIVE_STATUS_NAMES or any(
        semantic_status.get(flag)
        for flag in ACTIVE_STATUS_FLAGS
    ):
        return "active"

    if any(term in status for term in UNAVAILABLE_STATUS_TERMS):
        return "unavailable"

    if (
        status in AVAILABLE_STATUS_NAMES
        or semantic_status.get("ConsiderAsInQuarters")
        or semantic_status.get("ConsiderAsStaged")
    ):
        return "available"

    return "unknown"


def get_all_units(
    client: CentralSquareClient | None = None,
    county_profile: CountyProfile | None = None,
    *,
    tenant_context: TenantContext | None = None,
) -> list:
    if tenant_context is not None:
        if county_profile is not None:
            raise TenantAuthorizationDenied(
                "Trusted context and direct county profile cannot be combined."
            )
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.UNITS,
            "read",
        )

    client = client or CentralSquareClient()
    raw_units = []
    skip = 0

    for page_number in range(MAX_UNIT_PAGES):
        result = client.search_units({}, skip=skip, limit=UNIT_PAGE_LIMIT)
        page_units = result.get("Units") or result.get("units") or []

        if not isinstance(page_units, list):
            page_units = []

        raw_units.extend(
            unit
            for unit in page_units
            if isinstance(unit, dict)
        )

        if len(page_units) < UNIT_PAGE_LIMIT or not result.get("next"):
            break

        skip += len(page_units)
    else:
        raise CentralSquareAPIError(
            f"Unit search exceeded the {MAX_UNIT_PAGES}-page safety limit."
        )

    units_by_number = {}

    for raw_unit in raw_units:
        unit = normalize_unit(
            raw_unit,
            status_mappings=(
                county_profile.unit_status_mappings
                if county_profile is not None
                else None
            ),
        )
        unit_number = unit.get("unit_number") or ""

        if not unit_number:
            continue

        unit_key = unit_number.upper()
        existing_unit = units_by_number.get(unit_key)

        if (
            existing_unit is None
            or _parse_datetime(unit.get("last_status_time"))
            >= _parse_datetime(existing_unit.get("last_status_time"))
        ):
            units_by_number[unit_key] = unit

    return sorted(
        units_by_number.values(),
        key=lambda unit: (
            unit.get("agency") or "",
            unit.get("unit_number") or "",
        ),
    )
