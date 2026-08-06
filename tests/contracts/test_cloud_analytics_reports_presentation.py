"""Cloud analytics/report presentation must be accurate and fail closed."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from jinja2 import Environment, FileSystemLoader


REPOSITORY = Path(__file__).parents[2]


class CloudAnalyticsReportsPresentationTests(unittest.TestCase):
    def test_analytics_explains_unpopulated_cloud_warehouse_without_implying_sync(self):
        template = (REPOSITORY / "templates" / "analytics.html").read_text(encoding="utf-8")
        self.assertIn("WAREHOUSE READY — NO IMPORTED HISTORY", template)
        self.assertIn("no synchronization or import is running", template)
        self.assertIn("No historical analytics imported", template)
        self.assertIn("{% if cloud_analytics_unpopulated %}", template)

    def test_cloud_reports_hide_legacy_run_action_and_explain_gate(self):
        template = (REPOSITORY / "templates" / "reports.html").read_text(encoding="utf-8")
        self.assertIn("HISTORICAL DATA REQUIRED", template)
        self.assertIn("No direct CAD report query will run", template)
        self.assertIn("{% if cloud_reporting_available %}", template)
        self.assertIn('<form id="county-report-form"', template)
        self.assertIn('<script src="/static/js/lcdash-reports.js', template)

        rendered = Environment(loader=FileSystemLoader(REPOSITORY / "templates")).get_template(
            "reports.html"
        ).render(
            cloud_reporting_available=False,
            request=SimpleNamespace(url=SimpleNamespace(path="/reports")),
            version="test",
        )
        self.assertNotIn('id="county-report-form"', rendered)
        self.assertNotIn("lcdash-reports.js", rendered)
        self.assertIn("HISTORICAL DATA REQUIRED", rendered)

    def test_county_commission_start_route_fails_closed_before_service_call(self):
        tree = ast.parse((REPOSITORY / "app" / "main.py").read_text(encoding="utf-8"))
        handler = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "county_commission_job_start_api"
        )
        statements = handler.body
        self.assertIsInstance(statements[0], ast.If)
        self.assertIn("settings.deployment_mode == 'synthetic-disconnected'", ast.unparse(statements[0]))
        service_statement = next(
            index for index, statement in enumerate(statements)
            if "start_county_commission_job" in ast.unparse(statement)
        )
        self.assertGreater(service_statement, 0)

        guard_source = ast.unparse(statements[0])
        self.assertIn("raise HTTPException", guard_source)
        self.assertIn("status_code=409", guard_source)

    def test_page_routes_supply_explicit_cloud_presentation_flags(self):
        source = (REPOSITORY / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"cloud_analytics_unpopulated": (', source)
        self.assertIn('cloud_reporting_available = settings.deployment_mode != "synthetic-disconnected"', source)
        self.assertIn('"cloud_reporting_available": cloud_reporting_available', source)


if __name__ == "__main__":
    unittest.main()
