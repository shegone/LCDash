"""Radio v2 UI contracts: corrections, admin tabs, dashboard alerts panel, nav.

Static checks on the templates and scripts (the behaviour itself is exercised
in a browser harness; the API is covered by tests/contracts/test_radio_routes.py).
The scripts render transcripts that are untrusted machine or human text, so the
central rule here is: server text only ever goes in through textContent.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RADIO_JS = ROOT / "static" / "js" / "lcdash-admin-radio.js"
ALERTS_JS = ROOT / "static" / "js" / "lcdash-radio-alerts.js"
RADIO_CSS = ROOT / "static" / "css" / "lcdash-admin-radio.css"
ALERTS_CSS = ROOT / "static" / "css" / "lcdash-radio-alerts.css"
CACHE_BUST = "20261017-radio3"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def code_only(source: str) -> str:
    """The script without whole-line // comments (they may mention banned names)."""
    return "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("//")
    )


def if_admin_blocks(template: str) -> list[str]:
    """Bodies of every {% if is_admin %} ... {% endif %} block (no nesting used)."""
    return re.findall(r"{%\s*if is_admin\s*%}(.*?){%\s*endif\s*%}", template, re.S)


class NoHtmlInjectionTests(unittest.TestCase):
    def test_no_html_assignment_in_any_radio_script(self):
        for path in (RADIO_JS, ALERTS_JS):
            source = read(path)
            for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                           "document.write", "createContextualFragment", "DOMParser"):
                self.assertNotIn(banned, source, f"{path.name} uses {banned}")
            self.assertIn("textContent", source, path.name)

    def test_no_modal_dialogs(self):
        for path in (RADIO_JS, ALERTS_JS):
            code = code_only(read(path))
            for banned in (r"(?<![\w.-])alert\(", r"(?<![\w.-])confirm\(", r"(?<![\w.-])prompt\("):
                self.assertIsNone(re.search(banned, code), f"{path.name}: {banned}")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_scripts_parse(self):
        for path in (RADIO_JS, ALERTS_JS):
            result = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True, timeout=60
            )
            self.assertEqual(result.returncode, 0, result.stderr)


class CorrectionWiringTests(unittest.TestCase):
    def setUp(self):
        self.script = read(RADIO_JS)

    def test_edit_and_revert_call_the_correction_api(self):
        for needle in (
            '"PUT"',
            '"DELETE"',
            "/correction",
            "{ text: text }",
            "MAX_CORRECTION = 20000",
            "area.maxLength = MAX_CORRECTION",
            '"Edit"',
            '"Revert to machine text"',
            '"Show machine text"',
            "machine_transcript",
            "Corrected by",
            "Keep correction",
            "Corrected audio and text are kept for 1 year.",
        ):
            self.assertIn(needle, self.script)

    def test_editor_keyboard_and_character_count(self):
        self.assertIn('event.key === "Escape"', self.script)
        self.assertIn("event.ctrlKey || event.metaKey", self.script)
        self.assertIn("Ctrl+Enter saves. Esc cancels.", self.script)
        self.assertIn("radio-editor-count", self.script)

    def test_card_text_uses_display_text_and_low_confidence_only_when_uncorrected(self):
        self.assertIn("segment.text", self.script)
        self.assertIn("if (corrected) {", self.script)
        self.assertIn("} else if (segment.low_confidence) {", self.script)

    def test_errors_show_status_and_detail_inline(self):
        self.assertIn("HTTP ${status}", self.script)
        self.assertIn("readDetail", self.script)
        self.assertIn("The correction could not be saved", self.script)
        self.assertIn("The correction could not be reverted", self.script)

    def test_refresh_never_overwrites_a_card_being_edited(self):
        self.assertIn("state.editing", self.script)
        self.assertIn("hasEditingCard()", self.script)
        self.assertIn("is-editing", self.script)

    def test_playback_survives_a_card_update(self):
        # The audio area (and its Play button) is reused across in-place updates.
        self.assertIn("state.audio", self.script)
        self.assertIn("audio.available !== Boolean(segment.audio_available)", self.script)
        self.assertIn("new Audio()", self.script)
        self.assertIn('preload = "none"', self.script)


class RadioTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = read(ROOT / "templates" / "mindshare_radio.html")

    def test_admin_sections_exist_only_inside_is_admin_blocks(self):
        blocks = "\n".join(if_admin_blocks(self.template))
        outside = self.template
        for body in if_admin_blocks(self.template):
            outside = outside.replace(body, "")
        for marker in (
            'id="radio-tab-watch"',
            'id="radio-tab-fixes"',
            'id="radio-panel-watch"',
            'id="radio-panel-fixes"',
            'id="radio-watch-form"',
            'id="radio-fix-form"',
            "Alert keywords",
            "Transcript fixes",
        ):
            self.assertIn(marker, blocks, marker)
            self.assertNotIn(marker, outside, f"{marker} must be admin-only")

    def test_admin_notes_are_present(self):
        text = re.sub(r"\s+", " ", self.template)
        self.assertIn(
            "A match raises a visible alert on the dashboard. It makes no sound and "
            "sends nothing to CAD, radio, or paging.",
            text,
        )
        self.assertIn(
            "Fixes are applied to machine transcripts when displayed. Human corrections "
            "are never changed by a fix.",
            text,
        )

    def test_admin_forms_cover_the_api_fields(self):
        for needle in (
            'minlength="2" maxlength="80"',
            'name="channel" value="all"',
            'name="channel" value="law"',
            'name="channel" value="ems"',
            'name="channel" value="fire"',
            'id="radio-fix-find"',
            'id="radio-fix-replace"',
            'id="radio-fix-whole"',
        ):
            self.assertIn(needle, self.template)

    def test_admin_scripts_use_the_admin_apis(self):
        script = read(RADIO_JS)
        for needle in (
            "/api/radio/watch-terms",
            "/api/radio/replacements",
            '"PATCH"',
            "Delete this?",
            "data-admin-tab",
            "dataset.adminTab",
        ):
            self.assertIn(needle, script)

    def test_cache_bust_strings(self):
        self.assertIn(f"lcdash-admin-radio.js?v={CACHE_BUST}", self.template)
        self.assertIn(f"lcdash-admin-radio.css?v={CACHE_BUST}", self.template)

    def test_template_is_not_described_as_admin_only(self):
        self.assertNotIn("Administrators only", self.template)

    def test_channel_tabs_are_unchanged(self):
        # Four channel tabs carry the literal role; admin tabs get theirs from the script.
        self.assertEqual(self.template.count('role="tab"'), 4)


class DashboardAlertsPanelTests(unittest.TestCase):
    def setUp(self):
        self.template = read(ROOT / "templates" / "dashboard.html")
        self.script = read(ALERTS_JS)

    def test_dashboard_includes_panel_script_and_style(self):
        self.assertIn('id="radio-alerts-panel"', self.template)
        self.assertIn("Radio keyword alerts", self.template)
        self.assertIn("No radio keyword alerts", self.template)
        self.assertIn("Machine-generated. Unverified. Advisory only.", self.template)
        self.assertIn(f"/static/js/lcdash-radio-alerts.js?v={CACHE_BUST}", self.template)
        self.assertIn(f"/static/css/lcdash-radio-alerts.css?v={CACHE_BUST}", self.template)

    def test_panel_is_not_role_gated_in_the_template(self):
        panel = self.template[self.template.index('id="radio-alerts-panel"'):]
        panel = panel[: panel.index("</section>")]
        self.assertNotIn("{% if", panel)
        # Starts hidden; the script reveals it only once the API answers.
        opening = re.search(r'<section[^>]*id="radio-alerts-panel"[^>]*>', self.template, re.S)
        self.assertIsNotNone(opening)
        self.assertRegex(opening.group(0), r"\shidden\s")

    def test_polling_follows_the_session_pattern(self):
        self.assertIn("/api/radio/alerts", self.script)
        self.assertIn("POLL_MS = 15000", self.script)
        self.assertIn("LCDashSession.check(response)", self.script)
        self.assertIn("LCDashSession.onEnd(stop)", self.script)
        self.assertIn('document.visibilityState !== "visible"', self.script)
        self.assertIn("response.status === 403 || response.status === 404", self.script)
        self.assertIn("panel.hidden = true", self.script)

    def test_alert_rows_use_dom_nodes_and_the_documented_endpoints(self):
        self.assertIn('element("mark"', self.script)
        self.assertIn("/api/radio/segments/${encodeURIComponent(alert.segment_id)}/audio", self.script)
        self.assertIn("/api/radio/alerts/${encodeURIComponent(alert.alert_id)}/dismiss", self.script)
        self.assertIn('"POST"', self.script)
        self.assertIn("/mindshare/radio", self.script)
        self.assertIn('preload = "none"', self.script)

    def test_panel_is_visual_only(self):
        for banned in ("Notification", "AudioContext", "speechSynthesis", "vibrate",
                       "serviceWorker", "sendBeacon", "WebSocket", "EventSource"):
            self.assertNotIn(banned, self.script)
        # The only Audio is the one a person starts with Play.
        self.assertEqual(self.script.count("new Audio()"), 1)
        self.assertEqual(self.script.count("player.src ="), 1)

    def test_styles_exist(self):
        css = read(ALERTS_CSS)
        for selector in (".radio-alerts", ".radio-alert-mark", ".radio-alert-button"):
            self.assertIn(selector, css)
        self.assertIn("@media (max-width: 600px)", css)
        self.assertIn(".radio-editor", read(RADIO_CSS))


class NavigationTests(unittest.TestCase):
    def test_nav_entry_is_no_longer_admin_gated(self):
        base = read(ROOT / "templates" / "layouts" / "base.html")
        self.assertIn('nav_item("/mindshare/radio"', base)
        for match in re.finditer(
                r'{%\s*if nav_identity\.role == "admin"\s*%}(.*?){%\s*endif\s*%}', base, re.S):
            self.assertNotIn("/mindshare/radio", match.group(1))
        # The avatar kiosk keeps whatever it had (nothing): the entry is skipped for it.
        entry = base[: base.index('nav_item("/mindshare/radio"')]
        self.assertIn('nav_identity.role != "avatar"', entry[entry.rindex("{% if"):])

    def test_radio_entry_is_outside_the_restricted_sections(self):
        base = read(ROOT / "templates" / "layouts" / "base.html")
        operations = base[base.index('id="nav-operations"'):base.index('id="nav-intelligence"')]
        self.assertIn("/mindshare/radio", operations)
        # Not inside the `not restricted` / `not dispatcher` blocks of that section.
        before = operations[: operations.index("/mindshare/radio")]
        # The avatar guard sits immediately before the entry; every other gate
        # in front of it must already be closed.
        before = before[: before.rindex("{% if")]
        opened = len(re.findall(r"{%\s*if ", before))
        closed = len(re.findall(r"{%\s*endif\s*%}", before))
        self.assertEqual(opened, closed, "the Radio entry must not sit inside a role gate")

    def test_hub_card_is_open_to_everyone_who_can_see_it(self):
        hub = read(ROOT / "templates" / "mindshare.html")
        self.assertIn('href="/mindshare/radio"', hub)
        self.assertNotIn("ADMIN ONLY", hub)
        self.assertNotIn("ADMIN TESTING", hub)
        self.assertNotIn("pilot_identity_badge", hub)
        self.assertNotIn("is-planned", hub)


if __name__ == "__main__":
    unittest.main()
