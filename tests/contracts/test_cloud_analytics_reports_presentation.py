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

    def test_saved_templates_list_renders_in_cloud_and_only_in_cloud(self):
        """The save button must not be write-only.

        "Save as Template" (MAE/Mindshare) wrote templates that no page ever
        listed -- found live on 2026-08-09 when Ted saved one and could not
        find it again. This section is the read side. It renders only in the
        cloud presentation: the /api/cloud-ai/reports endpoints require the
        cloud tenant context and 403 on-prem, so showing the section there
        would be a dead panel.
        """
        environment = Environment(loader=FileSystemLoader(REPOSITORY / "templates"))
        request = SimpleNamespace(url=SimpleNamespace(path="/reports"))

        cloud = environment.get_template("reports.html").render(
            cloud_reporting_available=False, request=request, version="test"
        )
        self.assertIn('id="saved-templates-list"', cloud)
        self.assertIn('id="saved-templates-error"', cloud)
        self.assertIn("lcdash-report-templates.js", cloud)
        # It must teach where templates come from, or an empty list is a
        # dead end exactly like the page it fixes.
        self.assertIn("Save as Template", cloud)
        self.assertIn("MAE", cloud)

        onprem = environment.get_template("reports.html").render(
            cloud_reporting_available=True, request=request, version="test"
        )
        self.assertNotIn('id="saved-templates-list"', onprem)
        self.assertNotIn("lcdash-report-templates.js", onprem)

    def test_saved_templates_script_reads_the_endpoints_it_claims(self):
        script = (REPOSITORY / "static" / "js" / "lcdash-report-templates.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('fetch("/api/cloud-ai/reports/templates"', script)
        # Export must stay preview-first: the backend requires
        # preview_confirmed and the UI must not fabricate that confirmation
        # without having shown a preview.
        self.assertIn('fetch("/api/cloud-ai/reports/preview"', script)
        self.assertIn("preview_confirmed: true", script)
        export_index = script.index('fetch("/api/cloud-ai/reports/export"')
        preview_index = script.index('fetch("/api/cloud-ai/reports/preview"')
        self.assertGreater(export_index, preview_index)
        # Errors surface the HTTP status instead of a silent dead panel.
        self.assertIn("HTTP ${response.status}", script)

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
