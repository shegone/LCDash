"""Synthetic, network-free contract tests for the CentralSquare adapter."""

import socket
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx

from app.core.tenancy import TenantContext
from app.integrations.cad.base import CadProvider
from app.integrations.cad.centralsquare import (
    CentralSquareCadAdapter,
    legacy_tenant_context,
)
from app.integrations.contracts import (
    CapabilityDenied,
    PageRequest,
    ProviderRateLimit,
    ProviderTimeout,
    TenantBindingError,
)
from app.services.cad_service import simplify_call
from app.services.centralsquare import CentralSquareAPIError


def raw_call(number: str) -> dict:
    return {
        "CFSNumber": number,
        "IncidentCode": [
            {
                "IsPrimary": True,
                "IncidentCode": {
                    "Code": "SYN",
                    "Description": "Synthetic incident",
                },
            }
        ],
        "Address": {
            "Street": "1 Fixture Way",
            "City": "Testville",
            "Latitude": 38.1,
            "Longitude": -81.2,
        },
        "Priority": {"Level": "2"},
        "PrimaryResponseAgency": {"Abbreviation": "SYN"},
        "CallDateTime": "2026-08-04T12:00:00Z",
        "Unit": [
            {
                "UnitNumber": "SYN-12",
                "Status": {"Description": "Assigned"},
            }
        ],
        "CommandLog": [
            {
                "Timestamp": "2026-08-04T12:02:00Z",
                "UnitNumber": "SYN-12",
                "Status": {"Description": "Enroute"},
                "Narrative": "synthetic detail excluded from provider output",
            }
        ],
        "Reporter": {
            "First": "Synthetic",
            "Last": "Caller",
            "PhoneNumber": "555-0100",
        },
    }


def raw_unit(number: str) -> dict:
    return {
        "UnitNumber": number,
        "Agency": {"Abbreviation": "SYN"},
        "UnitType": {"Description": "Medic"},
        "Status": {"Description": "Available"},
        "Station": {"Description": "Synthetic Station"},
    }


class FakeCentralSquareTransport:
    def __init__(self, calls=None, units=None):
        self.calls = list(calls or [])
        self.units = list(units or [])
        self.call_searches = []
        self.unit_searches = []
        self.failure = None

    def _fail_if_configured(self):
        if self.failure is not None:
            raise self.failure

    def get_system_config(self, configuration):
        self._fail_if_configured()
        return {configuration: [{"Description": "Synthetic"}]}

    def search_cfs_core(self, search_body, skip=0, limit=100):
        self._fail_if_configured()
        self.call_searches.append((dict(search_body), skip, limit))
        items = self.calls[skip : skip + limit]
        return {
            "cfs_cores": items,
            "next": "synthetic-next" if skip + len(items) < len(self.calls) else None,
        }

    def search_units(self, search_body=None, skip=0, limit=100):
        self._fail_if_configured()
        self.unit_searches.append((dict(search_body or {}), skip, limit))
        items = self.units[skip : skip + limit]
        return {
            "Units": items,
            "next": "synthetic-next" if skip + len(items) < len(self.units) else None,
        }

    def get_cfs_core(self, cfs_number):
        self._fail_if_configured()
        return next(item for item in self.calls if item["CFSNumber"] == cfs_number)

    def get_cfs_analytics(self, cfs_number):
        self._fail_if_configured()
        return {"CFSNumber": cfs_number, "CallTimes": []}


class CentralSquareAdapterTests(unittest.TestCase):
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
            patch("httpx.put", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.stream", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.Client", side_effect=AssertionError("HTTP access blocked")),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_network(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_adapter_satisfies_protocol_and_matches_inherited_normalization(self):
        context = legacy_tenant_context("adapter-normalization")
        raw = raw_call("SYN-0001")
        inherited = simplify_call(raw)
        adapter = CentralSquareCadAdapter(
            FakeCentralSquareTransport(calls=[raw]),
            tenant=context,
        )

        normalized = adapter.get_call(context, "SYN-0001", timeout_ms=100)

        self.assertIsInstance(adapter, CadProvider)
        self.assertEqual(normalized.cfs_number, inherited["cfs_number"])
        self.assertEqual(normalized.incident_description, inherited["incident_description"])
        self.assertEqual(normalized.location, inherited["location"])
        self.assertEqual(normalized.status, inherited["status"])
        self.assertEqual(normalized.assigned_units, ("SYN-12",))
        self.assertFalse(hasattr(normalized, "raw"))
        self.assertFalse(hasattr(normalized, "reporter"))
        self.assert_no_network()

    def test_call_and_unit_search_paginate_with_stable_offsets(self):
        context = legacy_tenant_context("adapter-pagination")
        transport = FakeCentralSquareTransport(
            calls=[raw_call(f"SYN-{index:04d}") for index in range(1, 4)],
            units=[raw_unit(f"SYN-{index}") for index in range(10, 13)],
        )
        adapter = CentralSquareCadAdapter(transport, tenant=context)

        call_page_1 = adapter.search_calls(
            context,
            {"CurrentlyActive": True},
            PageRequest(limit=2),
            timeout_ms=100,
        )
        call_page_2 = adapter.search_calls(
            context,
            {"CurrentlyActive": True},
            PageRequest(limit=2, cursor=call_page_1.next_cursor),
            timeout_ms=100,
        )
        unit_page = adapter.search_units(
            context,
            {},
            PageRequest(limit=2),
            timeout_ms=100,
        )

        self.assertEqual([item.cfs_number for item in call_page_1.items], ["SYN-0001", "SYN-0002"])
        self.assertEqual([item.cfs_number for item in call_page_2.items], ["SYN-0003"])
        self.assertEqual(call_page_1.next_cursor, "2")
        self.assertIsNone(call_page_2.next_cursor)
        self.assertEqual([item.unit_number for item in unit_page.items], ["SYN-10", "SYN-11"])
        self.assertEqual(transport.call_searches, [({"CurrentlyActive": True}, 0, 2), ({"CurrentlyActive": True}, 2, 2)])
        self.assertEqual(transport.unit_searches, [({}, 0, 2)])
        self.assert_no_network()

    def test_legacy_raw_shim_preserves_call_signatures_and_payload_shapes(self):
        raw = raw_call("SYN-0001")
        transport = FakeCentralSquareTransport(calls=[raw], units=[raw_unit("SYN-10")])
        adapter = CentralSquareCadAdapter(transport)

        calls = adapter.search_cfs_core({"CurrentlyActive": True}, skip=0, limit=25)
        units = adapter.search_units({}, skip=0, limit=25)
        positional_units = adapter.search_units({}, 0, 25)
        detail = adapter.get_cfs_core("SYN-0001")
        analytics = adapter.get_cfs_analytics("SYN-0001")

        self.assertIs(calls["cfs_cores"][0], raw)
        self.assertEqual(units["Units"][0]["UnitNumber"], "SYN-10")
        self.assertEqual(positional_units["Units"][0]["UnitNumber"], "SYN-10")
        self.assertIs(detail, raw)
        self.assertEqual(analytics, {"CFSNumber": "SYN-0001", "CallTimes": []})
        self.assert_no_network()

    def test_timeout_and_rate_limit_errors_are_sanitized_and_audited(self):
        context = legacy_tenant_context("adapter-errors")
        transport = FakeCentralSquareTransport(calls=[raw_call("SYN-0001")])
        adapter = CentralSquareCadAdapter(transport, tenant=context)

        transport.failure = CentralSquareAPIError("synthetic timeout")
        transport.failure.__cause__ = httpx.ReadTimeout("synthetic transport timeout")
        with self.assertRaises(ProviderTimeout):
            adapter.search_calls(context, {}, PageRequest(), timeout_ms=100)

        request = httpx.Request("POST", "https://synthetic.invalid/cfs_core/search")
        response = httpx.Response(429, request=request, headers={"retry-after": "7"})
        status_error = httpx.HTTPStatusError("synthetic rate limit", request=request, response=response)
        transport.failure = CentralSquareAPIError("synthetic rate limit")
        transport.failure.__cause__ = status_error
        with self.assertRaises(ProviderRateLimit) as caught:
            adapter.search_calls(context, {}, PageRequest(), timeout_ms=100)

        self.assertEqual(caught.exception.retry_after_seconds, 7)
        self.assertEqual([item.outcome for item in adapter.audit_events], ["timeout", "rate_limited"])
        self.assertTrue(all("synthetic timeout" not in repr(item) for item in adapter.audit_events))
        self.assert_no_network()

    def test_tenant_binding_and_operational_capabilities_deny_by_default(self):
        context = legacy_tenant_context("adapter-boundary")
        other = TenantContext(
            tenant_id="other-county",
            subject="synthetic-user",
            identity_source="synthetic-federation",
            roles=frozenset({"cad-read"}),
            request_id="other-request",
            authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
        adapter = CentralSquareCadAdapter(FakeCentralSquareTransport(), tenant=context)

        with self.assertRaises(TenantBindingError):
            adapter.search_calls(other, {}, PageRequest(), timeout_ms=100)
        with self.assertRaises(CapabilityDenied):
            adapter.register_subscription(context, "synthetic://callback", timeout_ms=100)
        with self.assertRaises(CapabilityDenied):
            adapter.update_call(context, "SYN-0001", {}, timeout_ms=100)
        with self.assertRaises(CapabilityDenied):
            adapter.send_message(context, "SYN-10", "synthetic", timeout_ms=100)
        with self.assertRaises(CapabilityDenied):
            adapter.acknowledge(context, "SYN-0001", timeout_ms=100)

        self.assertEqual(
            [event.detail for event in adapter.audit_events],
            ["tenant_binding", "capability", "capability", "capability", "capability"],
        )
        self.assert_no_network()

    def test_read_consumers_use_adapter_but_operational_command_transport_stays_separate(self):
        repository = Path(__file__).parents[2]
        read_paths = (
            "app/main.py",
            "app/services/analytics_collector.py",
            "app/services/cad_service.py",
            "app/services/county_commission_report_service.py",
            "app/services/heatmap_service.py",
            "app/services/mae_service.py",
            "app/services/operations_service.py",
            "app/services/station_alert_service.py",
            "app/services/unit_service.py",
            "scripts/backfill_dispatcher_names.py",
            "scripts/inspect_subscription_agencies.py",
        )
        for relative_path in read_paths:
            source = (repository / relative_path).read_text(encoding="utf-8")
            self.assertIn("CentralSquareCadAdapter as CentralSquareClient", source)

        operational_source = (
            repository / "app/services/ems_delay_alert_service.py"
        ).read_text(encoding="utf-8")
        self.assertIn("app.services.centralsquare import", operational_source)
        self.assertNotIn("CentralSquareCadAdapter", operational_source)
        subscription_source = (
            repository / "scripts/register_centralsquare_subscriptions.py"
        ).read_text(encoding="utf-8")
        self.assertIn("app.services.centralsquare import CentralSquareClient", subscription_source)
        self.assertNotIn("CentralSquareCadAdapter", subscription_source)
        self.assertFalse(hasattr(CentralSquareCadAdapter, "run_command"))
        self.assert_no_network()


if __name__ == "__main__":
    unittest.main()
