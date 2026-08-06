"""Fail-closed configuration contract for a future cloud CAD read path.

This module does not resolve secrets, create a transport, make network calls, or
activate a provider.  It validates only the non-secret activation envelope that
must be approved before a cloud-specific read adapter can be constructed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import ip_address
import re
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit


class CloudCadMode(StrEnum):
    SYNTHETIC_DISCONNECTED = "synthetic-disconnected"
    CENTRALSQUARE_READ_POLL = "centralsquare-read-poll"


CENTRALSQUARE_SECRET_NAME = "lcdash-p1-logan-use1/centralsquare/read-only"
CENTRALSQUARE_SECRET_JSON_KEYS = ("username", "password")
CENTRALSQUARE_SECRET_ARN_PREFIX = (
    "arn:aws:secretsmanager:us-east-1:862772137583:secret:"
    + CENTRALSQUARE_SECRET_NAME
)

CENTRALSQUARE_DOCUMENTED_HOST = "api-wv-logan-911.centralsquarecloudgov.com"
CENTRALSQUARE_DOCUMENTED_TOKEN_URL = (
    f"https://{CENTRALSQUARE_DOCUMENTED_HOST}/api/token"
)
CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL = (
    f"https://{CENTRALSQUARE_DOCUMENTED_HOST}/api/cad/v1"
)
CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL = (
    f"https://{CENTRALSQUARE_DOCUMENTED_HOST}/api/system/v1"
)
CENTRALSQUARE_DOCUMENTED_READ_PATHS = (
    ("POST", "/cfs_core/search"),
    ("GET", "/cfs_core/{CFSNumber}"),
    ("POST", "/units/search"),
    ("GET", "/configurations"),
)
CENTRALSQUARE_DOCUMENTED_MAX_PAGE_SIZE = 100


ALLOWED_CONFIGURATION_KEYS = frozenset(
    {
        "mode",
        "tenant_id",
        "secret_reference",
        "token_url",
        "cad_base_url",
        "system_base_url",
        "poll_seconds",
        "reconciliation_overlap_seconds",
        "webhooks_enabled",
    }
)

CALL_FIELDS = (
    "cfs_number",
    "incident_code",
    "incident_description",
    "priority",
    "agency",
    "status",
    "call_datetime",
    "location_label",
    "beat",
    "zone",
    "city",
    "assigned_units",
    "command_logs",
    "latitude",
    "longitude",
)
UNIT_FIELDS = (
    "unit_number",
    "agency",
    "unit_type",
    "status",
    "station",
    "assignment_cfs_number",
)
FORBIDDEN_OPERATIONS = (
    "acknowledge",
    "dispatch",
    "register_subscription",
    "send_alert",
    "send_message",
    "send_page",
    "trigger_tone",
    "update_call",
)
TENANT_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def _https_endpoint(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTPS URL without credentials, query, or fragment.")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        raise ValueError(f"{name} cannot target a local host.")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError(f"{name} must use an approved DNS hostname, not an IP address.")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class CloudCadReadConfig:
    mode: CloudCadMode
    tenant_id: str
    secret_reference: str = ""
    token_url: str = ""
    cad_base_url: str = ""
    system_base_url: str = ""
    poll_seconds: int = 30
    reconciliation_overlap_seconds: int = 120
    webhooks_enabled: bool = False

    def __post_init__(self) -> None:
        if not TENANT_IDENTIFIER.fullmatch(self.tenant_id):
            raise ValueError("Cloud CAD configuration requires a stable tenant identifier.")
        if self.webhooks_enabled:
            raise ValueError("Cloud CAD webhook activation is not authorized.")
        if self.mode is CloudCadMode.SYNTHETIC_DISCONNECTED:
            if any((self.secret_reference, self.token_url, self.cad_base_url, self.system_base_url)):
                raise ValueError("Synthetic-disconnected mode cannot contain live CAD configuration.")
            return
        if not self.secret_reference.startswith(CENTRALSQUARE_SECRET_ARN_PREFIX):
            raise ValueError("The reviewed tenant-scoped CentralSquare secret ARN is required.")
        if not 15 <= self.poll_seconds <= 300:
            raise ValueError("Read polling must be between 15 and 300 seconds.")
        if not self.poll_seconds <= self.reconciliation_overlap_seconds <= 900:
            raise ValueError("Reconciliation overlap must cover at least one poll and at most 900 seconds.")
        object.__setattr__(self, "token_url", _https_endpoint(self.token_url, "token_url"))
        object.__setattr__(self, "cad_base_url", _https_endpoint(self.cad_base_url, "cad_base_url"))
        object.__setattr__(self, "system_base_url", _https_endpoint(self.system_base_url, "system_base_url"))

    @property
    def activation_ready(self) -> bool:
        return self.mode is CloudCadMode.CENTRALSQUARE_READ_POLL

    @property
    def data_minimization(self) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType({"calls": CALL_FIELDS, "units": UNIT_FIELDS})

    @property
    def allowed_operations(self) -> tuple[str, ...]:
        return ("authenticate", "health", "search_calls", "get_call", "search_units")

    @property
    def forbidden_operations(self) -> tuple[str, ...]:
        return FORBIDDEN_OPERATIONS

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "CloudCadReadConfig":
        unknown = set(values) - ALLOWED_CONFIGURATION_KEYS
        if unknown:
            raise ValueError("Unknown cloud CAD configuration keys: " + ", ".join(sorted(unknown)))
        return cls(
            mode=CloudCadMode(str(values.get("mode", CloudCadMode.SYNTHETIC_DISCONNECTED))),
            tenant_id=str(values.get("tenant_id", "")),
            secret_reference=str(values.get("secret_reference", "")),
            token_url=str(values.get("token_url", "")),
            cad_base_url=str(values.get("cad_base_url", "")),
            system_base_url=str(values.get("system_base_url", "")),
            poll_seconds=int(values.get("poll_seconds", 30)),
            reconciliation_overlap_seconds=int(values.get("reconciliation_overlap_seconds", 120)),
            webhooks_enabled=bool(values.get("webhooks_enabled", False)),
        )
