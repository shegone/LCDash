from datetime import datetime, timezone


TIME_FIELDS = {
    "Dispatched": "dispatched_at",
    "Enroute": "enroute_at",
    "Staged": "staged_at",
    "OnScene": "on_scene_at",
    "AtPatient": "at_patient_at",
    "BackupEnroute": "backup_enroute_at",
    "BackupArrived": "backup_arrived_at",
    "Leaving": "leaving_at",
    "Transporting": "transporting_at",
    "ArrivedAt": "arrived_at",
    "Available": "available_at",
    "InQuarters": "in_quarters_at",
}


def _safe_text(value) -> str:
    return str(value or "").strip()


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None

    try:
        cleaned = str(value).strip()
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _dropdown_text(value, *preferred_fields) -> str:
    if value is None:
        return ""

    if isinstance(value, list):
        return _dropdown_text(value[0], *preferred_fields) if value else ""

    if not isinstance(value, dict):
        return _safe_text(value)

    fields = preferred_fields or (
        "Abbreviation",
        "Code",
        "Description",
        "Name",
        "Level",
        "FullDescription",
        "UniqueIdentifier",
    )
    for field in fields:
        text = _safe_text(value.get(field))
        if text:
            return text

    return ""


def normalize_personnel_identity(value) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", _safe_text(value)

    unique_identifier = _safe_text(value.get("UniqueIdentifier"))
    first_name = _safe_text(value.get("FirstName"))
    last_name = _safe_text(value.get("LastName"))
    suffix = _safe_text(value.get("Suffix"))
    display_name = " ".join(
        part for part in (first_name, last_name, suffix) if part
    )

    if not display_name:
        display_name = _dropdown_text(
            value,
            "Username",
            "CallSign",
            "EmployeeCode",
            "FullDescription",
            "Description",
        )

    return unique_identifier, display_name


def _primary_incident(raw_call: dict) -> tuple[str, str]:
    incident_rows = raw_call.get("IncidentCode") or []
    if not isinstance(incident_rows, list):
        incident_rows = [incident_rows]
    incident_rows = [row for row in incident_rows if isinstance(row, dict)]
    if not incident_rows:
        return "", ""

    primary = next(
        (row for row in incident_rows if row.get("IsPrimary")),
        incident_rows[0],
    )
    incident = primary.get("IncidentCode") or primary
    return (
        _dropdown_text(incident, "Code", "Abbreviation"),
        _dropdown_text(incident, "Description", "Name", "FullDescription"),
    )


def _disposition(raw_call: dict) -> tuple[str, str]:
    rows = raw_call.get("Disposition") or []
    if not isinstance(rows, list):
        rows = [rows]
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return "", ""

    disposition = rows[0].get("Disposition") or rows[0]
    return (
        _dropdown_text(disposition, "Code", "Abbreviation"),
        _dropdown_text(disposition, "Description", "Name", "FullDescription"),
    )


def _coordinates(address: dict) -> tuple[float | None, float | None]:
    try:
        latitude = float(address.get("Latitude"))
        longitude = float(address.get("Longitude"))
    except (TypeError, ValueError):
        return None, None

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None
    if latitude == 0 and longitude == 0:
        return None, None

    return round(latitude, 4), round(longitude, 4)


def _normalize_times(raw_times: dict) -> dict:
    raw_times = raw_times if isinstance(raw_times, dict) else {}
    return {
        normalized_field: _parse_datetime(raw_times.get(source_field))
        for source_field, normalized_field in TIME_FIELDS.items()
    }


def _latest_completion_time(call_times: list, unit_responses: list) -> datetime | None:
    candidates = []
    for row in call_times + unit_responses:
        candidates.extend(
            [
                row.get("available_at"),
                row.get("in_quarters_at"),
                row.get("arrived_at"),
            ]
        )
    valid = [value for value in candidates if isinstance(value, datetime)]
    return max(valid) if valid else None


def normalize_analytics_bundle(
    raw_call: dict,
    raw_analytics: dict,
    roster_by_unit: dict | None = None,
    collected_at: datetime | None = None,
) -> dict:
    roster_by_unit = roster_by_unit or {}
    collected_at = collected_at or datetime.now(timezone.utc)
    cfs_number = _safe_text(raw_call.get("CFSNumber"))
    if not cfs_number:
        raise ValueError("CentralSquare call is missing CFSNumber.")

    incident_code, incident_description = _primary_incident(raw_call)
    disposition_code, disposition_description = _disposition(raw_call)
    call_taker_unique_identifier, call_taker_name = (
        normalize_personnel_identity(raw_call.get("CallTaker"))
    )
    address = raw_call.get("Address") or {}
    address = address if isinstance(address, dict) else {}
    latitude, longitude = _coordinates(address)

    call_times = []
    for raw_row in raw_analytics.get("CallTimes") or []:
        if not isinstance(raw_row, dict):
            continue
        agency_ori = _safe_text(raw_row.get("AgencyORI"))
        if not agency_ori:
            continue
        call_times.append(
            {
                "cfs_number": cfs_number,
                "agency_ori": agency_ori,
                **_normalize_times(raw_row),
            }
        )

    unit_responses = []
    units = []
    for raw_unit in raw_analytics.get("Unit") or []:
        if not isinstance(raw_unit, dict):
            continue
        unit_number = _safe_text(raw_unit.get("UnitNumber"))
        if not unit_number:
            continue

        roster_unit = roster_by_unit.get(unit_number.upper()) or {}
        unit_type = (
            _dropdown_text(raw_unit.get("UnitType"), "Description", "Abbreviation", "Code")
            or _safe_text(roster_unit.get("unit_type"))
        )
        station = _safe_text(roster_unit.get("station"))
        agency = _safe_text(roster_unit.get("agency"))
        response = {
            "cfs_number": cfs_number,
            "unit_number": unit_number,
            "unit_type": unit_type,
            "station": station,
            "beat": _dropdown_text(raw_unit.get("Beat"), "Description", "Name", "Code"),
            **_normalize_times(raw_unit.get("UnitTimes") or {}),
        }
        unit_responses.append(response)
        units.append(
            {
                "unit_number": unit_number,
                "agency": agency,
                "unit_type": unit_type,
                "station": station,
                "last_seen_at": collected_at,
            }
        )

    call_record = {
        "cfs_number": cfs_number,
        "dispatch_agency": _dropdown_text(
            raw_call.get("DispatchAgency"),
            "Abbreviation",
            "Name",
            "Description",
        ),
        "response_agency": _dropdown_text(
            raw_call.get("PrimaryResponseAgency"),
            "Abbreviation",
            "Name",
            "Description",
        ),
        "call_taker": call_taker_name,
        "call_taker_unique_identifier": call_taker_unique_identifier,
        "incident_code": incident_code,
        "incident_description": incident_description,
        "priority": _dropdown_text(raw_call.get("Priority"), "Level", "Description", "Code"),
        "disposition_code": disposition_code,
        "disposition_description": disposition_description,
        "beat": _dropdown_text(raw_call.get("Beat"), "Description", "Name", "Code"),
        "zone": _dropdown_text(raw_call.get("Zone"), "Description", "Name", "Code"),
        "city": _dropdown_text(address.get("City"), "Description", "Name", "Abbreviation")
        or _safe_text(address.get("City")),
        "latitude": latitude,
        "longitude": longitude,
        "is_scheduled": bool(raw_call.get("IsScheduledCall", False)),
        "incident_at": _parse_datetime(raw_call.get("IncidentDateTime")),
        "call_received_at": _parse_datetime(raw_call.get("CallDateTime")),
        "closed_at": _latest_completion_time(call_times, unit_responses),
        "source_collected_at": collected_at,
    }

    return {
        "call": call_record,
        "call_times": call_times,
        "unit_responses": unit_responses,
        "units": units,
    }
