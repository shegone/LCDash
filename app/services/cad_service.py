from app.services.centralsquare import CentralSquareClient


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


def _primary_incident(call: dict) -> dict:
    incident_codes = call.get("IncidentCode") or []
    if not incident_codes:
        return {"code": "UNKNOWN", "description": "Unknown Incident"}

    primary = next(
        (item for item in incident_codes if item.get("IsPrimary")),
        incident_codes[0],
    )

    incident = primary.get("IncidentCode") or {}

    return {
        "code": _safe_text(incident.get("Code"), "UNKNOWN"),
        "description": _safe_text(incident.get("Description"), "Unknown Incident"),
    }


def _address_text(call: dict) -> str:
    address = call.get("Address") or {}

    parts = [
        _safe_text(address.get("Street")),
        _safe_text(address.get("City")),
    ]

    return ", ".join([part for part in parts if part]) or "Unknown Location"


def _unit_list(call: dict) -> str:
    units = call.get("Unit") or []
    unit_numbers = [
        _safe_text(unit.get("UnitNumber"))
        for unit in units
        if unit.get("UnitNumber")
    ]

    return ", ".join(unit_numbers) if unit_numbers else "No units assigned"


def _latest_status(call: dict) -> str:
    command_log = call.get("CommandLog") or []

    for entry in command_log:
        status = entry.get("Status")
        if isinstance(status, dict):
            return _safe_text(status.get("Description"), "Unknown")

    return "Open"


def _simplify_units(call: dict) -> list:
    units = call.get("Unit") or []
    simplified_units = []

    for unit in units:
        if not isinstance(unit, dict):
            continue

        status = (
            _get_nested(unit, "Status", "Description")
            or _get_nested(unit, "CurrentStatus", "Description")
            or _get_nested(unit, "UnitStatus", "Description")
            or "Assigned"
        )

        responder = (
            _get_nested(unit, "Responder", "FullDescription")
            or _get_nested(unit, "Personnel", "FullDescription")
            or _get_nested(unit, "PrimaryPersonnel", "FullDescription")
            or ""
        )

        simplified_units.append(
            {
                "unit_number": _safe_text(unit.get("UnitNumber")),
                "unit_type": _safe_text(_get_nested(unit, "UnitType", "Description")),
                "agency": _safe_text(
                    _get_nested(unit, "Agency", "Abbreviation")
                    or _get_nested(unit, "Agency", "Name")
                ),
                "status": _safe_text(status, "Assigned"),
                "responder": _safe_text(responder),
                "dispatch_time": _safe_text(
                    unit.get("DispatchDateTime")
                    or unit.get("AssignedDateTime")
                    or unit.get("CreatedDateTime")
                    or ""
                ),
                "enroute_time": _safe_text(unit.get("EnrouteDateTime")),
                "arrival_time": _safe_text(unit.get("ArrivalDateTime")),
                "clear_time": _safe_text(unit.get("ClearDateTime")),
            }
        )

    return simplified_units


def _simplify_command_log(call: dict) -> list:
    logs = call.get("CommandLog") or []
    simplified_logs = []

    for log in logs:
        if not isinstance(log, dict):
            continue

        simplified_logs.append(
            {
                "timestamp": _safe_text(
                    log.get("Timestamp")
                    or log.get("CreatedDateTime")
                    or log.get("DateTime")
                    or ""
                ),
                "unit_number": _safe_text(log.get("UnitNumber")),
                "text": _safe_text(log.get("Text") or log.get("Message") or ""),
                "status": _safe_text(_get_nested(log, "Status", "Description")),
                "creator": _safe_text(
                    _get_nested(log, "Creator", "FullDescription")
                    or _get_nested(log, "CreatedBy", "FullDescription")
                    or ""
                ),
            }
        )

    return simplified_logs


def _simplify_reporter(call: dict) -> dict:
    reporter = call.get("Reporter") or {}

    if not isinstance(reporter, dict):
        reporter = {}

    first = _safe_text(reporter.get("First"))
    last = _safe_text(reporter.get("Last"))

    return {
        "first": first,
        "last": last,
        "name": " ".join([part for part in [first, last] if part]).strip(),
        "phone": _safe_text(
            reporter.get("ContactPhoneNumber")
            or reporter.get("FromPhoneNumber")
            or reporter.get("PhoneNumber")
            or ""
        ),
        "how_reported": _safe_text(_get_nested(reporter, "HowReported", "Description")),
    }


def simplify_call(call: dict) -> dict:
    incident = _primary_incident(call)
    priority = call.get("Priority") or {}
    agency = call.get("PrimaryResponseAgency") or {}
    call_taker = call.get("CallTaker") or {}
    address = call.get("Address") or {}

    return {
        "cfs_number": _safe_text(call.get("CFSNumber")),
        "incident_code": incident["code"],
        "incident_description": incident["description"],
        "location": _address_text(call),
        "priority": _safe_text(priority.get("Level")),
        "agency": _safe_text(agency.get("Abbreviation")),
        "units": _unit_list(call),
        "status": _latest_status(call),
        "call_taker": _safe_text(
            call_taker.get("CallSign") or call_taker.get("Username") or ""
        ),
        "call_datetime": _safe_text(call.get("CallDateTime")),
        "latitude": address.get("Latitude"),
        "longitude": address.get("Longitude"),
        "assigned_units": _simplify_units(call),
        "command_logs": _simplify_command_log(call),
        "reporter": _simplify_reporter(call),
        "raw": call,
    }


def get_active_calls() -> list:
    client = CentralSquareClient()

    result = client.search_cfs_core(
        {
            "CurrentlyActive": True,
            "OrderByField": "Created",
            "OrderByDirection": "Descending",
        }
    )

    raw_calls = result.get("cfs_cores", [])

    return [simplify_call(call) for call in raw_calls]


def get_call_detail(cfs_number: str) -> dict:
    client = CentralSquareClient()
    raw_call = client.get_cfs_core(cfs_number)
    return simplify_call(raw_call)