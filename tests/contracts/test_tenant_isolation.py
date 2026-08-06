"""Offline synthetic tests for representative tenant isolation boundaries."""

import socket
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.core.county_profiles import load_builtin_county_profile
from app.core.tenancy import TenantContext
from app.core.tenant_isolation import (
    SyntheticTenantIsolation,
    SyntheticTenantResource,
    TenantIsolationDenied,
    TenantScope,
)


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-isolation",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


def resource(tenant_id: str, scope: TenantScope, resource_id: str) -> SyntheticTenantResource:
    return SyntheticTenantResource(
        tenant_id=tenant_id,
        scope=scope,
        resource_id=resource_id,
        value=f"synthetic:{tenant_id}:{scope.value}:{resource_id}",
    )


class TenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.blockers = [
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("network access blocked"),
            ),
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network access blocked"),
            ),
            patch("httpx.get", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.post", side_effect=AssertionError("HTTP access blocked")),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)
        self.resources = tuple(
            resource(tenant, scope, resource_id)
            for tenant, prefix in (
                ("logan-synthetic", "logan"),
                ("northstar-fictional", "northstar"),
            )
            for scope, suffix in (
                (TenantScope.RECORD, "record"),
                (TenantScope.FILE, "file.json"),
                (TenantScope.QUEUE, "job"),
                (TenantScope.CACHE, "snapshot"),
            )
            for resource_id in (f"{prefix}-{suffix}",)
        )
        self.logan = SyntheticTenantIsolation(
            trusted_context("logan-synthetic"),
            load_builtin_county_profile("logan-synthetic"),
            self.resources,
        )

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_service_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_record_read_and_list_exclude_northstar(self):
        listed = self.logan.list(TenantScope.RECORD)

        self.assertEqual([item.resource_id for item in listed], ["logan-record"])
        self.assertEqual(
            self.logan.read(TenantScope.RECORD, "logan-record").tenant_id,
            "logan-synthetic",
        )
        with self.assertRaises(TenantIsolationDenied):
            self.logan.read(TenantScope.RECORD, "northstar-record")
        self.assert_no_service_used()

    def test_file_path_cannot_be_derived_for_northstar(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            derived = self.logan.file_path(Path(temporary_directory), "logan-file.json")
            with self.assertRaises(TenantIsolationDenied):
                self.logan.file_path(Path(temporary_directory), "northstar-file.json")

        self.assertEqual(derived.parent.name, "logan-synthetic")
        self.assertEqual(derived.name, "logan-file.json")
        self.assert_no_service_used()

    def test_queue_and_cache_keys_cannot_be_derived_for_northstar(self):
        self.assertEqual(
            self.logan.queue_key("logan-job"),
            "tenant/logan-synthetic/queue/logan-job",
        )
        self.assertEqual(
            self.logan.cache_key("logan-snapshot"),
            "tenant/logan-synthetic/cache/logan-snapshot",
        )
        for method, resource_id in (
            (self.logan.queue_key, "northstar-job"),
            (self.logan.cache_key, "northstar-snapshot"),
        ):
            with self.assertRaises(TenantIsolationDenied):
                method(resource_id)
        self.assert_no_service_used()

    def test_mismatched_context_profile_and_unknown_scope_fail_closed(self):
        with self.assertRaisesRegex(TenantIsolationDenied, "binding mismatch"):
            SyntheticTenantIsolation(
                trusted_context("logan-synthetic"),
                load_builtin_county_profile("northstar-fictional"),
                self.resources,
            )
        with self.assertRaisesRegex(TenantIsolationDenied, "Unknown"):
            self.logan.list("database")
        with self.assertRaises(FrozenInstanceError):
            self.logan._context = trusted_context("northstar-fictional")
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
