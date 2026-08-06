"""Import-safe trusted-context contracts for MAE evaluation wiring."""

import ast
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY = Path(__file__).parents[2]
MAIN_PATH = REPOSITORY / "app" / "main.py"
SERVICE_PATH = REPOSITORY / "app" / "services" / "mae_evaluation_service.py"


def function_named(tree, name):
    return next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


class MAEEvaluationContextWiringTests(unittest.TestCase):
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
        self.main_tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        self.service_tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_external_access(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_route_uses_trusted_dependency_and_forwards_exact_context(self):
        handler = function_named(self.main_tree, "mae_evaluation_run_api")
        argument_names = [argument.arg for argument in handler.args.args]
        self.assertEqual(
            argument_names,
            ["evaluation_request", "request", "response", "tenant_context"],
        )
        annotation = ast.unparse(handler.args.args[-1].annotation)
        self.assertIn("Depends(get_trusted_tenant_context)", annotation)
        self.assertEqual(ast.literal_eval(handler.args.defaults[-1]), None)

        run_calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "run_evaluation_case"
        ]
        self.assertEqual(len(run_calls), 1)
        forwarded = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in run_calls[0].keywords
        }
        self.assertEqual(forwarded["tenant_context"], "tenant_context")
        self.assertNotIn("tenant_id", argument_names)
        self.assertNotIn("county_profile", argument_names)
        self.assert_no_external_access()

    def test_service_preserves_legacy_call_and_forwards_only_trusted_context(self):
        handler = function_named(self.service_tree, "run_evaluation_case")
        argument_names = [argument.arg for argument in handler.args.args]
        self.assertEqual(
            argument_names,
            ["case_id", "requested_by", "tenant_context"],
        )
        self.assertEqual(ast.literal_eval(handler.args.defaults[-1]), None)
        self.assertNotIn("tenant_id", argument_names)
        self.assertNotIn("county_profile", argument_names)

        ask_calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ask_mae"
        ]
        self.assertEqual(len(ask_calls), 2)
        legacy_calls = [call for call in ask_calls if not call.keywords]
        trusted_calls = [call for call in ask_calls if call.keywords]
        self.assertEqual(len(legacy_calls), 1)
        self.assertEqual(len(trusted_calls), 1)
        self.assertEqual(len(legacy_calls[0].args), 3)
        forwarded = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in trusted_calls[0].keywords
        }
        self.assertEqual(forwarded, {"tenant_context": "tenant_context"})

        branch = next(
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "tenant_context is None"
        )
        self.assertTrue(branch.body)
        self.assertTrue(branch.orelse)
        self.assert_no_external_access()


if __name__ == "__main__":
    unittest.main()
