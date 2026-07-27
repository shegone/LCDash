from datetime import datetime, timedelta, timezone

from app.config.settings import settings
from app.services.cad_service import get_active_calls
from app.services.centralsquare import CentralSquareClient
from app.services.ems_delay_alert_database import EMSDelayAlertRepository
from app.services.unit_service import classify_unit, get_all_units


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


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None

    try:
        cleaned_value = str(value).strip()
        if cleaned_value.endswith("Z"):
            cleaned_value = cleaned_value[:-1] + "+00:00"

        parsed_value = datetime.fromisoformat(cleaned_value)
        if parsed_value.tzinfo is None:
            parsed_value = parsed_value.replace(tzinfo=timezone.utc)

        return parsed_value.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _normalized_values(values: tuple[str, ...]) -> set[str]:
    return {
        str(value).strip().upper()
        for value in values
        if str(value).strip()
    }


def _unit_is_ems(unit: dict) -> bool:
    agency = str(unit.get("agency") or "").strip().upper()
    unit_number = str(unit.get("unit_number") or "").strip().upper()
    agencies = _normalized_values(settings.ems_response_agencies)
    prefixes = _normalized_values(settings.ems_unit_prefixes)

    return (
        agency in agencies
        or any(unit_number.startswith(prefix) for prefix in prefixes)
    )


def _response_has_started(call: dict) -> bool:
    for unit in call.get("assigned_units") or []:
        if not _unit_is_ems(unit):
            continue

        status = str(unit.get("status") or "").strip().lower()
        if any(
            term in status
            for term in (
                "enroute",
                "en route",
                "on scene",
                "arriv",
                "transport",
                "clear",
                "available",
                "complete",
            )
        ):
            return True

        if any(
            unit.get(timestamp_field)
            for timestamp_field in (
                "enroute_time",
                "arrival_time",
                "transport_time",
                "clear_time",
            )
        ):
            return True

    return False


def classify_delayed_ems_call(
    call: dict,
    now: datetime | None = None,
) -> dict | None:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    incident_code = str(call.get("incident_code") or "").strip().upper()
    transfer_codes = _normalized_values(settings.ems_delay_transfer_codes)
    is_scheduled = bool(call.get("is_scheduled"))

    if is_scheduled:
        alert_type = "scheduled"
        reference_time = (
            _parse_datetime(call.get("incident_datetime"))
            or _parse_datetime(call.get("call_datetime"))
        )
    elif incident_code in transfer_codes:
        alert_type = "transfer"
        reference_time = _parse_datetime(call.get("call_datetime"))
    else:
        return None

    if reference_time is None:
        return None

    threshold_minutes = max(settings.ems_delay_threshold_minutes, 1)
    eligible_at = reference_time + timedelta(minutes=threshold_minutes)

    return {
        "cfs_number": str(call.get("cfs_number") or "").strip(),
        "alert_type": alert_type,
        "reference_time": reference_time,
        "eligible_at": eligible_at,
        "is_due": now >= eligible_at,
        "response_started": _response_has_started(call),
        "incident_code": incident_code,
        "incident_description": str(
            call.get("incident_description") or ""
        ).strip(),
        "location": str(call.get("location") or "").strip(),
    }


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def build_delay_alert_message(candidate: dict, sequence_number: int) -> str:
    alert_label = (
        "prescheduled call"
        if candidate["alert_type"] == "scheduled"
        else "transfer"
    )
    return (
        f"{_ordinal(sequence_number)} notification: EMS {alert_label} "
        f"{candidate['cfs_number']} has waited more than "
        f"{settings.ems_delay_threshold_minutes} minutes without an EMS unit "
        f"enroute. {candidate['incident_description']} - "
        f"{candidate['location']}"
    )


def evaluate_ems_delay_alerts(
    *,
    now: datetime | None = None,
    client: CentralSquareClient | None = None,
    repository: EMSDelayAlertRepository | None = None,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    client = client or CentralSquareClient()
    if repository is not None:
        return _evaluate_ems_delay_alerts(
            now=now,
            client=client,
            repository=repository,
        )

    with EMSDelayAlertRepository() as managed_repository:
        return _evaluate_ems_delay_alerts(
            now=now,
            client=client,
            repository=managed_repository,
        )


def _evaluate_ems_delay_alerts(
    *,
    now: datetime,
    client: CentralSquareClient,
    repository: EMSDelayAlertRepository,
) -> dict:
    calls = get_active_calls(client=client)
    roster_units = get_all_units(client=client)
    recipients = select_ems_supervisor_recipients(roster_units)
    observed_cfs_numbers: set[str] = set()
    due_count = 0
    dry_run_count = 0
    waiting_count = 0
    resolved_count = 0

    repository.initialize_schema()

    for call in calls:
        candidate = classify_delayed_ems_call(call, now=now)
        if candidate is None or not candidate["cfs_number"]:
            continue

        cfs_number = candidate["cfs_number"]
        observed_cfs_numbers.add(cfs_number)

        if candidate["response_started"]:
            if repository.resolve_alert(
                cfs_number,
                resolved_at=now,
                reason="EMS response started",
            ):
                resolved_count += 1
            continue

        state = repository.observe_candidate(candidate, observed_at=now)

        if not candidate["is_due"]:
            waiting_count += 1
            continue

        due_count += 1
        next_notification_at = state.get("next_notification_at")
        if next_notification_at and now < next_notification_at:
            continue

        if not recipients:
            repository.record_delivery_issue(
                candidate,
                observed_at=now,
                issue="No eligible EMS supervisor unit responder found.",
            )
            continue

        sequence_number = int(state.get("alert_count") or 0) + 1
        message = build_delay_alert_message(candidate, sequence_number)

        if settings.ems_delay_alert_mode != "dry_run":
            repository.record_delivery_issue(
                candidate,
                observed_at=now,
                issue=(
                    "Live paging is disabled until a CentralSquare API "
                    "run command is configured and approved."
                ),
            )
            continue

        repository.record_dry_run(
            candidate,
            sequence_number=sequence_number,
            recipients=recipients,
            message=message,
            observed_at=now,
            repeat_minutes=max(settings.ems_delay_repeat_minutes, 1),
        )
        dry_run_count += 1

    resolved_count += repository.resolve_missing_alerts(
        observed_cfs_numbers,
        resolved_at=now,
        reason="Call no longer active or monitored",
    )

    return {
        "status": "dry_run",
        "evaluated_at": now.isoformat(),
        "active_calls": len(calls),
        "monitored_calls": len(observed_cfs_numbers),
        "waiting_calls": waiting_count,
        "due_calls": due_count,
        "dry_run_notifications": dry_run_count,
        "resolved_alerts": resolved_count,
        "recipient_units": [
            recipient["unit_number"]
            for recipient in recipients
        ],
    }
