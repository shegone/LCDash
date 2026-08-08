"""Import-safe trusted-context wiring contracts for heatmap routes."""

import ast
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


MAIN_PATH = Path(__file__).parents[2] / "app" / "main.py"


class HeatmapRouteWiringTests(unittest.TestCase):
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
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)
        self.tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def _handler(self, name):
        return next(
            node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )

    def _assert_route(self, name, expected_arguments):
        handler = self._handler(name)
        argument_names = [argument.arg for argument in handler.args.args]
        self.assertEqual(argument_names, expected_arguments)
        annotation = ast.unparse(handler.args.args[-1].annotation)
        self.assertIn("Depends(get_trusted_tenant_context)", annotation)
        self.assertEqual(ast.literal_eval(handler.args.defaults[-1]), None)

        calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_current_heatmap_snapshot"
        ]
        self.assertEqual(len(calls), 1)
        forwarded = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in calls[0].keywords
        }
        self.assertEqual(forwarded, {"tenant_context": "tenant_context"})
        source = ast.unparse(handler)
        self.assertNotIn("tenant_id", source)
        self.assertNotIn("county_profile", source)
        caught = {
            ast.unparse(node.type)
            for node in ast.walk(handler)
            if isinstance(node, ast.ExceptHandler) and node.type is not None
        }
        self.assertEqual(caught, {"CentralSquareAPIError"})

    def test_api_and_page_forward_exact_trusted_context(self):
        self._assert_route(
            "heatmap_api",
            ["response", "hours", "tenant_context"],
        )
        self._assert_route(
            "heatmap_page",
            ["request", "hours", "tenant_context"],
        )
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
