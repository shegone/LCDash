"""Pure synthetic tenant-isolation contract for representative local boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from app.core.tenancy import CountyProfile, TenantContext, TENANCY_CONTRACT_VERSION


_RESOURCE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,120}$")


class TenantScope(StrEnum):
    RECORD = "record"
    FILE = "file"
    QUEUE = "queue"
    CACHE = "cache"


class TenantIsolationDenied(PermissionError):
    """Sanitized denial for an invalid tenant or scoped resource boundary."""


@dataclass(frozen=True, slots=True)
class SyntheticTenantResource:
    tenant_id: str
    scope: TenantScope
    resource_id: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, TenantScope):
            raise ValueError("Synthetic resource scope must be known.")
        if not _RESOURCE_ID.fullmatch(self.resource_id) or ".." in self.resource_id:
            raise ValueError("Synthetic resource ID is unsafe.")


@dataclass(frozen=True, slots=True, init=False)
class SyntheticTenantIsolation:
    """Bound, immutable-view helper for synthetic tenant-scoped resources."""

    _context: TenantContext
    _profile: CountyProfile
    _resources: tuple[SyntheticTenantResource, ...]

    def __init__(
        self,
        context: TenantContext,
        profile: CountyProfile,
        resources: Iterable[SyntheticTenantResource],
    ) -> None:
        if not isinstance(context, TenantContext) or not isinstance(profile, CountyProfile):
            raise TenantIsolationDenied("Trusted tenant context and county profile are required.")
        if (
            context.contract_version != TENANCY_CONTRACT_VERSION
            or profile.contract_version != TENANCY_CONTRACT_VERSION
        ):
            raise TenantIsolationDenied("Tenant isolation contract mismatch.")
        if context.tenant_id != profile.tenant_id:
            raise TenantIsolationDenied("Tenant and county profile binding mismatch.")

        resource_tuple = tuple(resources)
        if any(not isinstance(item, SyntheticTenantResource) for item in resource_tuple):
            raise TenantIsolationDenied("Synthetic tenant resources are required.")
        object.__setattr__(self, "_context", context)
        object.__setattr__(self, "_profile", profile)
        object.__setattr__(self, "_resources", resource_tuple)

    @staticmethod
    def _scope(scope: TenantScope | str) -> TenantScope:
        try:
            return TenantScope(str(scope))
        except ValueError as exc:
            raise TenantIsolationDenied("Unknown tenant resource scope.") from exc

    def list(self, scope: TenantScope | str) -> tuple[SyntheticTenantResource, ...]:
        selected_scope = self._scope(scope)
        return tuple(
            item
            for item in self._resources
            if item.tenant_id == self._context.tenant_id
            and item.scope is selected_scope
        )

    def read(
        self,
        scope: TenantScope | str,
        resource_id: str,
    ) -> SyntheticTenantResource:
        selected_scope = self._scope(scope)
        for item in self.list(selected_scope):
            if item.resource_id == resource_id:
                return item
        raise TenantIsolationDenied("Tenant-scoped resource is not available.")

    def file_path(self, root: Path, resource_id: str) -> Path:
        resource = self.read(TenantScope.FILE, resource_id)
        tenant_root = (Path(root).resolve() / self._context.tenant_id).resolve()
        candidate = (tenant_root / resource.resource_id).resolve()
        if tenant_root not in candidate.parents:
            raise TenantIsolationDenied("Tenant file path escaped its boundary.")
        return candidate

    def queue_key(self, resource_id: str) -> str:
        resource = self.read(TenantScope.QUEUE, resource_id)
        return f"tenant/{self._context.tenant_id}/queue/{resource.resource_id}"

    def cache_key(self, resource_id: str) -> str:
        resource = self.read(TenantScope.CACHE, resource_id)
        return f"tenant/{self._context.tenant_id}/cache/{resource.resource_id}"
