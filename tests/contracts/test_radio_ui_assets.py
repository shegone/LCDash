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
CACHE_BUST = "20261017-radio4"
# The dashboard alerts panel (dashboard.html and its script and style) did not
# change in v3, so it keeps its own cache-bust string.
ALERTS_CACHE_BUST = "20261017-radio3"


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
            'id="radio-tab-hints"',
            'id="radio-panel-hints"',
            'id="radio-hint-form"',
            "Alert keywords",
            "Transcript fixes",
            "Hint words",
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


class ListenerHealthPanelTests(unittest.TestCase):
    """Radio v3: the health panel and the administrator hint-words tab."""

    def setUp(self):
        self.template = read(ROOT / "templates" / "mindshare_radio.html")
        self.script = read(RADIO_JS)
        self.css = read(RADIO_CSS)

    def test_health_panel_hooks_exist_and_the_old_strip_is_gone(self):
        for needle in (
            'id="radio-health"',
            'id="radio-stats"',
            'id="radio-stats-error"',
            'id="radio-health-announce"',
            "Listener health",
        ):
            self.assertIn(needle, self.template, needle)
        self.assertNotIn("Last 24 hours", self.template)
        # Re-rendered every poll, so it must not be a live region itself.
        self.assertRegex(self.template, r'id="radio-stats"[^>]*aria-live="off"')

    def test_health_panel_reads_the_stats_listener_object(self):
        for needle in (
            "/api/radio/stats",
            "stats.listener",
            "listener.reporting",
            "listener.age_seconds",
            "listener.last_heartbeat_at",
            "listener.capture_uptime_seconds",
            "listener.listeners",
            "packets_interval",
            "interval_seconds",
            "other_payload_types",
            "transcriber",
            "uploader",
            "oldest_queued_age_seconds",
            "radio-health-warning",
            "radio-health-detail",
            "radio-health-tiles",
        ):
            self.assertIn(needle, self.script, needle)

    def test_listener_tile_wording_for_the_three_states(self):
        for needle in (
            "`Reporting, ${age}`",
            "Not reporting since",
            "No heartbeat received yet",
        ):
            self.assertIn(needle, self.script, needle)

    def test_channel_lines_use_honest_quiet_wording(self):
        self.assertIn("in the last minute", self.script)
        self.assertIn('packet${perMinute === 1 ? "" : "s"}', self.script)
        self.assertIn("no traffic (quiet is normal)", self.script)
        # Per-minute rate comes from the interval counters.
        self.assertIn("(packets * 60) / seconds", self.script)

    def test_undecoded_payload_types_raise_a_visible_warning(self):
        self.assertIn("Undecoded audio: payload type", self.script)
        self.assertIn("These transmissions are not being transcribed.", self.script)
        self.assertIn("bi-exclamation-triangle-fill", self.script)
        self.assertIn(".radio-health-warning", self.css)

    def test_health_panel_never_renders_hosts_or_addresses(self):
        # Only whitelisted listener fields are read: no host, ip, address or
        # source fields appear in the health code at all.
        start = self.script.index("function renderListenerTile")
        end = self.script.index("function stopPolling")
        health = self.script[start:end].lower()
        for banned in ("hostname", "host_name", ".host", "source_ip", "ip_address",
                       "remote_addr", ".address", "sources_seen"):
            self.assertNotIn(banned, health, banned)

    def test_stats_poll_every_15_seconds_only_while_visible(self):
        self.assertIn("STATS_REFRESH_MS = 15000", self.script)
        self.assertIn("window.setInterval(pollStats, STATS_REFRESH_MS)", self.script)
        pool = self.script[self.script.index("function pollStats"):]
        self.assertIn('document.visibilityState !== "visible"', pool[:200])
        self.assertIn("LCDashSession.onEnd(stopPolling)", self.script)
        self.assertIn("LCDashSession.check(response)", self.script)
        self.assertIn("is-stale", self.script)

    def test_hint_tab_is_admin_only_and_not_a_literal_tab_role(self):
        outside = self.template
        for body in if_admin_blocks(self.template):
            outside = outside.replace(body, "")
        for marker in ('radio-tab-hints', 'radio-panel-hints', 'radio-hint-', "Hint words",
                       'data-admin-tab="hints"'):
            self.assertNotIn(marker, outside, f"{marker} must be admin-only")
        self.assertIn('data-admin-tab="hints"', self.template)
        self.assertIn('data-panel="radio-panel-hints"', self.template)
        # The script, not the template, gives admin tabs their role.
        self.assertEqual(self.template.count('role="tab"'), 4)
        self.assertIn('button.setAttribute("role", "tab")', self.script)
        self.assertIn('tab.name === "hints"', self.script)

    def test_hint_note_matches_the_spec(self):
        text = re.sub(r"\s+", " ", self.template)
        self.assertIn(
            "Hint words help the transcriber hear local names. They reach the listener "
            "within about a minute and apply to new transcriptions only. Watch phrases "
            "and the right-hand side of fixes are included automatically.",
            text,
        )

    def test_hint_tab_says_hints_are_switched_off_at_the_listener(self):
        text = re.sub(r"\s+", " ", self.template)
        self.assertIn('id="radio-hint-mode-note"', text)
        self.assertIn("Currently switched off at the listener", text)

    def test_hint_tab_uses_the_hint_api_with_a_two_step_delete(self):
        for needle in (
            "/api/radio/hints",
            '"POST", "/api/radio/hints", { phrase: text }',
            '"DELETE", `/api/radio/hints/${encodeURIComponent(hint.id)}`',
            "MAX_HINT = 60",
            "MIN_HINT = 2",
            "hint.createdBy",
            "makeDeleteControl(`hint word",
            "The hint word could not be added",
        ):
            self.assertIn(needle, self.script, needle)
        self.assertIn('minlength="2" maxlength="60"', self.template)
        # A 409 shows the server's reason through the shared inline error path.
        self.assertIn("httpSuffix(response.status, detail)", self.script)


class DashboardAlertsPanelTests(unittest.TestCase):
    def setUp(self):
        self.template = read(ROOT / "templates" / "dashboard.html")
        self.script = read(ALERTS_JS)

    def test_dashboard_includes_panel_script_and_style(self):
        self.assertIn('id="radio-alerts-panel"', self.template)
        self.assertIn("Radio keyword alerts", self.template)
        self.assertIn("No radio keyword alerts", self.template)
        self.assertIn("Machine-generated. Unverified. Advisory only.", self.template)
        self.assertIn(f"/static/js/lcdash-radio-alerts.js?v={ALERTS_CACHE_BUST}", self.template)
        self.assertIn(f"/static/css/lcdash-radio-alerts.css?v={ALERTS_CACHE_BUST}", self.template)

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
