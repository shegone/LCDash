"""Network-free contracts for the cloud-only MAE advisory client branch."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MaeCloudChatPresentationTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_mae_page_exposes_explicit_cloud_mode_without_changing_on_prem(self):
        route = self.read("app/main.py")
        template = self.read("templates/mae.html")

        self.assertIn('"cloud_mode": cloud_mode_enabled(settings)', route)
        self.assertIn('data-cloud-mode="{{ \'true\' if cloud_mode else \'false\' }}"', template)
        self.assertIn("{% if cloud_mode %}", template)
        self.assertIn("{% else %}Talk naturally with MAE{% endif %}", template)

    def test_cloud_client_uses_only_citation_advisory_request_shape(self):
        script = self.read("static/js/lcdash-mae.js")

        self.assertIn('cloudMode ? "/api/cloud-ai/advisory" : "/api/mae/chat"', script)
        self.assertIn("? {question: question}", script)
        self.assertIn(": {question: question, history: requestHistory, entities: entities}", script)
        self.assertIn("if (!cloudMode) mergeEntities(payload.entities);", script)
        self.assertIn('document.querySelectorAll("[data-mae-prompt], .mae-prompt-folder")', script)
        self.assertIn("if (cloudMode) {", script)
        self.assertIn("loadStatus();", script)
        self.assertIn("loadVoiceStatus();", script)

    def test_cloud_answers_require_citations_and_render_sanitized_fields(self):
        script = self.read("static/js/lcdash-mae.js")

        self.assertIn("!Array.isArray(result.citations) || !result.citations.length", script)
        self.assertIn("mandatory approved citations were missing", script)
        self.assertIn("approved citation support was unavailable", script)
        self.assertIn('citationBlock.setAttribute("aria-label", "Approved document citations")', script)
        self.assertIn('citation.title || "Approved document"', script)
        self.assertNotIn("citation.source_uri", script)

    def test_legacy_cloud_routes_remain_server_denied(self):
        source = self.read("app/main.py")
        chat = source[source.index('@app.post("/api/mae/chat")'):]
        stream = source[source.index('@app.post("/api/mae/chat/stream")'):]

        self.assertIn("_deny_unscoped_cloud_advisory_state()", chat.split("@app.", 2)[1])
        self.assertIn("_deny_unscoped_cloud_advisory_state()", stream.split("@app.", 2)[1])

    def test_cloud_presentation_discloses_capabilities_and_boundary(self):
        """The page must describe what cloud MAE actually does today.

        The original assertion pinned "no memory, feedback, tools, reports,
        widgets" -- true when written, false after tool-calling (rev 38),
        report templates, widgets, and voice all shipped; the greeting kept
        telling users MAE "cannot use CAD data" while she answered live CAD
        questions daily. What must never soften is the write boundary, so
        that is pinned in BOTH user-visible texts.
        """
        template = self.read("templates/mae.html")

        # The greeting owns the capability story...
        for capability in ("active\n                            calls", "analytics", "citations", "voice"):
            self.assertIn(capability, template)
        # ...and both greeting and source note state the read-only boundary.
        self.assertIn("I can never dispatch, page, set off tones", template)
        self.assertIn("no dispatch, paging, tones, or CAD writes", template)
        self.assertIn("strictly read-only", template)
        # Document answers still require citations; uploads are disclosed as
        # part of the citation pool now that /admin/knowledge exists.
        self.assertIn("cited approved documents", template)
        self.assertIn("uploaded to the knowledge library", template)
        # Voice starts gated until the status endpoint proves readiness --
        # the button may not default to available.
        self.assertIn('id="mae-voice-toggle" type="button"\n                    {% if cloud_mode %}disabled', template)


if __name__ == "__main__":
    unittest.main()
