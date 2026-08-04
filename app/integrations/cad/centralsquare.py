"""CentralSquare read adapter for the version 1 CAD provider contract.

The inherited HTTP/OAuth client remains the compatibility transport so its
configuration and Docker behavior do not change. Operational write methods are
not exposed through the legacy compatibility surface and provider operations
deny unless an explicit capability is configured.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, overload

import httpx

from app.core.tenancy import TenantContext
from app.integrations.contracts import (
    PROVIDER_CONTRACT_VERSION,
    AuditEvent,
    CadCapability,
    CapabilityDenied,
    NormalizedCall,
    NormalizedEvent,
    NormalizedUnit,
    Page,
    PageRequest,
    ProviderError,
    ProviderHealth,
    ProviderRateLimit,
    ProviderTimeout,
    TenantBindingError,
)
from app.services.centralsquare import CentralSquareAPIError, CentralSquareClient


DEFAULT_CENTRALSQUARE_TIMEOUT_MS = 30_000
LEGACY_CENTRALSQUARE_SECRET_REFERENCE = "legacy-config://centralsquare"


class CentralSquareReadTransport(Protocol):
    """Inherited raw read surface required by the adapter."""

    def get_system_config(self, configuration: str) -> dict: ...
    def search_cfs_core(self, search_body: dict, skip: int = 0, limit: int = 100) -> dict: ...
    def search_units(self, search_body: dict | None = None, skip: int = 0, limit: int = 100) -> dict: ...
    def get_cfs_core(self, cfs_number: str) -> dict: ...
    def get_cfs_analytics(self, cfs_number: str) -> dict: ...


def legacy_tenant_context(request_id: str = "legacy-server-request") -> TenantContext:
    """Build the trusted single-county binding used by inherited call sites."""

    return TenantContext(
        tenant_id="logan-county",
        subject="lcdash-server",
        identity_source="legacy-server-binding",
        roles=frozenset({"cad-read"}),
        request_id=request_id,
        authenticated_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
    )


class CentralSquareCadAdapter:
    """Read-only CentralSquare provider plus a behavior-preserving raw shim."""

    contract_version = PROVIDER_CONTRACT_VERSION
    capabilities = frozenset(
        {
            CadCapability.AUTHENTICATE,
            CadCapability.HEALTH,
            CadCapability.SEARCH_CALLS,
            CadCapability.GET_CALL,
            CadCapability.SEARCH_UNITS,
            CadCapability.NORMALIZE_EVENT,
        }
    )

    def __init__(
        self,
        transport: CentralSquareReadTransport | None = None,
        *,
        tenant: TenantContext | None = None,
        capabilities: frozenset[CadCapability] | None = None,
    ) -> None:
        self._transport = transport or CentralSquareClient()
        self._tenant = tenant or legacy_tenant_context()
        self.capabilities = (
            type(self).capabilities
            if capabilities is None
            else frozenset(capabilities)
        )
        self._audit: list[AuditEvent] = []

    @property
    def audit_events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._audit)

    def _record(
        self,
        context: TenantContext,
        operation: str,
        outcome: str,
        detail: str = "",
    ) -> None:
        self._audit.append(
            AuditEvent(
                tenant_id=self._tenant.tenant_id,
                provider=type(self).__name__,
                operation=operation,
                outcome=outcome,
                request_id=context.request_id,
                detail=detail,
            )
        )

    def _guard(
        self,
        context: TenantContext,
        operation: str,
        capability: CadCapability,
        timeout_ms: int,
    ) -> None:
        if context.tenant_id != self._tenant.tenant_id:
            self._record(context, operation, "denied", "tenant_binding")
            raise TenantBindingError("CAD provider is bound to a different tenant.")
        if capability not in self.capabilities:
            self._record(context, operation, "denied", "capability")
            raise CapabilityDenied(f"Capability denied: {capability.value}")
        if timeout_ms <= 0:
            self._record(context, operation, "timeout")
            raise ProviderTimeout("CentralSquare provider deadline exceeded.")

    def _translate_error(
        self,
        context: TenantContext,
        operation: str,
        error: BaseException,
    ) -> ProviderError:
        current: BaseException | None = error
        while current is not None:
            if isinstance(current, httpx.TimeoutException | TimeoutError):
                self._record(context, operation, "timeout")
                return ProviderTimeout("CentralSquare provider deadline exceeded.")
            if isinstance(current, httpx.HTTPStatusError) and current.response.status_code == 429:
                retry_header = current.response.headers.get("retry-after", "1")
                try:
                    retry_after = max(int(retry_header), 1)
                except ValueError:
                    retry_after = 1
                self._record(context, operation, "rate_limited")
                return ProviderRateLimit(retry_after)
            current = current.__cause__
        self._record(context, operation, "error")
        return ProviderError("CentralSquare provider request failed.")

    def _provider_call(
        self,
        context: TenantContext,
        operation: str,
        callback,
    ) -> Any:
        try:
            result = callback()
        except (CentralSquareAPIError, httpx.HTTPError, TimeoutError) as exc:
            raise self._translate_error(context, operation, exc) from exc
        self._record(context, operation, "success")
        return result

    @staticmethod
    def _next_cursor(result: Mapping[str, Any], start: int, count: int) -> str | None:
        return str(start + count) if result.get("next") else None

    @staticmethod
    def _cursor_offset(page: PageRequest) -> int:
        try:
            offset = int(page.cursor or "0")
        except ValueError as exc:
            raise ValueError("CentralSquare cursor must be an integer offset.") from exc
        if offset < 0:
            raise ValueError("CentralSquare cursor cannot be negative.")
        return offset

    @staticmethod
    def _normalize_call(context: TenantContext, raw_call: Mapping[str, Any]) -> NormalizedCall:
        # Local import avoids a module cycle while reusing the accepted inherited
        # normalizer byte-for-byte for behavioral parity.
        from app.services.cad_service import simplify_call

        normalized = simplify_call(dict(raw_call))
        return NormalizedCall(
            tenant_id=context.tenant_id,
            cfs_number=normalized["cfs_number"],
            incident_code=normalized["incident_code"],
            incident_description=normalized["incident_description"],
            priority=normalized["priority"],
            agency=normalized["agency"],
            status=normalized["status"],
            call_datetime=normalized["call_datetime"],
            location=normalized["location"],
            assigned_units=tuple(
                item["unit_number"]
                for item in normalized["assigned_units"]
                if item.get("unit_number")
            ),
        )

    @staticmethod
    def _normalize_unit(context: TenantContext, raw_unit: Mapping[str, Any]) -> NormalizedUnit:
        from app.services.unit_service import normalize_unit

        normalized = normalize_unit(dict(raw_unit))
        return NormalizedUnit(
            tenant_id=context.tenant_id,
            unit_number=normalized["unit_number"],
            agency=normalized["agency"],
            unit_type=normalized["unit_type"],
            status=normalized["status"],
            station=normalized["station"],
        )

    def health(self, context: TenantContext, *, timeout_ms: int) -> ProviderHealth:
        self._guard(context, "health", CadCapability.HEALTH, timeout_ms)
        self._record(context, "health", "success")
        return ProviderHealth(True, "centralsquare")

    def authenticate(
        self,
        context: TenantContext,
        secret_reference: str,
        *,
        timeout_ms: int,
    ) -> ProviderHealth:
        self._guard(context, "authenticate", CadCapability.AUTHENTICATE, timeout_ms)
        if secret_reference != LEGACY_CENTRALSQUARE_SECRET_REFERENCE:
            self._record(context, "authenticate", "denied", "secret_reference")
            raise CapabilityDenied("CentralSquare secret reference is not authorized.")
        self._record(context, "authenticate", "success")
        return ProviderHealth(True, "centralsquare")

    def search_calls(
        self,
        context: TenantContext,
        query: Mapping[str, Any],
        page: PageRequest,
        *,
        timeout_ms: int,
    ) -> Page:
        self._guard(context, "search_calls", CadCapability.SEARCH_CALLS, timeout_ms)
        offset = self._cursor_offset(page)
        result = self._provider_call(
            context,
            "search_calls",
            lambda: self._transport.search_cfs_core(
                dict(query), skip=offset, limit=page.limit
            ),
        )
        raw_items = tuple(result.get("cfs_cores") or ())
        return Page(
            items=tuple(self._normalize_call(context, item) for item in raw_items),
            next_cursor=self._next_cursor(result, offset, len(raw_items)),
        )

    def get_call(
        self,
        context: TenantContext,
        cfs_number: str,
        *,
        timeout_ms: int,
    ) -> NormalizedCall:
        self._guard(context, "get_call", CadCapability.GET_CALL, timeout_ms)
        raw_call = self._provider_call(
            context,
            "get_call",
            lambda: self._transport.get_cfs_core(cfs_number),
        )
        return self._normalize_call(context, raw_call)

    def _search_units_provider(
        self,
        context: TenantContext,
        query: Mapping[str, Any],
        page: PageRequest,
        *,
        timeout_ms: int,
    ) -> Page:
        self._guard(context, "search_units", CadCapability.SEARCH_UNITS, timeout_ms)
        offset = self._cursor_offset(page)
        result = self._provider_call(
            context,
            "search_units",
            lambda: self._transport.search_units(
                dict(query), skip=offset, limit=page.limit
            ),
        )
        raw_items = tuple(result.get("Units") or result.get("units") or ())
        return Page(
            items=tuple(self._normalize_unit(context, item) for item in raw_items),
            next_cursor=self._next_cursor(result, offset, len(raw_items)),
        )

    def normalize_event(
        self,
        context: TenantContext,
        source: str,
        payload: Mapping[str, Any],
    ) -> NormalizedEvent:
        self._guard(
            context,
            "normalize_event",
            CadCapability.NORMALIZE_EVENT,
            DEFAULT_CENTRALSQUARE_TIMEOUT_MS,
        )
        event = NormalizedEvent(
            tenant_id=context.tenant_id,
            event_id=str(payload.get("event_id") or payload.get("EventId") or ""),
            event_type=str(payload.get("event_type") or payload.get("EventType") or source),
            occurred_at=str(payload.get("occurred_at") or payload.get("Timestamp") or ""),
            resource_id=str(payload.get("resource_id") or payload.get("CFSNumber") or ""),
        )
        self._record(context, "normalize_event", "success")
        return event

    def ingest_event(
        self,
        context: TenantContext,
        event: NormalizedEvent,
        *,
        timeout_ms: int,
    ) -> None:
        self._guard(context, "ingest_event", CadCapability.INGEST_EVENTS, timeout_ms)

    def register_subscription(
        self,
        context: TenantContext,
        callback_reference: str,
        *,
        timeout_ms: int,
    ) -> None:
        self._guard(
            context,
            "register_subscription",
            CadCapability.REGISTER_SUBSCRIPTION,
            timeout_ms,
        )

    def update_call(
        self,
        context: TenantContext,
        cfs_number: str,
        changes: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> None:
        self._guard(context, "update_call", CadCapability.UPDATE_CALL, timeout_ms)

    def send_message(
        self,
        context: TenantContext,
        destination: str,
        message: str,
        *,
        timeout_ms: int,
    ) -> None:
        self._guard(context, "send_message", CadCapability.SEND_MESSAGE, timeout_ms)

    def acknowledge(
        self,
        context: TenantContext,
        resource_id: str,
        *,
        timeout_ms: int,
    ) -> None:
        self._guard(context, "acknowledge", CadCapability.ACKNOWLEDGE, timeout_ms)

    # Raw compatibility methods preserve inherited call signatures and response
    # shapes while placing all read consumers behind this adapter.
    def get_system_config(self, configuration: str) -> dict:
        return self._transport.get_system_config(configuration)

    def search_cfs_core(
        self,
        search_body: dict,
        skip: int = 0,
        limit: int = 100,
    ) -> dict:
        return self._transport.search_cfs_core(search_body, skip=skip, limit=limit)

    @overload
    def search_units(
        self,
        context_or_body: TenantContext,
        query: Mapping[str, Any],
        page: PageRequest,
        *,
        timeout_ms: int,
    ) -> Page: ...

    @overload
    def search_units(
        self,
        context_or_body: dict | None = None,
        query: int | None = None,
        page: int | None = None,
        *,
        timeout_ms: None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> dict: ...

    def search_units(
        self,
        context_or_body: TenantContext | dict | None = None,
        query: Mapping[str, Any] | int | None = None,
        page: PageRequest | int | None = None,
        *,
        timeout_ms: int | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Page | dict:
        if isinstance(context_or_body, TenantContext):
            if not isinstance(page, PageRequest) or timeout_ms is None:
                raise TypeError("Provider unit search requires page and timeout_ms.")
            return self._search_units_provider(
                context_or_body,
                query if isinstance(query, Mapping) else {},
                page,
                timeout_ms=timeout_ms,
            )
        legacy_skip = query if isinstance(query, int) else skip
        legacy_limit = page if isinstance(page, int) else limit
        return self._transport.search_units(
            context_or_body,
            skip=legacy_skip,
            limit=legacy_limit,
        )

    def get_cfs_core(self, cfs_number: str) -> dict:
        return self._transport.get_cfs_core(cfs_number)

    def get_cfs_analytics(self, cfs_number: str) -> dict:
        return self._transport.get_cfs_analytics(cfs_number)
