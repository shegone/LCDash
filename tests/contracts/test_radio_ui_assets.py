"""Radio v2 UI contracts: corrections, admin tabs, dashboard alerts panel, nav.

Static checks on the templates and scripts (the behaviour itself is exercised
in a browser harness; the API is covered by tests/contracts/test_radio_routes.py).
The scripts render transcripts that are untrusted machine or human text, so the
central rule here is: server text only ever goes in through textContent.
"""

import json
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
CACHE_BUST = "20261020-radio9"
# The dashboard alerts panel (dashboard.html and its script and style) did not
# change in v3, so it keeps its own cache-bust string.
ALERTS_CACHE_BUST = "20261020-radio5"


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


class PossibleNoiseUiTests(unittest.TestCase):
    """The display-only "Possible noise" badge and the hide toggle."""

    def setUp(self):
        self.template = read(ROOT / "templates" / "mindshare_radio.html")
        self.script = read(RADIO_JS)
        self.css = read(RADIO_CSS)

    def test_template_has_the_unchecked_filter_checkbox_and_count_line(self):
        self.assertIn('id="radio-hide-noise"', self.template)
        self.assertIn("Hide possible noise", self.template)
        self.assertIn('id="radio-noise-count"', self.template)
        checkbox = re.search(r'<input id="radio-hide-noise"[^>]*>', self.template)
        self.assertIsNotNone(checkbox)
        self.assertNotIn("checked", checkbox.group(0))
        # In the filter row, not outside it.
        form = re.search(
            r'<form id="radio-filter-form".*?</form>', self.template, re.S
        ).group(0)
        self.assertIn('id="radio-hide-noise"', form)

    def test_badge_text_title_and_flag_field(self):
        self.assertIn('"Possible noise"', self.script)
        self.assertIn(
            "Very short or boilerplate text that the transcriber often invents "
            "from background noise. Listen to confirm.",
            re.sub(r'"\s*\+\s*"', "", self.script),
        )
        self.assertIn("segment.possible_noise", self.script)
        self.assertIn('"is-noise"', self.script)

    def test_low_confidence_and_noise_badges_are_independent(self):
        # The noise badge is its own branch, not part of the else-if chain.
        self.assertIn("} else if (segment.low_confidence) {", self.script)
        self.assertIn("!corrected && Boolean(segment.possible_noise)", self.script)

    def test_hide_toggle_is_client_side_and_remembered_safely(self):
        self.assertIn("sessionStorage", self.script)
        self.assertIn("HIDE_NOISE_KEY", self.script)
        for name in ("readHideNoise", "saveHideNoise"):
            body = re.search(
                r"function %s\(.*?\n    }\n" % name, self.script, re.S
            )
            self.assertIsNotNone(body, name)
            self.assertIn("try {", body.group(0), name)
            self.assertIn("catch (error)", body.group(0), name)
        self.assertIn("hide-noise", self.script)
        self.assertIn("hidden as possible noise", self.script)
        self.assertIn("updateNoiseCount", self.script)

    def test_hiding_is_a_class_on_the_shared_panels_box(self):
        # One class on #radio-panels covers every tab, including cards loaded
        # or refreshed later; nothing is removed from the DOM.
        self.assertIn(".radio-panels.hide-noise .radio-item.is-noise", self.css)
        self.assertIn("display: none", self.css)
        self.assertIn(".radio-item.is-noise .radio-item-text", self.css)
        self.assertIn(".radio-badge.is-noise", self.css)

    def test_noise_text_goes_in_through_textcontent_only(self):
        self.assertNotIn("innerHTML", self.script)
        self.assertIn('element("span", "radio-badge is-noise", "Possible noise")',
                      self.script)

    def test_the_changed_assets_use_the_new_cache_bust(self):
        self.assertEqual(CACHE_BUST, "20261020-radio9")
        self.assertIn(f"lcdash-admin-radio.js?v={CACHE_BUST}", self.template)
        self.assertIn(f"lcdash-admin-radio.css?v={CACHE_BUST}", self.template)
        self.assertNotIn("20261017-radio4", self.template)
        self.assertNotIn("20261018-radio6", self.template)


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
            'id="radio-watch-channels"',
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

    def test_group_tabs_carry_the_literal_role(self):
        # All, Law, EMS, Fire and Other carry the literal role; admin tabs get
        # theirs from the script.
        self.assertEqual(self.template.count('role="tab"'), 5)
        for group in ("", "law", "ems", "fire", "other"):
            self.assertIn(f'data-group="{group}"', self.template, group)
        self.assertNotIn("data-channel=", self.template)
        self.assertIn('id="radio-districts"', self.template)


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
        self.assertEqual(self.template.count('role="tab"'), 5)
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


class RadioIdAndUnitsUiTests(unittest.TestCase):
    """Radio v4: who is talking (chips, radio ID filter) and the admin Units tab."""

    def setUp(self):
        self.template = read(ROOT / "templates" / "mindshare_radio.html")
        self.script = read(RADIO_JS)
        self.css = read(RADIO_CSS)
        self.admin_only = "\n".join(if_admin_blocks(self.template))
        self.outside = self.template
        for body in if_admin_blocks(self.template):
            self.outside = self.outside.replace(body, "")

    def test_units_tab_exists_only_inside_is_admin_blocks(self):
        for marker in (
            'id="radio-tab-units"',
            'data-admin-tab="units"',
            'data-panel="radio-panel-units"',
            'id="radio-panel-units"',
            'id="radio-unit-list"',
            'id="radio-unit-form"',
            'id="radio-unit-unnamed"',
            'id="radio-unit-new-id"',
            'id="radio-unit-new-name"',
            "Show only unnamed",
            "Add by ID",
        ):
            self.assertIn(marker, self.admin_only, marker)
            self.assertNotIn(marker, self.outside, f"{marker} must be admin-only")
        self.assertNotIn("radio-unit-", self.outside)
        text = re.sub(r"\s+", " ", self.admin_only)
        self.assertIn("Names appear on every transcript card for everyone.", text)
        # The script, not the template, gives the admin tab its role.
        self.assertEqual(self.template.count('role="tab"'), 5)
        self.assertIn('tab.name === "units"', self.script)
        self.assertIn("setupUnitsTab(tab)", self.script)

    def test_hint_panel_closes_before_the_units_panel_opens(self):
        hints = self.template.index('id="radio-panel-hints"')
        units = self.template.index('id="radio-panel-units"')
        between = self.template[hints:units]
        # The hint panel's own <div> is the one still open at the end.
        self.assertEqual(len(re.findall(r"<div\b", between)),
                         len(re.findall(r"</div>", between)))

    def test_units_form_limits_match_the_spec(self):
        self.assertRegex(self.template, r'id="radio-unit-new-id"[^>]*maxlength="10"')
        self.assertRegex(self.template, r'id="radio-unit-new-name"[^>]*maxlength="60"')
        self.assertIn("MAX_UNIT_NAME = 60", self.script)
        self.assertIn("RADIO_ID_PATTERN = /^[0-9]{1,10}$/", self.script)
        self.assertIn("TALKGROUP_PATTERN = /^G[0-9]{1,6}$/", self.script)

    def test_units_tab_uses_the_units_api_with_a_two_step_clear(self):
        for needle in (
            '"GET", "/api/radio/units"',
            '"PUT", `/api/radio/units/${encodeURIComponent(unit.id)}`',
            '"PUT", `/api/radio/units/${encodeURIComponent(id)}`',
            '"DELETE", `/api/radio/units/${encodeURIComponent(unit.id)}`',
            "{ name: name }",
            "Clear this name?",
            "Yes, clear",
            "makeDeleteControl(`name of radio ID",
            "The unit name could not be saved",
            "The unit name could not be cleared",
            "Units could not be loaded",
            "segments_30d",
            "last_seen_at",
            "max_units",
            "is-only-unnamed",
        ):
            self.assertIn(needle, self.script, needle)
        # Failures (400, 409, 503) show the server's reason through the shared path.
        self.assertIn("httpSuffix(response.status, detail)", self.script)
        # The delete control keeps its wording for every other tab.
        self.assertIn('ask: "Delete this?"', self.script)

    def test_feed_filter_and_chip_hooks(self):
        for needle in (
            'params.set("radio_id", radioFilter.id)',
            "function setRadioFilter(id, name)",
            "Showing radio ID ${radioFilter.id}",
            "Clear the radio ID filter",
            "`Console: ${label}`",
            "`ID ${id}`",
            "segment.unit_name",
            "segment.talkgroup",
            "segment.console_label",
            "segment.radio_id",
            'element("button", `radio-who',
            "unitsDirty",
        ):
            self.assertIn(needle, self.script, needle)
        # An empty chip is never drawn: every piece is checked first.
        self.assertIn('RADIO_ID_PATTERN.test(id) ? id : ""', self.script)
        self.assertIn("TALKGROUP_PATTERN.test(group)", self.script)
        self.assertIn("CONSOLE_LABEL_PATTERN.test(label)", self.script)
        # A rename changes the chip, so these are part of the card signature.
        self.assertIn('segment.radio_id || ""', self.script)
        self.assertIn('segment.unit_name || ""', self.script)

    def test_filter_bar_is_in_the_page_for_everyone(self):
        self.assertIn('id="radio-id-filter"', self.outside)
        opening = re.search(r'<div id="radio-id-filter"[^>]*>', self.template).group(0)
        self.assertIn('class="radio-id-filter"', opening)
        self.assertIn('role="status"', opening)
        self.assertRegex(opening, r"\shidden")

    def test_search_box_says_it_finds_ids_and_unit_names(self):
        box = re.search(r'<input id="radio-filter-q"[^>]*>', self.template).group(0)
        self.assertIn("radio ID", box)
        self.assertIn("unit name", box)
        self.assertNotIn("Transcript contains", self.template)

    def test_names_and_ids_only_ever_go_in_through_textcontent(self):
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
            self.assertNotIn(banned, self.script)
        code = code_only(self.script)
        self.assertNotIn("innerText", code)
        self.assertNotIn(".href =", code)
        self.assertNotIn('setAttribute("on', code)

    def test_styles_for_chips_filter_bar_and_units_rows_exist(self):
        for selector in (
            ".radio-who",
            ".radio-who.is-named",
            ".radio-tg",
            ".radio-console",
            ".radio-id-filter",
            ".radio-id-filter-clear",
            ".radio-unit-row",
            ".radio-unit-name",
            ".radio-unit-list.is-only-unnamed .radio-unit-row.is-named",
        ):
            self.assertIn(selector, self.css, selector)

    def test_other_admin_tabs_are_still_wired(self):
        for name in ("watch", "fixes", "hints"):
            self.assertIn(f'tab.name === "{name}"', self.script)
            self.assertIn(f'data-admin-tab="{name}"', self.template)


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


class ChannelGroupTests(unittest.TestCase):
    """The listener channels: grouping, labels and the group feed query."""

    def setUp(self):
        self.script = read(RADIO_JS)
        self.alerts = read(ALERTS_JS)
        self.css = read(RADIO_CSS)
        self.config = json.loads(
            read(ROOT / "radio_listener" / "config.json"))

    def test_every_configured_channel_is_in_the_catalog(self):
        names = [channel["name"] for channel in self.config["channels"]]
        self.assertEqual(len(names), len(set(names)))
        catalog = set(re.findall(r"^\s{8}(\w+): \{ short:", self.script, re.M))
        self.assertEqual(set(names), catalog)
        for name in names:
            self.assertRegex(name, r"^[a-z0-9_]{1,32}$")

    def test_channels_follow_the_mindshare_sheet(self):
        # Mindshare sheet v117 as revised 2026-10-20: card number -> channel.
        by_name = {c["name"]: c for c in self.config["channels"]}
        expected = {
            "ems_ward_vhf": 20, "ems_south_vhf": 21, "ems_park_vhf": 22,
            "ems_dmr": 23, "fire_ward_vhf": 24, "fire_south_vhf": 25,
            "fire_park_vhf": 26, "fire_dmr": 27, "fire_horsepen_vhf": 28,
            "law_ward_vhf": 29, "law_south_vhf": 30, "law_park_vhf": 31,
            "sirn_multi_vhf": 38, "fire_north_vhf": 35, "law_dmr": 36,
            "fire_lmars": 41, "ems_lmars": 42, "law_lmars": 43,
        }
        for name, number in expected.items():
            channel = by_name[name]
            self.assertEqual(channel["group"], f"235.98.99.{number}", name)
            # Same direction convention as the SIRN channels already running.
            self.assertEqual(channel["ports"],
                             {"tx": 10000 + number, "rx": 12000 + number}, name)
        self.assertEqual(by_name["law"]["ports"], {"tx": 10032, "rx": 12032})
        # Two cards were VHF channels in the first sheet and are DMR now;
        # their old names must not come back on those addresses.
        self.assertNotEqual(by_name["fire_north_vhf"]["group"], "235.98.99.27")
        self.assertNotIn("ems_north_vhf", by_name)
        groups = [c["group"] for c in self.config["channels"]]
        self.assertEqual(len(groups), len(set(groups)))
        self.assertLessEqual(len(by_name), self.config["max_channels"])

    def test_a_watch_keyword_can_name_every_channel(self):
        from app.services import radio_text
        self.assertGreaterEqual(
            radio_text.MAX_TERM_CHANNELS, len(self.config["channels"]))

    def test_groups_and_the_group_query(self):
        for needle in (
            'channel === group.key || channel.startsWith(`${group.key}_`)',
            'groupChannels(tab.group).join(",")',
            "if (tab.selected) return tab.selected;",
            'params.set("channel", channelFilter)',
        ):
            self.assertIn(needle, self.script, needle)
        # No tab is built from a name the server sent.
        self.assertNotIn("ensureChannelTab", self.script)

    def test_alert_chips_follow_the_same_prefix_rule(self):
        self.assertIn("channel.startsWith(`${group}_`)", self.alerts)

    def test_district_chips_are_styled_and_accessible(self):
        self.assertIn(".radio-district[aria-pressed=\"true\"]", self.css)
        self.assertIn('chip.setAttribute("aria-pressed"', self.script)
        self.assertIn('"Narrow to one channel"', read(ROOT / "templates" / "mindshare_radio.html"))


class DisciplineColourTests(unittest.TestCase):
    """Law blue, EMS green, fire red, on cards, tabs, chips and dashboard alerts."""

    def setUp(self):
        self.css = read(RADIO_CSS)
        self.alerts_css = read(ALERTS_CSS)
        self.script = read(RADIO_JS)

    def test_card_chips_and_alert_chips_use_the_three_colours(self):
        for rule, colour in (
            (".radio-chip-law", "#4cc9ff"),
            (".radio-chip-ems", "#5ee79a"),
            (".radio-chip-fire", "#ff7070"),
        ):
            self.assertRegex(self.css, re.escape(rule) + r" \{ --chip: " + colour)
        self.assertIn(".radio-alert-chip.is-ems { border-color: #5ee79a", self.alerts_css)
        self.assertIn(".radio-alert-chip.is-fire { border-color: #ff7070", self.alerts_css)

    def test_other_channel_colours_avoid_the_discipline_hues(self):
        others = re.findall(r"\.radio-chip-c\d \{ --chip: (#\w+)", self.css)
        self.assertEqual(len(others), 4)
        for colour in ("#4cc9ff", "#5ee79a", "#ff7070", "#69ffb9", "#5eead4",
                       "#ff8aa0", "#ffab5e"):
            self.assertNotIn(colour, others)

    def test_group_tabs_and_chips_take_the_group_colour(self):
        for group, colour in (("law", "#4cc9ff"), ("ems", "#5ee79a"), ("fire", "#ff7070")):
            self.assertIn(f'.radio-tab[data-group="{group}"]', self.css)
            self.assertIn(f'.radio-district[data-group="{group}"]', self.css)
            self.assertIn(f'[data-group="{group}"], .radio-district[data-group="{group}"] {{ --disc: {colour};', self.css)
        self.assertIn("chip.dataset.group = tab.group;", self.script)


class FoldedHealthBarTests(unittest.TestCase):
    """The listener's details sit behind a button; one status line stays visible."""

    def setUp(self):
        self.template = read(ROOT / "templates" / "mindshare_radio.html")
        self.script = read(RADIO_JS)
        self.css = read(RADIO_CSS)

    def test_details_are_folded_by_default_and_the_status_line_is_not(self):
        body = re.search(r'<div id="radio-health-body"[^>]*>', self.template).group(0)
        self.assertIn("hidden", body)
        self.assertIn('aria-expanded="false"', self.template)
        self.assertIn('aria-controls="radio-health-body"', self.template)
        # Status line, toggle and screen-reader announcer are outside the folded part.
        folded = self.template[self.template.index('<div id="radio-health-body"'):]
        folded = folded[:folded.index('id="radio-health-announce"')]
        for marker in ('id="radio-health-status"', 'id="radio-health-toggle"'):
            self.assertIn(marker, self.template)
            self.assertNotIn(marker, folded)
        # The capture lines and queues render into radio-stats, inside the fold.
        self.assertIn('id="radio-stats"', folded)
        self.assertIn('id="radio-stats-error"', folded)

    def test_a_problem_is_visible_without_opening_the_details(self):
        for needle in (
            "Listener NOT reporting",
            "No listener heartbeat received yet",
            "undecoded audio, see details",
            "Listener status could not be loaded",
            "healthSummary(listener, state, undecoded, last);",
        ):
            self.assertIn(needle, self.script, needle)
        for tone in ("is-ok", "is-warn", "is-bad", "is-unknown"):
            self.assertIn(f".radio-health-status.{tone}", self.css, tone)

    def test_open_state_is_a_per_viewer_convenience_guarded_by_try(self):
        self.assertIn("lcdash.radio.health.open", self.script)
        self.assertGreaterEqual(self.script.count("storage may be blocked"), 2)
