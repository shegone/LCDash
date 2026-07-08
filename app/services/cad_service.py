from app.services.centralsquare import CentralSquareClient


def _primary_incident(call: dict) -> dict:
    incident_codes = call.get("IncidentCode") or []
    if not incident_codes:
        return {"code": "UNKNOWN", "description": "Unknown Incident"}

    primary = next(
        (item for item in incident_codes if item.get("IsPrimary")),
        incident_codes[0]
    )

    incident = primary.get("IncidentCode") or {}

    return {
        "code": incident.get("Code", "UNKNOWN"),
        "description": incident.get("Description", "Unknown Incident"),
    }


def _address_text(call: dict) -> str:
    address = call.get("Address") or {}

    parts = [
        address.get("Street"),
        address.get("City"),
    ]

    return ", ".join([part for part in parts if part]) or "Unknown Location"


def _unit_list(call: dict) -> str:
    units = call.get("Unit") or []
    unit_numbers = [unit.get("UnitNumber") for unit in units if unit.get("UnitNumber")]

    return ", ".join(unit_numbers) if unit_numbers else "No units assigned"


def _latest_status(call: dict) -> str:
    command_log = call.get("CommandLog") or []

    for entry in command_log:
        status = entry.get("Status")
        if status:
            return status.get("Description", "Unknown")

    return "Open"


def simplify_call(call: dict) -> dict:
    incident = _primary_incident(call)
    priority = call.get("Priority") or {}
    agency = call.get("PrimaryResponseAgency") or {}
    call_taker = call.get("CallTaker") or {}

    return {
        "cfs_number": call.get("CFSNumber", ""),
        "incident_code": incident["code"],
        "incident_description": incident["description"],
        "location": _address_text(call),
        "priority": priority.get("Level", ""),
        "agency": agency.get("Abbreviation", ""),
        "units": _unit_list(call),
        "status": _latest_status(call),
        "call_taker": call_taker.get("CallSign") or call_taker.get("Username", ""),
        "call_datetime": call.get("CallDateTime", ""),
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