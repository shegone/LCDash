"""Import-safe trusted-context wiring contract for the units page."""

import ast
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


MAIN_PATH = Path(__file__).parents[2] / "app" / "main.py"


class UnitsPageRouteWiringTests(unittest.TestCase):
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
        tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        self.handler = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "units_board"
        )

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def test_units_page_uses_trusted_dependency_and_exact_forwarding(self):
        argument_names = [argument.arg for argument in self.handler.args.args]
        self.assertEqual(argument_names, ["request", "tenant_context"])
        annotation = ast.unparse(self.handler.args.args[-1].annotation)
        self.assertIn("Depends(get_trusted_tenant_context)", annotation)
        self.assertEqual(ast.literal_eval(self.handler.args.defaults[-1]), None)

        snapshot_calls = [
            node
            for node in ast.walk(self.handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_current_unit_snapshot"
        ]
        self.assertEqual(len(snapshot_calls), 1)
        forwarded = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in snapshot_calls[0].keywords
        }
        self.assertEqual(forwarded, {"tenant_context": "tenant_context"})
        source = ast.unparse(self.handler)
        self.assertNotIn("tenant_id", source)
        self.assertNotIn("county_profile", source)
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
