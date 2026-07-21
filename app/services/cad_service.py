from datetime import datetime, timezone

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


def _parse_sort_datetime(value: str) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)

    try:
        cleaned_value = str(value)

        if cleaned_value.endswith("Z"):
            cleaned_value = cleaned_value.replace("Z", "+00:00")

        parsed_value = datetime.fromisoformat(cleaned_value)

        if parsed_value.tzinfo is None:
            parsed_value = parsed_value.replace(tzinfo=timezone.utc)

        return parsed_value

    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


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
        if isinstance(unit, dict) and unit.get("UnitNumber")
    ]

    return ", ".join(unit_numbers) if unit_numbers else "No units assigned"


def _latest_status(call: dict) -> str:
    command_log = call.get("CommandLog") or []
    candidates = []
    fallback_status = ""

    for entry in command_log:
        if not isinstance(entry, dict):
            continue

        status = entry.get("Status")
        if isinstance(status, dict):
            description = _safe_text(status.get("Description"))

            if not description:
                continue

            if not fallback_status:
                fallback_status = description

            timestamp = _log_timestamp(entry)
            if timestamp:
                candidates.append(
                    {
                        "description": description,
                        "sort_time": _parse_sort_datetime(timestamp),
                    }
                )

    if candidates:
        latest_status = max(candidates, key=lambda item: item["sort_time"])
        return latest_status["description"]

    return fallback_status or "Open"


def _log_timestamp(log: dict) -> str:
    return _safe_text(
        log.get("Timestamp")
        or log.get("CreatedDateTime")
        or log.get("DateTime")
        or log.get("LogDateTime")
        or log.get("CommandDateTime")
        or ""
    )


def _log_unit_number(log: dict) -> str:
    return _safe_text(
        log.get("UnitNumber")
        or _get_nested(log, "Unit", "UnitNumber")
        or _get_nested(log, "Unit", "Number")
        or ""
    )


def _log_status_text(log: dict) -> str:
    return _safe_text(
        _get_nested(log, "Status", "Description")
        or log.get("StatusDescription")
        or log.get("Status")
        or ""
    )


def _log_message_text(log: dict) -> str:
    return _safe_text(
        log.get("Text")
        or log.get("Message")
        or log.get("Narrative")
        or log.get("Description")
        or ""
    )


def _combined_log_text(log: dict) -> str:
    return f"{_log_status_text(log)} {_log_message_text(log)}".strip()


def _log_belongs_to_unit(log: dict, unit_number: str) -> bool:
    if not unit_number:
        return False

    log_unit_number = _log_unit_number(log)

    if log_unit_number:
        return log_unit_number.upper() == unit_number.upper()

    combined_text = _combined_log_text(log).upper()
    return unit_number.upper() in combined_text


def _classify_unit_status_from_text(text: str) -> str:
    normalized_text = (text or "").lower()

    if "transport" in normalized_text:
        return "Transporting"

    if "arriv" in normalized_text or "on scene" in normalized_text or "scene" in normalized_text:
        return "On Scene"

    if "enroute" in normalized_text or "en route" in normalized_text or "route" in normalized_text:
        return "Enroute"

    if "clear" in normalized_text or "cleared" in normalized_text:
        return "Cleared"

    if "complete" in normalized_text or "available" in normalized_text:
        return "Cleared"

    if "dispatch" in normalized_text or "assigned" in normalized_text:
        return "Assigned"

    return ""


def _status_key_from_classified_status(status: str) -> str:
    normalized_status = (status or "").lower()

    if "transport" in normalized_status:
        return "transport"

    if "scene" in normalized_status or "arriv" in normalized_status:
        return "arrival"

    if "route" in normalized_status:
        return "enroute"

    if "clear" in normalized_status:
        return "clear"

    if "dispatch" in normalized_status or "assign" in normalized_status:
        return "dispatch"

    return ""


def _unit_status_times_from_command_log(call: dict, unit_number: str) -> dict:
    logs = call.get("CommandLog") or []

    status_times = {
        "dispatch": "",
        "enroute": "",
        "arrival": "",
        "transport": "",
        "clear": "",
    }

    latest_sort_times = {
        "dispatch": datetime.min.replace(tzinfo=timezone.utc),
        "enroute": datetime.min.replace(tzinfo=timezone.utc),
        "arrival": datetime.min.replace(tzinfo=timezone.utc),
        "transport": datetime.min.replace(tzinfo=timezone.utc),
        "clear": datetime.min.replace(tzinfo=timezone.utc),
    }

    for log in logs:
        if not isinstance(log, dict):
            continue

        if not _log_belongs_to_unit(log, unit_number):
            continue

        combined_text = _combined_log_text(log)
        classified_status = _classify_unit_status_from_text(combined_text)
        status_key = _status_key_from_classified_status(classified_status)
        timestamp = _log_timestamp(log)

        if not status_key or not timestamp:
            continue

        sort_time = _parse_sort_datetime(timestamp)

        if sort_time >= latest_sort_times[status_key]:
            latest_sort_times[status_key] = sort_time
            status_times[status_key] = timestamp

    return status_times


def _latest_unit_status_from_command_log(call: dict, unit_number: str) -> dict:
    logs = call.get("CommandLog") or []
    candidates = []

    for log in logs:
        if not isinstance(log, dict):
            continue

        if not _log_belongs_to_unit(log, unit_number):
            continue

        combined_text = _combined_log_text(log)
        classified_status = _classify_unit_status_from_text(combined_text)

        if not classified_status:
            continue

        timestamp = _log_timestamp(log)

        if not timestamp:
            continue

        candidates.append(
            {
                "status": classified_status,
                "timestamp": timestamp,
                "sort_time": _parse_sort_datetime(timestamp),
            }
        )

    if not candidates:
        return {
            "status": "",
            "timestamp": "",
        }

    latest_candidate = max(candidates, key=lambda item: item["sort_time"])

    return {
        "status": latest_candidate["status"],
        "timestamp": latest_candidate["timestamp"],
    }


def _status_timer_start(
    status: str,
    dispatch_time: str,
    enroute_time: str,
    arrival_time: str,
    transport_time: str,
    clear_time: str,
) -> str:
    normalized_status = (status or "").lower()

    if "clear" in normalized_status or "complete" in normalized_status:
        return (
            clear_time
            or transport_time
            or arrival_time
            or enroute_time
            or dispatch_time
            or ""
        )

    if "transport" in normalized_status:
        return (
            transport_time
            or arrival_time
            or enroute_time
            or dispatch_time
            or ""
        )

    if "scene" in normalized_status or "arriv" in normalized_status:
        return (
            arrival_time
            or enroute_time
            or dispatch_time
            or ""
        )

    if "route" in normalized_status:
        return enroute_time or dispatch_time or ""

    if "dispatch" in normalized_status or "assign" in normalized_status:
        return dispatch_time or ""

    return (
        clear_time
        or transport_time
        or arrival_time
        or enroute_time
        or dispatch_time
        or ""
    )


def _simplify_units(call: dict) -> list:
    units = call.get("Unit") or []
    simplified_units = []

    for unit in units:
        if not isinstance(unit, dict):
            continue

        unit_object_status = (
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

        unit_number = _safe_text(unit.get("UnitNumber"))

        command_log_times = _unit_status_times_from_command_log(
            call=call,
            unit_number=unit_number,
        )

        dispatch_time = _safe_text(
            unit.get("DispatchDateTime")
            or unit.get("DispatchedDateTime")
            or unit.get("AssignedDateTime")
            or unit.get("CreatedDateTime")
            or command_log_times["dispatch"]
            or ""
        )

        enroute_time = _safe_text(
            unit.get("EnrouteDateTime")
            or unit.get("EnRouteDateTime")
            or unit.get("EnrouteTime")
            or command_log_times["enroute"]
            or ""
        )

        arrival_time = _safe_text(
            unit.get("ArrivalDateTime")
            or unit.get("ArrivedDateTime")
            or unit.get("OnSceneDateTime")
            or unit.get("OnSceneTime")
            or command_log_times["arrival"]
            or ""
        )

        transport_time = _safe_text(
            unit.get("TransportDateTime")
            or unit.get("TransportingDateTime")
            or unit.get("TransportTime")
            or unit.get("DepartSceneDateTime")
            or unit.get("LeftSceneDateTime")
            or unit.get("PatientTransportDateTime")
            or command_log_times["transport"]
            or ""
        )

        clear_time = _safe_text(
            unit.get("ClearDateTime")
            or unit.get("ClearedDateTime")
            or unit.get("AvailableDateTime")
            or unit.get("ClearTime")
            or command_log_times["clear"]
            or ""
        )

        latest_log_status = _latest_unit_status_from_command_log(
            call=call,
            unit_number=unit_number,
        )

        status_text = (
            latest_log_status.get("status")
            or _safe_text(unit_object_status, "Assigned")
        )

        status_timer_start = (
            latest_log_status.get("timestamp")
            or _status_timer_start(
                status=status_text,
                dispatch_time=dispatch_time,
                enroute_time=enroute_time,
                arrival_time=arrival_time,
                transport_time=transport_time,
                clear_time=clear_time,
            )
        )

        simplified_units.append(
            {
                "unit_number": unit_number,
                "unit_type": _safe_text(_get_nested(unit, "UnitType", "Description")),
                "agency": _safe_text(
                    _get_nested(unit, "Agency", "Abbreviation")
                    or _get_nested(unit, "Agency", "Name")
                ),
                "status": status_text,
                "responder": _safe_text(responder),
                "dispatch_time": dispatch_time,
                "enroute_time": enroute_time,
                "arrival_time": arrival_time,
                "transport_time": transport_time,
                "clear_time": clear_time,
                "status_timer_start": status_timer_start,
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
                "timestamp": _log_timestamp(log),
                "unit_number": _log_unit_number(log),
                "text": _log_message_text(log),
                "status": _log_status_text(log),
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
