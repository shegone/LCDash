"""Offline contracts for optional trusted context propagation within MAE analytics."""

import socket
import sys
import types
import unittest
from datetime import datetime, timezone
from importlib.util import find_spec
from unittest.mock import Mock, patch

from app.core.tenancy import TenantContext

if "psycopg" not in sys.modules and find_spec("psycopg") is None:
    psycopg_stub = types.ModuleType("psycopg")
    psycopg_stub.Error = type("Error", (Exception,), {})
    psycopg_stub.connect = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("database access blocked")
    )
    sys.modules["psycopg"] = psycopg_stub

from app.services.mae_service import ask_mae


def trusted_context() -> TenantContext:
    return TenantContext(
        tenant_id="logan-synthetic",
        subject="synthetic-supervisor",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id="synthetic-mae-analytics",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class MAEAnalyticsContextPropagationTests(unittest.TestCase):
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
            patch(
                "app.services.analytics_database.AnalyticsRepository",
                side_effect=AssertionError("database access blocked"),
            ),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def _model_response(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "message": {"content": "There were 42 calls."}
        }
        return response

    def _analytics_snapshot(self):
        return {
            "available": True,
            "period_label": "Last 7 days",
            "latest_data_at": "2026-08-04T10:00:00-04:00",
            "metrics": {"total_calls": 42},
        }

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_analytics_overview")
    def test_exact_trusted_context_reaches_analytics(self, analytics_mock, post_mock):
        context = trusted_context()
        analytics_mock.return_value = self._analytics_snapshot()
        post_mock.return_value = self._model_response()

        result = ask_mae(
            "How many calls were there last week?",
            tenant_context=context,
        )

        analytics_mock.assert_called_once_with(
            period="7d",
            tenant_context=context,
        )
        self.assertIs(
            analytics_mock.call_args.kwargs["tenant_context"],
            context,
        )
        self.assertEqual(result["answer"], "There were 42 calls.")
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    @patch("app.services.mae_service.httpx.post")
    @patch("app.services.mae_service.get_analytics_overview")
    def test_no_context_retains_legacy_output_and_none_context(
        self,
        analytics_mock,
        post_mock,
    ):
        analytics_mock.return_value = self._analytics_snapshot()
        post_mock.return_value = self._model_response()

        result = ask_mae("How many calls were there last week?")

        analytics_mock.assert_called_once_with(period="7d")
        self.assertEqual(result["answer"], "There were 42 calls.")
        self.assertEqual(result["sources"][0]["kind"], "historical")
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
