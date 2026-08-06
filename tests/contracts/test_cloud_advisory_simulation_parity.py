from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CloudAdvisorySimulationParityTests(unittest.TestCase):
    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_mae_fails_closed_before_any_browser_status_request(self):
        template = self.read("templates/mae.html").lower()
        script = self.read("static/js/lcdash-mae.js")
        self.assertIn("approved advisory source unavailable", template)
        self.assertIn("cannot dispatch", template)
        gate = script.index("if (cloudMode && !advisoryReady)")
        status_request = script.index('fetch("/api/mae/status"')
        self.assertLess(gate, status_request)
        self.assertIn("questionInput.disabled = true", script[gate:status_request])

    def test_mindshare_is_explicitly_unavailable_and_action_free(self):
        landing = self.read("templates/mindshare.html").lower()
        technical = self.read("templates/mindshare_technical.html").lower()
        script = self.read("static/js/lcdash-mindshare.js")
        self.assertIn("approved mindshare cloud library unavailable", landing)
        self.assertNotIn("<div class=\"mindshare-module-state is-ready\">available", landing)
        for phrase in ("cannot transmit radio traffic", "activate alerts or tones", "control emergency-call routing"):
            self.assertIn(phrase, technical)
        gate = script.index("if (!approvedSource)")
        status_request = script.index('fetch("/api/mindshare/status"')
        self.assertLess(gate, status_request)

    def test_nga_is_visual_simulation_without_notifications_audio_or_live_claims(self):
        overview = self.read("templates/nga911_intelligence.html").lower()
        operations = self.read("templates/nga911_operations.html").lower()
        event = self.read("templates/nga911_event.html").lower()
        script = self.read("static/js/lcdash-nga911-operations.js")
        event_script = self.read("static/js/lcdash-nga911-event.js").lower()

        self.assertIn("open network simulation", overview)
        self.assertNotIn("open live network", overview)
        self.assertIn("browser-only synthetic simulation", operations)
        self.assertIn("not an operational acknowledgment", event)
        for forbidden in ("Notification", "AudioContext", "speechSynthesis", "fetch("):
            self.assertNotIn(forbidden, script)
        self.assertNotIn("alert acknowledged", event_script)
        self.assertIn("simulation marked reviewed", event_script)

    def test_inventory_records_route_and_mobile_safety_scope(self):
        plan = self.read("docs/planning/CLOUD_UI_MAE_MINDSHARE_NGA_PARITY_2026-08-06.md").lower()
        for route in ("`/mae`", "`/mindshare/technical`", "`/nga911-intelligence`"):
            self.assertIn(route, plan)
        self.assertIn("mobile parity", plan)
        self.assertIn("none may dispatch", plan)

    def test_secondary_mindshare_and_nga_routes_keep_visible_safety_boundaries(self):
        for path in (
            "templates/mindshare_library.html",
            "templates/mindshare_reliability.html",
            "templates/mindshare_coverage.html",
            "templates/mindshare_radio.html",
            "templates/mindshare_jack_hines.html",
            "templates/nga911_county.html",
            "templates/nga911_event.html",
            "templates/nga911_nova.html",
        ):
            self.assertIn("command-safety-banner", self.read(path), path)
        reliability = self.read("templates/mindshare_reliability.html")
        reliability_script = self.read("static/js/lcdash-mindshare-reliability.js")
        self.assertIn("Reliability runs unavailable", reliability)
        self.assertIn("data-run-jack=\"{{ case.case_id }}\" disabled", reliability)
        self.assertLess(
            reliability_script.index("jack-reliability-unavailable"),
            reliability_script.index('fetch("/api/mindshare/evaluations/run"'),
        )

    def test_unscoped_legacy_advisory_state_fails_closed_in_cloud(self):
        source = self.read("app/main.py")
        self.assertIn("def _deny_unscoped_cloud_advisory_state", source)
        self.assertIn("tenant-isolated cloud deployment", source)
        for handler in (
            "mindshare_chat_api",
            "mindshare_feedback_api",
            "jack_memory_create_api",
            "jack_memory_review_api",
            "mindshare_evaluation_run_api",
            "mae_chat_api",
            "mae_feedback_api",
            "mae_evaluation_run_api",
            "mae_memory_create_api",
            "mae_memory_review_api",
        ):
            start = source.index(f"def {handler}")
            body = source[start:source.find("\n\n@app.", start)]
            self.assertIn("_deny_unscoped_cloud_advisory_state()", body, handler)


if __name__ == "__main__":
    unittest.main()
