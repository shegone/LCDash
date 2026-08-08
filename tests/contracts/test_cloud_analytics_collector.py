"""Contracts for the scheduled cloud analytics collector.

The collector is the only cloud component that both reads CAD and writes the
analytics warehouse, so these assert the read-only shape of its CAD adapter and
that it refuses to run when cloud CAD access is disabled.
"""

import unittest
from unittest.mock import patch

from app.integrations.cad.cloud_read_config import CloudCadReadConfig
from app.tools.cloud_analytics_collector import CollectorReadOnlyCadClient, collect


class _FakeConnector:
    def __init__(self):
        self.searches = []
        self.analytics = []

    def search_calls(self, body, *, skip=0, limit=100):
        self.searches.append((dict(body), skip, limit))
        return {"cfs_cores": []}

    def get_cfs_analytics(self, cfs_number):
        self.analytics.append(cfs_number)
        return {"CFSNumber": cfs_number}


class AdapterShapeTests(unittest.TestCase):
    def test_adapter_exposes_only_the_two_read_methods(self):
        public = {
            name
            for name in dir(CollectorReadOnlyCadClient)
            if not name.startswith("_")
        }
        self.assertEqual(public, {"search_cfs_core", "get_cfs_analytics"})

    def test_no_write_or_command_method_exists(self):
        for forbidden in (
            "run_command",
            "put",
            "post",
            "dispatch",
            "acknowledge",
            "update_call",
            "send_message",
        ):
            self.assertFalse(
                hasattr(CollectorReadOnlyCadClient(_FakeConnector()), forbidden),
                f"{forbidden} must not be reachable from the collector adapter",
            )

    def test_search_is_forwarded_with_paging(self):
        connector = _FakeConnector()
        client = CollectorReadOnlyCadClient(connector)
        client.search_cfs_core({"ClosedFrom": "x"}, skip=100, limit=50)
        self.assertEqual(connector.searches, [({"ClosedFrom": "x"}, 100, 50)])

    def test_analytics_lookup_is_forwarded(self):
        connector = _FakeConnector()
        CollectorReadOnlyCadClient(connector).get_cfs_analytics("CFS26-00001")
        self.assertEqual(connector.analytics, ["CFS26-00001"])


class AllowlistTests(unittest.TestCase):
    def test_get_cfs_analytics_is_allowlisted_and_writes_are_not(self):
        config = CloudCadReadConfig.from_mapping(
            {"mode": "synthetic-disconnected", "tenant_id": "logan-synthetic"}
        )
        self.assertIn("get_cfs_analytics", config.allowed_operations)
        for forbidden in config.forbidden_operations:
            self.assertNotIn(forbidden, config.allowed_operations)


class DisabledAccessTests(unittest.TestCase):
    def test_collector_refuses_to_run_without_cloud_cad_access(self):
        with patch(
            "app.tools.cloud_analytics_collector.build_cloud_cad_connector",
            return_value=None,
        ):
            with self.assertRaises(RuntimeError):
                collect()


if __name__ == "__main__":
    unittest.main()
