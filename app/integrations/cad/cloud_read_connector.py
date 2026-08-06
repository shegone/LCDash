"""Dormant, fail-closed CentralSquare cloud read connector.

This module is intentionally not imported by application startup. It contains
no AWS client, secret implementation, logger, persistence, or default HTTP
transport. Runtime dependencies must be injected after a separate activation.
"""

from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import re
import time
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

from app.integrations.cad.cloud_read_config import (
    CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL,
    CENTRALSQUARE_DOCUMENTED_MAX_PAGE_SIZE,
    CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL,
    CENTRALSQUARE_DOCUMENTED_TOKEN_URL,
    CloudCadMode,
    CloudCadReadConfig,
)


MAX_ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 15.0
_SAFE_FROM = re.compile(r"^[\x20-\x7e]{1,128}$")
_CFS_NUMBER = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True, slots=True)
class CentralSquareCredentials:
    username: str
    password: str

    def __post_init__(self) -> None:
        if not self.username or not self.password:
            raise ValueError("CentralSquare credentials are incomplete.")


class SecretProvider(Protocol):
    def get_credentials(self, secret_reference: str) -> CentralSquareCredentials: ...


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    timeout_seconds: float
    form: Mapping[str, str] | None = None
    json_body: Mapping[str, Any] | None = None
    query: Mapping[str, Any] | None = None


class HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any: ...


class HttpTransport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse: ...


class CloudCadConnectorError(RuntimeError):
    """Structured error containing no endpoint, credential, token, or payload."""

    def __init__(
        self,
        code: str,
        operation: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.operation = operation
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(f"CentralSquare cloud read failed: {code}")

    def to_dict(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {
                "code": self.code,
                "operation": self.operation,
                "status_code": self.status_code,
                "retryable": self.retryable,
            }
        )


class CloudCentralSquareReadConnector:
    """Exact-path cloud read client, disabled unless explicitly constructed enabled."""

    def __init__(
        self,
        config: CloudCadReadConfig,
        *,
        from_header: str,
        secret_provider: SecretProvider,
        transport: HttpTransport,
        enabled: bool = False,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._validate_envelope(config, from_header)
        self._config = config
        self._from_header = from_header
        self._secret_provider = secret_provider
        self._transport = transport
        self._enabled = enabled
        self._sleeper = sleeper
        self._clock = clock
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0

    @staticmethod
    def _validate_envelope(config: CloudCadReadConfig, from_header: str) -> None:
        if config.mode is not CloudCadMode.CENTRALSQUARE_READ_POLL:
            raise ValueError("Cloud connector requires the reviewed read-poll envelope.")
        if (
            config.token_url != CENTRALSQUARE_DOCUMENTED_TOKEN_URL
            or config.cad_base_url != CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL
            or config.system_base_url != CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL
        ):
            raise ValueError("Cloud connector endpoint envelope does not match reviewed v1 endpoints.")
        if config.poll_seconds != 30 or config.reconciliation_overlap_seconds != 120:
            raise ValueError("Cloud connector requires the reviewed 30/120 polling envelope.")
        if config.webhooks_enabled:
            raise ValueError("Cloud connector never permits webhooks.")
        if not _SAFE_FROM.fullmatch(from_header) or "\r" in from_header or "\n" in from_header:
            raise ValueError("Cloud connector requires a safe nonsecret From header.")

    def _require_enabled(self) -> None:
        if not self._enabled:
            raise CloudCadConnectorError("connector_disabled", "startup")

    @staticmethod
    def _retry_delay(attempt: int, response: HttpResponse) -> float:
        if response.status_code == 429:
            try:
                return max(float(response.headers.get("retry-after", "0")), 0.0)
            except (TypeError, ValueError):
                pass
        return 0.25 * (2**attempt)

    def _send_with_backoff(self, operation: str, request: HttpRequest) -> HttpResponse:
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._transport.send(request)
            except Exception as exc:
                if attempt + 1 == MAX_ATTEMPTS:
                    raise CloudCadConnectorError(
                        "transport_unavailable", operation, retryable=True
                    ) from None
                self._sleeper(0.25 * (2**attempt))
                continue

            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and attempt + 1 < MAX_ATTEMPTS:
                self._sleeper(self._retry_delay(attempt, response))
                continue
            if response.status_code >= 400:
                raise CloudCadConnectorError(
                    "upstream_rejected",
                    operation,
                    status_code=response.status_code,
                    retryable=retryable,
                )
            return response
        raise AssertionError("bounded retry loop exhausted")

    def _token(self) -> str:
        if self._access_token and self._clock() < self._access_token_expires_at - 60:
            return self._access_token
        try:
            credentials = self._secret_provider.get_credentials(self._config.secret_reference)
        except Exception as exc:
            raise CloudCadConnectorError(
                "credential_unavailable", "authenticate"
            ) from None
        response = self._send_with_backoff(
            "authenticate",
            HttpRequest(
                method="POST",
                url=CENTRALSQUARE_DOCUMENTED_TOKEN_URL,
                headers={"Accept": "application/json"},
                form={
                    "grant_type": "password",
                    "username": credentials.username,
                    "password": credentials.password,
                },
                timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            ),
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise CloudCadConnectorError(
                "invalid_token_response", "authenticate"
            ) from None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise CloudCadConnectorError("invalid_token_response", "authenticate")
        expires_at = 0.0
        expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
        if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and expires_in > 0:
            expires_at = self._clock() + float(expires_in)
        if not expires_at:
            try:
                encoded = token.split(".")[1]
                encoded += "=" * (-len(encoded) % 4)
                claims = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
                expiry = claims.get("exp")
                if isinstance(expiry, (int, float)) and not isinstance(expiry, bool):
                    expires_at = float(expiry)
            except (IndexError, ValueError, TypeError, json.JSONDecodeError):
                pass
        if expires_at <= self._clock():
            raise CloudCadConnectorError("token_expiry_unavailable", "authenticate")
        self._access_token = token
        self._access_token_expires_at = expires_at
        return token

    def _read_request(
        self,
        *,
        operation: str,
        method: str,
        url: str,
        json_body: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        self._require_enabled()
        response = self._send_with_backoff(
            operation,
            HttpRequest(
                method=method,
                url=url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self._token()}",
                    "From": self._from_header,
                },
                json_body=json_body,
                query=query,
                timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            ),
        )
        try:
            return response.json()
        except Exception as exc:
            raise CloudCadConnectorError("invalid_json_response", operation) from None

    @staticmethod
    def _page(skip: int, limit: int) -> Mapping[str, int]:
        if isinstance(skip, bool) or not isinstance(skip, int) or skip < 0:
            raise ValueError("skip must be a non-negative integer.")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= CENTRALSQUARE_DOCUMENTED_MAX_PAGE_SIZE
        ):
            raise ValueError("limit must be between 1 and 100.")
        return {"skip": skip, "limit": limit}

    def search_calls(self, body: Mapping[str, Any], *, skip: int = 0, limit: int = 100) -> Any:
        return self._read_request(
            operation="search_calls",
            method="POST",
            url=f"{CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL}/cfs_core/search",
            json_body=dict(body),
            query=self._page(skip, limit),
        )

    def get_call(self, cfs_number: str) -> Any:
        if not _CFS_NUMBER.fullmatch(cfs_number):
            raise ValueError("CFS number is not safe for an endpoint path.")
        return self._read_request(
            operation="get_call",
            method="GET",
            url=(
                f"{CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL}/cfs_core/"
                f"{quote(cfs_number, safe='._-')}"
            ),
        )

    def search_units(self, body: Mapping[str, Any], *, skip: int = 0, limit: int = 100) -> Any:
        return self._read_request(
            operation="search_units",
            method="POST",
            url=f"{CENTRALSQUARE_DOCUMENTED_CAD_BASE_URL}/units/search",
            json_body=dict(body),
            query=self._page(skip, limit),
        )

    def get_configurations(self, configuration: str) -> Any:
        if not configuration or len(configuration) > 64 or not configuration.isidentifier():
            raise ValueError("Configuration name is not safe.")
        return self._read_request(
            operation="get_configurations",
            method="GET",
            url=f"{CENTRALSQUARE_DOCUMENTED_SYSTEM_BASE_URL}/configurations",
            query={"configuration": configuration},
        )
