"""Import-safe route wiring checks for the analytics overview API."""

import ast
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


MAIN_PATH = Path(__file__).parents[2] / "app" / "main.py"


class AnalyticsOverviewRouteWiringTests(unittest.TestCase):
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
        self.tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        self.handler = next(
            node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "analytics_overview_api"
        )

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_service_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_handler_uses_trusted_dependency_and_forwards_exact_context(self):
        argument_names = [argument.arg for argument in self.handler.args.args]
        self.assertEqual(
            argument_names,
            ["response", "period", "start", "end", "tenant_context"],
        )
        context_argument = self.handler.args.args[-1]
        annotation = ast.unparse(context_argument.annotation)
        self.assertIn("Depends(get_trusted_tenant_context)", annotation)
        self.assertEqual(ast.literal_eval(self.handler.args.defaults[-1]), None)

        overview_calls = [
            node
            for node in ast.walk(self.handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_analytics_overview"
        ]
        self.assertEqual(len(overview_calls), 1)
        forwarded = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in overview_calls[0].keywords
        }
        self.assertEqual(forwarded["tenant_context"], "tenant_context")
        self.assertNotIn("tenant_id", argument_names)
        self.assertNotIn("county_profile", argument_names)
        self.assert_no_service_used()

    def test_post_report_uses_trusted_dependency_and_forwards_exact_context(self):
        handler = next(
            node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "mae_analytics_report_api"
        )
        argument_names = [argument.arg for argument in handler.args.args]
        self.assertEqual(argument_names, ["report_request", "tenant_context"])
        annotation = ast.unparse(handler.args.args[-1].annotation)
        self.assertIn("Depends(get_trusted_tenant_context)", annotation)
        self.assertEqual(ast.literal_eval(handler.args.defaults[-1]), None)

        overview_calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_analytics_overview"
        ]
        self.assertEqual(len(overview_calls), 1)
        forwarded = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in overview_calls[0].keywords
        }
        self.assertEqual(forwarded["tenant_context"], "tenant_context")
        self.assertNotIn("tenant_id", argument_names)
        self.assertNotIn("county_profile", argument_names)
        self.assert_no_service_used()

    def test_analytics_page_forwards_exact_context_to_primary_and_fallback(self):
        handler = next(
            node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "analytics_page"
        )
        argument_names = [argument.arg for argument in handler.args.args]
        self.assertEqual(
            argument_names,
            ["request", "period", "start", "end", "tenant_context"],
        )
        annotation = ast.unparse(handler.args.args[-1].annotation)
        self.assertIn("Depends(get_trusted_tenant_context)", annotation)
        self.assertEqual(ast.literal_eval(handler.args.defaults[-1]), None)

        overview_calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "get_analytics_overview"
        ]
        self.assertEqual(len(overview_calls), 2)
        for call in overview_calls:
            forwarded = {
                keyword.arg: ast.unparse(keyword.value)
                for keyword in call.keywords
            }
            self.assertEqual(forwarded["tenant_context"], "tenant_context")
        self.assertNotIn("tenant_id", argument_names)
        self.assertNotIn("county_profile", argument_names)
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
