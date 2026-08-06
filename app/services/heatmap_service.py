from datetime import datetime, timedelta, timezone
from math import floor

from app.config.settings import settings
from app.core.county_profiles import (
    resolve_county_profile,
    validate_heatmap_configuration,
)
from app.core.tenancy import CountyProfile, TenantContext
from app.core.tenant_authorization import authorize_tenant_action
from app.integrations.contracts import ModuleCapability
from app.services.cad_service import simplify_call
from app.integrations.cad.centralsquare import (
    CentralSquareCadAdapter as CentralSquareClient,
)
from app.services.centralsquare import CentralSquareAPIError
from app.services.map_service import valid_coordinates


ALLOWED_HEATMAP_HOURS = (2, 8, 12, 24)
HEATMAP_PAGE_LIMIT = 100
MAX_HEATMAP_PAGES = 20
HEATMAP_GRID_DEGREES = 0.01

# Broad Logan County operating extent. Outliers are excluded from map fitting.
MIN_LATITUDE = 37.40
MAX_LATITUDE = 38.40
MIN_LONGITUDE = -82.60
MAX_LONGITUDE = -81.40


def validate_heatmap_hours(hours: int) -> int:
    if hours not in ALLOWED_HEATMAP_HOURS:
        raise ValueError("Heat-map hours must be one of: 2, 8, 12, or 24.")
    return hours


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


def _heatmap_geometry(
    county_profile: CountyProfile | None = None,
) -> dict[str, float]:
    if county_profile is None:
        return {
            "min_latitude": MIN_LATITUDE,
            "max_latitude": MAX_LATITUDE,
            "min_longitude": MIN_LONGITUDE,
            "max_longitude": MAX_LONGITUDE,
            "grid_degrees": HEATMAP_GRID_DEGREES,
        }
    if not isinstance(county_profile, CountyProfile):
        raise ValueError("CountyProfile is required for configured heatmap geometry.")
    return dict(validate_heatmap_configuration(county_profile.heatmap_configuration))


def _within_operating_extent(
    latitude: float,
    longitude: float,
    geometry: dict[str, float],
) -> bool:
    return (
        geometry["min_latitude"] <= latitude <= geometry["max_latitude"]
        and geometry["min_longitude"] <= longitude <= geometry["max_longitude"]
    )


def _grid_cell(
    latitude: float,
    longitude: float,
    grid_degrees: float,
) -> tuple[int, int]:
    return (
        floor(latitude / grid_degrees),
        floor(longitude / grid_degrees),
    )


def _grid_center(
    cell: tuple[int, int],
    grid_degrees: float,
) -> tuple[float, float]:
    latitude_index, longitude_index = cell
    return (
        round((latitude_index + 0.5) * grid_degrees, 5),
        round((longitude_index + 0.5) * grid_degrees, 5),
    )


def _build_search_window(hours: int, now: datetime) -> tuple[datetime, datetime, dict]:
    end_time = now.astimezone(timezone.utc)
    start_time = end_time - timedelta(hours=hours)
    search_body = {
        "RecordCreatedFrom": start_time.isoformat(),
        "RecordCreatedTo": end_time.isoformat(),
        "OrderByField": "Created",
        "OrderByDirection": "Descending",
    }
    return start_time, end_time, search_body


def _get_historical_calls(
    client: CentralSquareClient,
    search_body: dict,
) -> list:
    calls_by_number = {}
    skip = 0

    for _page_number in range(MAX_HEATMAP_PAGES):
        result = client.search_cfs_core(
            search_body,
            skip=skip,
            limit=HEATMAP_PAGE_LIMIT,
        )
        page_calls = result.get("cfs_cores") or result.get("CFSCore") or []
        if not isinstance(page_calls, list):
            page_calls = []

        for raw_call in page_calls:
            if not isinstance(raw_call, dict):
                continue

            cfs_number = str(raw_call.get("CFSNumber") or "")
            if cfs_number:
                calls_by_number[cfs_number] = raw_call

        if len(page_calls) < HEATMAP_PAGE_LIMIT or not result.get("next"):
            break

        skip += len(page_calls)
    else:
        raise CentralSquareAPIError(
            f"Historical CFS search exceeded the {MAX_HEATMAP_PAGES}-page safety limit."
        )

    return list(calls_by_number.values())


def build_heatmap_snapshot(
    raw_calls: list,
    hours: int,
    now: datetime | None = None,
    county_profile: CountyProfile | None = None,
) -> dict:
    geometry = _heatmap_geometry(county_profile)
    grid_degrees = geometry["grid_degrees"]
    hours = validate_heatmap_hours(hours)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)
    start_time = current_time - timedelta(hours=hours)

    cells = {}
    invalid_time_calls = 0
    outside_window_calls = 0
    unmapped_calls = 0
    outside_extent_calls = 0
    within_window_calls = 0

    for raw_call in raw_calls:
        call = simplify_call(raw_call)
        call_time = _parse_datetime(call.get("call_datetime"))
        if call_time is None:
            invalid_time_calls += 1
            continue
        if call_time < start_time or call_time > current_time:
            outside_window_calls += 1
            continue

        within_window_calls += 1
        latitude = call.get("latitude")
        longitude = call.get("longitude")
        if not valid_coordinates(latitude, longitude):
            unmapped_calls += 1
            continue

        latitude_value = float(latitude)
        longitude_value = float(longitude)
        if not _within_operating_extent(
            latitude_value,
            longitude_value,
            geometry,
        ):
            outside_extent_calls += 1
            continue

        cell_key = _grid_cell(latitude_value, longitude_value, grid_degrees)
        cell = cells.setdefault(
            cell_key,
            {
                "count": 0,
                "agency_counts": {},
            },
        )
        cell["count"] += 1
        agency = str(call.get("agency") or "Unknown")
        cell["agency_counts"][agency] = cell["agency_counts"].get(agency, 0) + 1

    max_count = max(
        (cell["count"] for cell in cells.values()),
        default=0,
    )

    features = []
    for cell_key, cell in sorted(cells.items()):
        latitude_center, longitude_center = _grid_center(
            cell_key,
            grid_degrees,
        )
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [longitude_center, latitude_center],
                },
                "properties": {
                    "count": cell["count"],
                    "weight": round(cell["count"] / max_count, 4) if max_count else 0,
                    "agency_counts": cell["agency_counts"],
                },
            }
        )

    mapped_calls = sum(cell["count"] for cell in cells.values())
    agencies = sorted(
        {
            agency
            for cell in cells.values()
            for agency in cell["agency_counts"]
            if agency and agency != "Unknown"
        }
    )

    return {
        "type": "FeatureCollection",
        "generated_at": current_time.isoformat(),
        "cad_connected": True,
        "window": {
            "hours": hours,
            "from": start_time.isoformat(),
            "to": current_time.isoformat(),
        },
        "summary": {
            "records_returned": len(raw_calls),
            "within_window_calls": within_window_calls,
            "mapped_calls": mapped_calls,
            "unmapped_calls": unmapped_calls,
            "outside_extent_calls": outside_extent_calls,
            "invalid_time_calls": invalid_time_calls,
            "outside_window_calls": outside_window_calls,
            "displayed_calls": mapped_calls,
            "displayed_cells": len(cells),
            "not_mapped_calls": unmapped_calls + outside_extent_calls,
        },
        "agencies": agencies,
        "features": features,
    }


def build_empty_heatmap_snapshot(hours: int) -> dict:
    hours = validate_heatmap_hours(hours)
    now = datetime.now(timezone.utc)
    return {
        "type": "FeatureCollection",
        "generated_at": now.isoformat(),
        "cad_connected": False,
        "window": {
            "hours": hours,
            "from": (now - timedelta(hours=hours)).isoformat(),
            "to": now.isoformat(),
        },
        "summary": {
            "records_returned": 0,
            "within_window_calls": 0,
            "mapped_calls": 0,
            "unmapped_calls": 0,
            "outside_extent_calls": 0,
            "invalid_time_calls": 0,
            "outside_window_calls": 0,
            "displayed_calls": 0,
            "displayed_cells": 0,
            "not_mapped_calls": 0,
        },
        "agencies": [],
        "features": [],
    }


def get_live_heatmap_snapshot(
    hours: int,
    client: CentralSquareClient | None = None,
    now: datetime | None = None,
    tenant_context: TenantContext | None = None,
) -> dict:
    if settings.deployment_mode == "synthetic-disconnected":
        return build_empty_heatmap_snapshot(hours)

    county_profile: CountyProfile | None = None
    if tenant_context is not None:
        county_profile = resolve_county_profile(tenant_context)
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.HEATMAP,
            "read",
        )
        authorize_tenant_action(
            tenant_context,
            county_profile,
            ModuleCapability.ACTIVE_CALLS,
            "read",
        )

    hours = validate_heatmap_hours(hours)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    client = client or CentralSquareClient()
    _start_time, _end_time, search_body = _build_search_window(hours, current_time)
    raw_calls = _get_historical_calls(client, search_body)
    if county_profile is None:
        return build_heatmap_snapshot(raw_calls, hours=hours, now=current_time)
    return build_heatmap_snapshot(
        raw_calls,
        hours=hours,
        now=current_time,
        county_profile=county_profile,
    )
