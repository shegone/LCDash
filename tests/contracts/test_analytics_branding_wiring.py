"""Offline route and template contracts for analytics title branding."""

import ast
import socket
import unittest
from pathlib import Path
from unittest.mock import patch


REPOSITORY = Path(__file__).parents[2]
MAIN_PATH = REPOSITORY / "app" / "main.py"
TEMPLATE_PATH = REPOSITORY / "templates" / "analytics.html"


class AnalyticsBrandingWiringTests(unittest.TestCase):
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
            and node.name == "analytics_page"
        )

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_external_access(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_page_passes_only_exact_trusted_context_to_branding_composer(self):
        branding_calls = [
            node
            for node in ast.walk(self.handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "branding_for_tenant_context"
        ]
        self.assertEqual(len(branding_calls), 1)
        self.assertEqual(
            [ast.unparse(argument) for argument in branding_calls[0].args],
            ["tenant_context"],
        )
        self.assertEqual(branding_calls[0].keywords, [])

        template_calls = [
            node
            for node in ast.walk(self.handler)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "TemplateResponse"
        ]
        self.assertEqual(len(template_calls), 1)
        context_keyword = next(
            keyword
            for keyword in template_calls[0].keywords
            if keyword.arg == "context"
        )
        context_entries = {
            ast.literal_eval(key): value
            for key, value in zip(context_keyword.value.keys, context_keyword.value.values)
        }
        self.assertEqual(
            ast.unparse(context_entries["county_branding"]),
            "branding_for_tenant_context(tenant_context)",
        )
        handler_source = ast.unparse(self.handler)
        self.assertNotIn("tenant_id", handler_source)
        self.assertNotIn("county_profile", handler_source)
        self.assert_no_external_access()

    def test_template_changes_only_title_with_exact_legacy_fallback(self):
        source = TEMPLATE_PATH.read_text(encoding="utf-8")
        expected = (
            '{% block title %}Analytics | {{ county_branding.short_name '
            'if county_branding else "LCDash" }}{% endblock %}'
        )
        self.assertIn(expected, source)
        title_line = next(line for line in source.splitlines() if "block title" in line)
        self.assertNotIn("logo_asset", title_line)
        self.assertNotIn("primary_color", title_line)
        self.assertNotIn("accent_color", title_line)
        self.assertNotIn("background_color", title_line)
        self.assert_no_external_access()


if __name__ == "__main__":
    unittest.main()
