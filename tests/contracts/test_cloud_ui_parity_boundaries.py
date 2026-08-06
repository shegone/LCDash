from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class CloudUiParityBoundaryTests(unittest.TestCase):
    def _read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8").lower()

    def test_gis_map_fails_closed_without_approved_coordinate_source(self):
        template = self._read("templates/map.html")
        self.assertIn("operational map source unavailable", template)
        self.assertIn("no approved cloud source", template)
        self.assertIn("read-only cloud map boundary", template)
        self.assertIn("cannot update cad", template)

    def test_heatmap_fails_closed_without_approved_history(self):
        template = self._read("templates/heatmap.html")
        self.assertIn("historical activity source unavailable", template)
        self.assertIn("no approved imported historical dataset", template)
        self.assertIn("no direct historical", template)
        self.assertIn("read-only cloud history boundary", template)

    def test_reports_remain_read_only_and_source_gated(self):
        template = self._read("templates/reports.html")
        self.assertIn("cloud_reporting_available", template)
        self.assertIn("no direct cad report query", template)
        self.assertIn("read-only cloud reporting boundary", template)
        self.assertIn("reports never update cad", template)

    def test_shared_mobile_layer_preserves_touch_and_safety_presentation(self):
        stylesheet = self._read("static/css/lcdash-command-center.css")
        self.assertIn("--command-touch-target: 44px", stylesheet)
        self.assertIn(".command-unavailable-state", stylesheet)
        self.assertIn(".command-scroll-region", stylesheet)
        self.assertIn("@media (max-width: 575.98px)", stylesheet)
        self.assertIn("min-height: var(--command-touch-target)", stylesheet)

    def test_nga_controls_are_explicitly_browser_only_simulation(self):
        operations = self._read("templates/nga911_operations.html")
        event = self._read("templates/nga911_event.html")
        intelligence = self._read("templates/nga911_intelligence.html")
        stylesheet = self._read("static/css/lcdash-nga911.css")
        self.assertIn("run visual simulation", operations)
        self.assertNotIn("enable simulated alerts", operations)
        self.assertIn("no esinet, ngcs", operations)
        self.assertIn("mark simulation reviewed", event)
        self.assertIn("stored only in this browser", event)
        self.assertIn("not an operational acknowledgment", event)
        self.assertIn("does not route calls", intelligence)
        self.assertIn("or take autonomous action", intelligence)
        self.assertIn(".nga-ops-actions .btn", stylesheet)
        self.assertIn("min-height:44px", stylesheet)
        self.assertIn("@media(max-width:520px)", stylesheet)


if __name__ == "__main__":
    unittest.main()
