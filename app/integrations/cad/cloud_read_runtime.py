"""Disabled-by-default runtime wiring for the cloud CAD read connector.

Only normalized display fields are retained in memory. Raw upstream payloads,
credentials, and tokens are never logged or persisted by this module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping

import httpx

from app.integrations.cad.cloud_read_config import (
    CALL_FIELDS,
    CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL,
    CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL,
    CENTRALSQUARE_DOCUMENTED_TOKEN_URL,
    CENTRALSQUARE_SECRET_JSON_KEYS,
    UNIT_FIELDS,
    CloudCadReadConfig,
)
from app.integrations.cad.cloud_read_connector import (
    CentralSquareCredentials,
    CloudCadConnectorError,
    CloudCentralSquareReadConnector,
    HttpRequest,
)

STATUS_OPERATIONS = (
    "search_calls",
    "get_call",
    "search_units",
    "get_configurations",
)
LOGGER = logging.getLogger(__name__)


class SecretsManagerCredentialProvider:
    """Resolve exactly one configured secret ARN, lazily and on first poll."""

    def __init__(self, allowed_secret_arn: str, *, client: Any | None = None) -> None:
        if not allowed_secret_arn:
            raise ValueError("An exact allowed CAD secret ARN is required.")
        self._allowed_secret_arn = allowed_secret_arn
        self._client = client

    def get_credentials(self, secret_reference: str) -> CentralSquareCredentials:
        if secret_reference != self._allowed_secret_arn:
            raise ValueError("CAD secret reference is outside the exact allowlist.")
        client = self._client
        if client is None:
            import boto3

            client = boto3.client("secretsmanager", region_name="us-east-1")
            self._client = client
        response = client.get_secret_value(SecretId=self._allowed_secret_arn)
        try:
            payload = json.loads(response["SecretString"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("CAD secret does not contain valid JSON credentials.") from None
        if set(payload) != set(CENTRALSQUARE_SECRET_JSON_KEYS):
            raise ValueError("CAD secret must contain only the reviewed credential keys.")
        return CentralSquareCredentials(
            username=str(payload["username"]), password=str(payload["password"])
        )


class HttpxReadTransport:
    """Small adapter that preserves the connector's bounded request contract."""

    def send(self, request: HttpRequest) -> httpx.Response:
        return httpx.request(
            request.method,
            request.url,
            headers=dict(request.headers),
            data=dict(request.form) if request.form is not None else None,
            json=dict(request.json_body) if request.json_body is not None else None,
            params=dict(request.query) if request.query is not None else None,
            timeout=request.timeout_seconds,
        )


@dataclass(frozen=True, slots=True)
class CloudCadDisplayState:
    calls: tuple[Mapping[str, Any], ...] = ()
    units: tuple[Mapping[str, Any], ...] = ()
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    error_code: str = ""

    def status(self, *, now: datetime | None = None) -> Mapping[str, Any]:
        current = now or datetime.now(timezone.utc)
        if self.last_success_at is None:
            freshness = "disabled" if self.last_attempt_at is None else "awaiting-success"
            age_seconds = None
        else:
            age_seconds = max(int((current - self.last_success_at).total_seconds()), 0)
            freshness = "current" if age_seconds <= 120 else "stale"
        return MappingProxyType(
            {
                "freshness": freshness,
                "age_seconds": age_seconds,
                "call_count": len(self.calls),
                "unit_count": len(self.units),
                "error_code": self.error_code,
            }
        )


def _items(payload: Any, names: tuple[str, ...]) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, Mapping):
        values = next((payload[name] for name in names if isinstance(payload.get(name), list)), [])
    else:
        values = []
    return [item for item in values if isinstance(item, Mapping)]


def _text(value: Any) -> str:
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)[:256]
    return ""


def _choice(value: Any, *names: str) -> str:
    if not isinstance(value, Mapping):
        return _text(value)
    return next((_text(value.get(name)) for name in names if _text(value.get(name))), "")


def _primary_incident(item: Mapping[str, Any]) -> tuple[str, str]:
    value = item.get("IncidentCode")
    if isinstance(value, list):
        candidates = [entry for entry in value if isinstance(entry, Mapping)]
        primary = next((entry for entry in candidates if entry.get("IsPrimary") is True), None)
        value = (primary or (candidates[0] if candidates else {})).get("IncidentCode")
    if isinstance(value, Mapping) and isinstance(value.get("IncidentCode"), Mapping):
        value = value.get("IncidentCode")
    return (
        _choice(value, "Code", "Abbreviation"),
        _choice(value, "Description", "Name"),
    )


def _timestamp(value: Mapping[str, Any]) -> str:
    return next(
        (
            _text(value.get(name))
            for name in (
                "Timestamp",
                "CreatedDateTime",
                "DateTime",
                "LogDateTime",
                "CommandDateTime",
            )
            if _text(value.get(name))
        ),
        "",
    )


def _command_log_text(value: Any) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return "".join(character for character in str(value) if character >= " " or character in "\n\t")[:2000]


def _normalize_command_logs(item: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
    logs = item.get("CommandLog") or item.get("command_logs")
    normalized: list[tuple[int, Mapping[str, str]]] = []
    for index, log in enumerate(logs if isinstance(logs, list) else []):
        if not isinstance(log, Mapping):
            continue
        unit = log.get("Unit")
        creator = log.get("Creator") or log.get("CreatedBy")
        entry = {
                "timestamp": _timestamp(log),
                "unit_number": _text(
                    log.get("UnitNumber")
                    or (unit.get("UnitNumber") if isinstance(unit, Mapping) else "")
                    or (unit.get("Number") if isinstance(unit, Mapping) else "")
                ),
                "text": _command_log_text(
                    log.get("Text")
                    or log.get("Message")
                    or log.get("Narrative")
                    or log.get("Description")
                ),
                "status": _choice(
                    log.get("Status") or log.get("StatusDescription"),
                    "Description",
                    "Abbreviation",
                    "Code",
                ),
                "creator": _choice(creator, "FullDescription", "Description", "Name"),
            }
        normalized.append((index, entry))
    normalized.sort(
        key=lambda item: (
            not bool(item[1]["timestamp"]),
            item[1]["timestamp"],
            item[0],
        )
    )
    return tuple(entry for _, entry in normalized[:500])


def _latest_call_status(item: Mapping[str, Any]) -> str:
    logs = item.get("CommandLog")
    candidates = []
    if isinstance(logs, list):
        for log in logs:
            if not isinstance(log, Mapping):
                continue
            status = _choice(log.get("Status"), "Description", "Abbreviation", "Code")
            if status:
                candidates.append((_timestamp(log), status))
    timestamped = [candidate for candidate in candidates if candidate[0]]
    if timestamped:
        return max(timestamped, key=lambda candidate: candidate[0])[1]
    if candidates:
        return candidates[0][1]
    return _choice(item.get("Status") or item.get("status"), "Description", "Abbreviation", "Code")


def _address_label(item: Mapping[str, Any]) -> str:
    address = item.get("Address") or item.get("address")
    if not isinstance(address, Mapping):
        return _text(address)
    full = _text(address.get("FullAddress") or address.get("FormattedAddress"))
    if full:
        return full
    street = _text(address.get("Street") or address.get("StreetAddress"))
    city = _text(address.get("City"))
    return ", ".join(part for part in (street, city) if part)[:256]


def _address_coordinates(item: Mapping[str, Any]) -> tuple[float | None, float | None]:
    address = item.get("Address") or item.get("address")
    if not isinstance(address, Mapping):
        return None, None
    try:
        latitude = float(address.get("Latitude"))
        longitude = float(address.get("Longitude"))
    except (TypeError, ValueError):
        return None, None
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return None, None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None, None
    if latitude == 0 and longitude == 0:
        return None, None
    return latitude, longitude


def _normalize_reporter(item: Mapping[str, Any]) -> Mapping[str, str]:
    reporter = item.get("Reporter") or item.get("Caller") or item.get("reporter")
    if not isinstance(reporter, Mapping):
        reporter = {}
    first = _text(reporter.get("First"))
    last = _text(reporter.get("Last"))
    name = _text(reporter.get("FreeformFullName")) or " ".join(
        part for part in (first, last) if part
    )
    phone = _text(
        reporter.get("ContactPhoneNumber")
        or reporter.get("FromPhoneNumber")
        or reporter.get("PhoneNumber")
    )

    def _strip(value: str) -> str:
        return "".join(character for character in value if character >= " " or character == "\t")[:256]

    return {
        "name": _strip(name),
        "phone": _strip(phone),
        "how_reported": _choice(reporter.get("HowReported"), "Description", "Name", "Code"),
    }


def _normalize_assigned_units(item: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
    units = item.get("Unit") or item.get("Units") or item.get("assigned_units")
    normalized = []
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, Mapping):
            continue
        number = _text(unit.get("UnitNumber") or unit.get("unit_number"))
        if not number:
            continue
        normalized.append(
            {
                "unit_number": number,
                "unit_type": _choice(unit.get("UnitType") or unit.get("unit_type"), "Description", "Code"),
                "agency": _choice(unit.get("Agency") or unit.get("agency"), "Abbreviation", "Name"),
                "status": _choice(
                    unit.get("Status") or unit.get("CurrentStatus") or unit.get("UnitStatus") or unit.get("status"),
                    "Description",
                    "Abbreviation",
                    "Code",
                ) or "Assigned",
            }
        )
    return tuple(normalized)


def _normalize_calls(items: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    normalized = []
    for item in items:
        incident_code, incident_description = _primary_incident(item)
        assigned = _normalize_assigned_units(item)
        command_logs = _normalize_command_logs(item)
        latitude, longitude = _address_coordinates(item)
        normalized.append(
            MappingProxyType(
                {
                    "cfs_number": _text(item.get("CFSNumber") or item.get("cfs_number")),
                    "incident_code": incident_code or _text(item.get("incident_code")),
                    "incident_description": incident_description or _text(item.get("incident_description")),
                    "priority": _choice(item.get("Priority") or item.get("priority"), "Level", "Code", "Description"),
                    "agency": _choice(item.get("PrimaryResponseAgency") or item.get("agency"), "Abbreviation", "Name"),
                    "status": _latest_call_status(item),
                    "call_datetime": _text(item.get("CallDateTime") or item.get("IncidentDateTime") or item.get("call_datetime")),
                    "location_label": _address_label(item),
                    "beat": _choice(item.get("Beat") or item.get("beat"), "Description", "Name", "Code", "Abbreviation"),
                    "zone": _choice(item.get("Zone") or item.get("zone"), "Description", "Name", "Code", "Abbreviation"),
                    "city": _choice(item.get("City") or item.get("city"), "Description", "Name", "Code", "Abbreviation"),
                    "assigned_units": assigned,
                    "command_logs": command_logs,
                    "reporter": _normalize_reporter(item),
                    "latitude": latitude,
                    "longitude": longitude,
                }
            )
        )
    return tuple(normalized)


def _normalize_units(items: list[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    normalized = []
    for item in items:
        incident = item.get("IncidentInformation")
        normalized.append(
            MappingProxyType(
                {
                    "unit_number": _text(item.get("UnitNumber") or item.get("unit_number")),
                    "agency": _choice(item.get("Agency"), "Abbreviation", "Name"),
                    "unit_type": _choice(item.get("UnitType"), "Description", "Code"),
                    "status": _choice(
                        item.get("Status") or item.get("CurrentStatus") or item.get("UnitStatus") or item.get("status"),
                        "Description",
                        "Abbreviation",
                        "Code",
                    ),
                    "station": _choice(item.get("Station"), "Description", "Name", "Code", "Abbreviation"),
                    "assignment_cfs_number": (
                        _text(incident.get("CFSNumber")) if isinstance(incident, Mapping) else ""
                    ),
                }
            )
        )
    return tuple(normalized)


def _log_normalized_presence(kind: str, items: tuple[Mapping[str, Any], ...]) -> None:
    presence = {
        field: sum(1 for item in items if item.get(field) not in (None, "", (), []))
        for field in (CALL_FIELDS if kind == "calls" else UNIT_FIELDS)
    }
    LOGGER.info(
        "cloud_cad_normalized_shape kind=%s item_count=%d field_presence=%s",
        kind,
        len(items),
        json.dumps(presence, sort_keys=True, separators=(",", ":")),
    )


class CloudCadReadPoller:
    """Own exactly one polling task and one minimized in-memory snapshot."""

    def __init__(
        self,
        connector: CloudCentralSquareReadConnector | None,
        *,
        enabled: bool = False,
        mode: str = "synthetic-disconnected",
        poll_seconds: int = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if poll_seconds != 30:
            raise ValueError("Cloud CAD runtime requires the reviewed 30-second poll interval.")
        if enabled and connector is None:
            raise ValueError("Enabled cloud CAD polling requires a connector.")
        self._connector = connector
        self._enabled = enabled
        self._mode = mode
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._state = CloudCadDisplayState()
        self._operation_counts = {name: 0 for name in STATUS_OPERATIONS}

    @property
    def state(self) -> CloudCadDisplayState:
        return self._state

    def status(self, *, now: datetime | None = None) -> Mapping[str, Any]:
        state_status = self._state.status(now=now)
        freshness = state_status["freshness"]
        if self._enabled and freshness == "disabled":
            freshness = "awaiting-success"
        return MappingProxyType(
            {
                "enabled": self._enabled,
                "mode": self._mode,
                "freshness": freshness,
                "age_seconds": state_status["age_seconds"],
                "error_code": state_status["error_code"],
                "call_count": state_status["call_count"],
                "unit_count": state_status["unit_count"],
                "operation_counts": dict(self._operation_counts),
            }
        )

    def _read_snapshot(self) -> tuple[Any, Any]:
        if self._connector is None:
            raise RuntimeError("Cloud CAD connector is unavailable.")
        self._operation_counts["search_calls"] += 1
        calls = self._connector.search_calls(
            {"CurrentlyActive": True}, skip=0, limit=100
        )
        self._operation_counts["search_units"] += 1
        units = self._connector.search_units({}, skip=0, limit=100)
        return calls, units

    def search_recent_calls(self, hours: int, now: datetime | None = None) -> list:
        """Read-only windowed historical CFS search via the same authenticated
        connector the poller uses. Returns raw CAD call dicts (unnormalized), like
        on-prem heatmap_service._get_historical_calls. Empty list if not enabled."""
        if not self._enabled or self._connector is None:
            return []
        end = now or self._clock()
        start = end - timedelta(hours=hours)
        body = {
            "RecordCreatedFrom": start.astimezone(timezone.utc).isoformat(),
            "RecordCreatedTo": end.astimezone(timezone.utc).isoformat(),
            "OrderByField": "Created",
            "OrderByDirection": "Descending",
        }
        calls_by_number: dict[str, Mapping[str, Any]] = {}
        skip = 0
        for _page_number in range(10):
            self._operation_counts["search_calls"] += 1
            result = self._connector.search_calls(body, skip=skip, limit=100)
            page_calls = result.get("cfs_cores") or result.get("CFSCore") or []
            if not isinstance(page_calls, list):
                page_calls = []
            for raw_call in page_calls:
                if not isinstance(raw_call, dict):
                    continue
                cfs_number = str(raw_call.get("CFSNumber") or "")
                if cfs_number:
                    calls_by_number[cfs_number] = raw_call
            if len(page_calls) < 100 or not result.get("next"):
                break
            skip += len(page_calls)
        else:
            raise CloudCadConnectorError(
                "search_pagination_exceeded", "search_calls"
            )
        return list(calls_by_number.values())

    async def poll_once(self) -> None:
        if not self._enabled or self._connector is None:
            return
        attempted = self._clock()
        try:
            calls_payload, units_payload = await asyncio.to_thread(
                self._read_snapshot
            )
            calls = _normalize_calls(
                _items(calls_payload, ("cfs_cores", "CFSCore", "calls", "items"))
            )
            units = _normalize_units(_items(units_payload, ("Units", "units", "items")))
            _log_normalized_presence("calls", calls)
            _log_normalized_presence("units", units)
            self._state = CloudCadDisplayState(
                calls=calls,
                units=units,
                last_success_at=self._clock(),
                last_attempt_at=attempted,
            )
        except CloudCadConnectorError as error:
            self._state = CloudCadDisplayState(
                calls=self._state.calls,
                units=self._state.units,
                last_success_at=self._state.last_success_at,
                last_attempt_at=attempted,
                error_code=error.code,
            )
        except Exception:
            self._state = CloudCadDisplayState(
                calls=self._state.calls,
                units=self._state.units,
                last_success_at=self._state.last_success_at,
                last_attempt_at=attempted,
                error_code="poll_failed",
            )

    async def _run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self._poll_seconds)

    def start(self) -> None:
        if not self._enabled:
            return
        if self._task is not None and not self._task.done():
            raise RuntimeError("Cloud CAD poller is already running.")
        self._task = asyncio.create_task(self._run(), name="cloud-cad-read-poller")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None


def build_cloud_cad_runtime(runtime_settings: Any) -> CloudCadReadPoller:
    """Build no AWS/HTTP dependency unless the explicit activation flag is true."""
    if not runtime_settings.cloud_cad_enabled:
        return CloudCadReadPoller(
            None,
            enabled=False,
            mode="synthetic-disconnected",
            poll_seconds=30,
        )
    config = CloudCadReadConfig.from_mapping(
        {
            "mode": runtime_settings.cloud_cad_mode,
            "tenant_id": runtime_settings.tenant_id,
            "secret_reference": runtime_settings.cloud_cad_secret_arn,
            "token_url": CENTRALSQUARE_DOCUMENTED_TOKEN_URL,
            "cad_base_url": CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL,
            "system_base_url": CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL,
            "poll_seconds": runtime_settings.cloud_cad_poll_seconds,
            "reconciliation_overlap_seconds": runtime_settings.cloud_cad_reconciliation_overlap_seconds,
            "webhooks_enabled": False,
        }
    )
    connector = CloudCentralSquareReadConnector(
        config,
        from_header="lcdash-cloud-pilot",
        secret_provider=SecretsManagerCredentialProvider(config.secret_reference),
        transport=HttpxReadTransport(),
        enabled=True,
    )
    return CloudCadReadPoller(
        connector,
        enabled=True,
        mode=config.mode.value,
        poll_seconds=config.poll_seconds,
    )
