"""Import-safe trusted-context wiring contract for speech generation."""

import ast
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


MAIN_PATH = Path(__file__).parents[2] / "app" / "main.py"


class VoiceRouteWiringTests(unittest.TestCase):
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
            and node.name == "voice_speech_api"
        )

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def test_speech_route_uses_trusted_dependency_and_exact_forwarding(self):
        argument_names = [argument.arg for argument in self.handler.args.args]
        self.assertEqual(argument_names, ["payload", "tenant_context"])
        annotation = ast.unparse(self.handler.args.args[-1].annotation)
        self.assertIn("Depends(get_trusted_tenant_context)", annotation)
        self.assertEqual(ast.literal_eval(self.handler.args.defaults[-1]), None)

        speech_calls = [
            node
            for node in ast.walk(self.handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "synthesize_speech"
        ]
        self.assertEqual(len(speech_calls), 1)
        forwarded = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in speech_calls[0].keywords
        }
        self.assertEqual(forwarded["tenant_context"], "tenant_context")
        self.assertNotIn("tenant_id", argument_names)
        self.assertNotIn("county_profile", argument_names)
        self.assertNotIn("tenant_id", ast.unparse(self.handler))
        self.assertNotIn("county_profile", ast.unparse(self.handler))
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
