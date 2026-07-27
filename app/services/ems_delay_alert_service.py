from app.config.settings import settings
from app.services.unit_service import classify_unit


def _normalized_unit_number(value: str) -> str:
    return (value or "").strip().upper()


def select_ems_supervisor_recipients(
    units: list[dict],
    configured_unit_numbers: tuple[str, ...] | None = None,
) -> list[dict]:
    """Resolve on-duty EMS supervisors from configured CAD unit assignments.

    A configured supervisor unit is eligible when it has an assigned responder
    and is not classified as off duty or otherwise unavailable. The returned
    PersonnelUniqueIdentifier is intended for CentralSquare's native paging
    command once that write command is enabled and tested.
    """

    configured_units = {
        _normalized_unit_number(unit_number)
        for unit_number in (
            configured_unit_numbers
            if configured_unit_numbers is not None
            else settings.ems_supervisor_unit_numbers
        )
        if _normalized_unit_number(unit_number)
    }

    recipients_by_personnel_id: dict[str, dict] = {}

    for unit in units:
        unit_number = _normalized_unit_number(unit.get("unit_number"))
        if unit_number not in configured_units:
            continue

        if classify_unit(unit) == "unavailable":
            continue

        personnel_id = str(
            unit.get("responder_unique_identifier") or ""
        ).strip()
        if not personnel_id:
            continue

        recipients_by_personnel_id.setdefault(
            personnel_id,
            {
                "personnel_unique_identifier": personnel_id,
                "unit_number": unit.get("unit_number") or "",
                "unit_status": unit.get("status") or "Unknown",
                "responder": unit.get("responder") or "",
                "responder_username": unit.get("responder_username") or "",
                "responder_call_sign": unit.get("responder_call_sign") or "",
            },
        )

    return sorted(
        recipients_by_personnel_id.values(),
        key=lambda recipient: (
            recipient.get("unit_number") or "",
            recipient.get("personnel_unique_identifier") or "",
        ),
    )
