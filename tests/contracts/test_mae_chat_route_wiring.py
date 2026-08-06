"""Import-safe trusted-context wiring contracts for MAE chat routes."""

import ast
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


MAIN_PATH = Path(__file__).parents[2] / "app" / "main.py"


class MAEChatRouteWiringTests(unittest.TestCase):
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

    def _assert_trusted_context_dependency(self, handler, expected_arguments):
        argument_names = [argument.arg for argument in handler.args.args]
        self.assertEqual(argument_names, expected_arguments)
        annotation = ast.unparse(handler.args.args[-1].annotation)
        self.assertIn("Depends(get_trusted_tenant_context)", annotation)
        self.assertEqual(ast.literal_eval(handler.args.defaults[-1]), None)
        self.assertNotIn("tenant_id", argument_names)
        self.assertNotIn("county_profile", argument_names)

    def _assert_exact_ask_forwarding(self, handler):
        ask_calls = [
            node
            for node in ast.walk(handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ask_mae"
        ]
        self.assertEqual(len(ask_calls), 1)
        forwarded = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in ask_calls[0].keywords
        }
        self.assertEqual(forwarded["tenant_context"], "tenant_context")

    def _assert_no_external_access(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_chat_handler_uses_trusted_dependency_and_exact_forwarding(self):
        handler = self._handler("mae_chat_api")
        self._assert_trusted_context_dependency(
            handler,
            ["chat_request", "request", "response", "tenant_context"],
        )
        self._assert_exact_ask_forwarding(handler)
        self._assert_no_external_access()

    def test_stream_worker_captures_and_forwards_exact_trusted_context(self):
        handler = self._handler("mae_chat_stream_api")
        self._assert_trusted_context_dependency(
            handler,
            ["chat_request", "request", "tenant_context"],
        )
        self._assert_exact_ask_forwarding(handler)

        worker = next(
            node
            for node in handler.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "run"
        )
        worker_arguments = [argument.arg for argument in worker.args.args]
        self.assertEqual(worker_arguments, [])
        self.assertNotIn("tenant_id", ast.unparse(worker))
        self.assertNotIn("county_profile", ast.unparse(worker))
        self._assert_no_external_access()


if __name__ == "__main__":
    unittest.main()
