from pathlib import Path
import ast
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CloudDashboardUnitsPresentationTests(unittest.TestCase):
    def test_dashboard_uses_one_explicit_cloud_card_mode(self):
        route = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        template = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
        script = (ROOT / "static" / "js" / "lcdash-dashboard.js").read_text(encoding="utf-8")

        dashboard_route = route[route.index('def dashboard('):route.index('@app.get("/active-calls")')]
        self.assertIn('"cloud_presentation": settings.deployment_mode == "synthetic-disconnected"', dashboard_route)
        self.assertIn('data-cloud-presentation=', template)
        self.assertIn('function usesCloudPresentation()', script)
        self.assertIn('createCloudFact("CALL RECEIVED"', script)
        self.assertIn('Array.isArray(call.assigned_units)', script)
        self.assertIn('{{ cloud_presentation_status.source.label }}', template)
        self.assertNotIn('VERIFIED READ-ONLY DATA', template)
        self.assertNotIn('"CALL TAKER", safeText(call.call_taker', script[script.index('if (usesCloudPresentation())'):script.index('const detailRow =')])

    def test_units_route_uses_presentation_source_not_snapshot_success(self):
        route = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        units_route = route[route.index('def units_board('):route.index('@app.get("/calls/{cfs_number}")')]

        self.assertIn('presentation = _cloud_presentation_status()', units_route)
        self.assertIn('source = presentation["source"]', units_route)
        self.assertIn('"cloud_presentation_status": presentation', units_route)
        self.assertNotIn('cad_status = "Connected"', units_route)
        self.assertNotIn('system_status = "Connected"', units_route)

    def test_units_template_never_claims_live_or_connected(self):
        template = (ROOT / "templates" / "units.html").read_text(encoding="utf-8")

        self.assertIn('cloud_presentation_status.source.may_display_snapshot', template)
        self.assertIn('{{ cloud_presentation_status.source.notice }}', template)
        self.assertIn('Unit information is read-only.', template)
        self.assertIn('in Displayed Snapshot', template)
        self.assertNotIn('LIVE CAD DATA', template)
        self.assertNotIn('>CONNECTED<', template)

    def test_cloud_json_apis_use_shared_verified_source_state(self):
        tree = ast.parse((ROOT / "app" / "main.py").read_text(encoding="utf-8"))
        for handler_name in ("active_calls_api", "units_api"):
            handler = next(
                node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == handler_name
            )
            handler_source = ast.unparse(handler)
            self.assertIn("_cloud_presentation_status()['source']", handler_source)
            self.assertIn("'connected': source['connected']", handler_source)
            self.assertNotIn("'connected': True", handler_source)

    def test_cloud_webhook_is_denied_before_auth_or_body_read(self):
        tree = ast.parse((ROOT / "app" / "main.py").read_text(encoding="utf-8"))
        handler = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "receive_centralsquare_webhook"
        )
        statements = [ast.unparse(node) for node in handler.body]
        self.assertIn("settings.deployment_mode == 'synthetic-disconnected'", statements[0])
        self.assertIn("403", statements[0])
        self.assertEqual(statements[1], "_authorize_centralsquare_webhook(request)")

    def test_cloud_tenant_context_comes_only_from_deployment_configuration(self):
        main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        settings_source = (ROOT / "app" / "config" / "settings.py").read_text(encoding="utf-8")
        tree = ast.parse(main_source)
        handler = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "get_trusted_tenant_context"
        )
        source = ast.unparse(handler)
        self.assertIn('settings.tenant_id', source)
        self.assertIn('TenantContext(', source)
        self.assertNotIn('Request', source)
        self.assertIn('tenant_id: str = _env("LCDASH_TENANT", "logan-synthetic")', settings_source)


if __name__ == "__main__":
    unittest.main()
