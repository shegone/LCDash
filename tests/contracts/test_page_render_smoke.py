"""Rendering smoke test: catch a deleted block of CSS that 1217 other tests miss.

Commit cf6b799 ("restore the 186 lines of core CSS my cleanup deleted") is the
motivating incident: a CSS file lost a block in the middle, every page still
returned 200, every contract test still passed, and nothing caught it because
nothing renders a page and checks that the markup it produced is actually
styleable.

This test does that, mechanically, for every HTML page route in the app:

1. Render the page (signed in as an admin, cloud/synthetic-disconnected mode,
   the same fixture shape ``test_sanitized_tier.py`` uses).
2. Resolve every ``<link rel="stylesheet">`` on the page to a file under
   ``static/`` and assert the file exists and is not suspiciously small.
3. Extract every ``class="..."`` token actually rendered into the page and
   assert each one appears as a selector token somewhere in the combined text
   of that page's linked CSS files. A class that is legitimately not backed
   by CSS (JS-toggled state, third-party widget markup) goes in
   ``ALLOWLIST`` -- and only there once verified, never to silence a real gap.
4. Separately (not per-page), assert a small set of load-bearing selectors
   that the product depends on are present in the core stylesheet at all:
   ``.glass``, ``.incident-card``, and the priority color classes.

Route discovery walks ``app.routes`` rather than a hand-written page list, for
the same reason ``test_sanitized_tier.py``'s path gate does: a page added
later is covered automatically or the route-count assertion here catches the
sweep collapsing to nothing.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = ROOT / "static"

# --------------------------------------------------------------------------
# Route discovery
# --------------------------------------------------------------------------

# Non-page GET routes that exist on the app but are not HTML documents to
# render: JSON debug endpoints, the auto-generated API docs/schema, the
# health check, PDF/binary downloads, and routes this environment cannot
# exercise (they need credentials this test suite never configures).
NON_PAGE_ROUTES = {
    "/health",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/openapi.json",
    "/config-test",
    "/auth-test",
    "/active-calls-test",
    "/system-test",  # requires live CentralSquare credentials
    "/logout",  # a redirect/side-effect route, not a rendered page
}

# Parameterised routes worth rendering with one concrete, synthesizable id.
# Everything else with a `{param}` in its path is skipped: either it returns
# a binary payload (PDF), or a real id cannot be produced without reaching
# into services this test does not otherwise mock, and the templates those
# routes use are already exercised elsewhere in this same sweep (the plain
# /nga911* and /nga911-intelligence* pages share their CSS and most of their
# markup with the /{county_id}/{event_id} variants).
PARAMETERIZED_PAGES = {
    "/calls/{cfs_number}": "/calls/CFS26-1234",
}

# Pages served verbatim as complete, self-contained HTML documents (one inline
# <style> block, no /static/ stylesheets). The incident this suite guards -- a
# block deleted from a shared static CSS file -- cannot affect them, and the
# class extraction cannot evaluate them: their markup is built by JavaScript
# template literals, so class="..." tokens found in the raw text include
# un-rendered `${...}` fragments. They still render in the sweep; only the
# static-stylesheet and class-backing checks are skipped, replaced by an
# assertion that the inline style block is actually there and substantial.
SELF_CONTAINED_PAGES = {
    "/callflow-cards",  # Nexis Call Flow Cards build (templates/nexis_callflow_cards.html)
}


def _discover_html_pages() -> list[str]:
    paths = sorted({getattr(route, "path", "") for route in app.routes})
    pages: list[str] = []
    for path in paths:
        if not path or path.startswith("/api/") or path.startswith("/static"):
            continue
        if path in NON_PAGE_ROUTES:
            continue
        if "{" in path:
            concrete = PARAMETERIZED_PAGES.get(path)
            if concrete:
                pages.append(concrete)
            continue
        pages.append(path)
    return pages


# --------------------------------------------------------------------------
# Allowlist: classes that are legitimately not present in any stylesheet.
# --------------------------------------------------------------------------

ALLOWLIST: set[str] = {
    # JS behavior hooks: rendered server-side into class="" so the extraction
    # sees them, but never styled -- lcdash-time.js / lcdash-nga911*.js query
    # `.cad-time` / `[data-cad-time]` purely to swap in a formatted timestamp.
    # Verified: grep of every static/css/*.css file for `.cad-time` returns
    # nothing anywhere in the codebase, on purpose.
    "cad-time",
    # Structural default-state markers, verified against their own CSS to
    # carry zero override on purpose: the base class already renders the
    # "normal" appearance, and a sibling modifier class is what carries the
    # actual override.
    #   - .mae-message is the full message style (avatar left); the paired
    #     .mae-message-user variant reverses layout with flex-direction, so
    #     "assistant" (the default, no reversal) needs no rule of its own --
    #     see static/css/lcdash-mae.css around `.mae-message-user`.
    #   - .nga-live-path / .nga-console render their default "everything is
    #     fine" look unmodified; only .degraded/.critical/.ringing/etc. carry
    #     overrides in static/css/lcdash-nga911.css. "healthy" and "ready" are
    #     the un-degraded status value and never get their own selector.
    "mae-message-assistant",
    "healthy",
    "ready",
}


def _read_static(rel_path: str) -> tuple[Path, bool]:
    """Resolve a /static/... href to a file path. Returns (path, exists)."""
    file_path = STATIC_ROOT / rel_path
    return file_path, file_path.is_file()


STYLESHEET_LINK_RE = re.compile(
    r'<link\b[^>]*\brel=["\']stylesheet["\'][^>]*\bhref=["\']([^"\']+)["\']'
    r'|<link\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*\brel=["\']stylesheet["\']',
    re.IGNORECASE,
)
CLASS_ATTR_RE = re.compile(r'class=["\']([^"\']*)["\']', re.IGNORECASE)
INLINE_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)


def _extract_inline_style_css(html: str) -> str:
    return "\n".join(INLINE_STYLE_BLOCK_RE.findall(html))


def _extract_stylesheet_hrefs(html: str) -> list[str]:
    hrefs = []
    for match in STYLESHEET_LINK_RE.finditer(html):
        href = match.group(1) or match.group(2)
        if href:
            hrefs.append(href)
    return hrefs


def _extract_classes(html: str) -> set[str]:
    classes: set[str] = set()
    for match in CLASS_ATTR_RE.finditer(html):
        for token in match.group(1).split():
            token = token.strip()
            if token:
                classes.add(token)
    return classes


def _selector_present(css_text: str, class_name: str) -> bool:
    # A literal "." only ever starts a class selector token in CSS -- class
    # names cannot themselves contain a dot -- so no lookbehind is needed
    # before it. A compound selector like ".dashboard-kpi.accent-kpi" has a
    # word character immediately before the second dot; that is a legitimate
    # match for "accent-kpi" and must not be excluded. The lookahead still
    # guards the tail, so searching for "kpi" does not false-match inside
    # ".kpi-time".
    pattern = re.compile(r"\." + re.escape(class_name) + r"(?![\w-])")
    return pattern.search(css_text) is not None


class _RenderSmokeTestCase(unittest.TestCase):
    """Signed in as an admin, cloud/synthetic-disconnected, CAD mocked out."""

    def setUp(self) -> None:
        self.client = TestClient(app)
        for name, value in (
            ("deployment_mode", "synthetic-disconnected"),
            ("tenant_id", "logan-synthetic"),
            ("alb_identity_enabled", True),
            ("alb_identity_region", "us-east-1"),
            ("alb_identity_load_balancer_arn", "arn:aws:elasticloadbalancing:x"),
            ("alb_identity_user_pool_id", "us-east-1_Example1"),
            ("alb_identity_client_id", "1example23456789"),
        ):
            patcher = patch.object(settings, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()

        identity = AlbIdentity(
            subject="sub-tedsparks@911logan.com",
            groups=("lcdash-pilot-admin",),
            email="tedsparks@911logan.com",
        )
        sign_in = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(sign_in.stop)
        sign_in.start()

        # Admin sees the full (unsanitized) app; take the cloud CAD bridge
        # path everywhere it is consulted so call-detail (and anything else
        # gated the same way) renders instead of trying to reach CentralSquare.
        bridge = patch("app.main._cloud_cad_bridge_enabled", return_value=True)
        self.addCleanup(bridge.stop)
        bridge.start()


class PageRenderSmokeTests(_RenderSmokeTestCase):
    """Each page rendered and cached exactly once for the whole test class."""

    _pages_html: dict[str, str] = {}
    _css_cache: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._pages_html = {}
        cls._css_cache = {}

    def _render(self, path: str) -> str:
        if path in self._pages_html:
            return self._pages_html[path]
        response = self.client.get(path)
        self.assertEqual(
            response.status_code, 200, f"{path} did not render (got {response.status_code})"
        )
        self.assertIn("text/html", response.headers.get("content-type", ""))
        html = response.text
        self._pages_html[path] = html
        return html

    def _css_text(self, rel_path: str) -> str:
        if rel_path not in self._css_cache:
            file_path, exists = _read_static(rel_path)
            self._css_cache[rel_path] = (
                file_path.read_text(encoding="utf-8", errors="replace") if exists else ""
            )
        return self._css_cache[rel_path]

    def test_route_discovery_found_a_real_set_of_pages(self):
        pages = _discover_html_pages()
        self.assertGreater(
            len(pages), 25, "page discovery collapsed -- the sweep proves nothing"
        )

    def test_every_page_renders_and_its_stylesheets_exist(self):
        pages = _discover_html_pages()
        for path in pages:
            with self.subTest(path=path):
                html = self._render(path)
                if path in SELF_CONTAINED_PAGES:
                    inline_css = _extract_inline_style_css(html)
                    self.assertGreater(
                        len(inline_css),
                        500,
                        f"{path} is registered self-contained but carries no real inline CSS",
                    )
                    continue
                hrefs = _extract_stylesheet_hrefs(html)
                self.assertTrue(hrefs, f"{path} links no stylesheets at all")
                for href in hrefs:
                    clean = href.split("?", 1)[0].split("#", 1)[0]
                    self.assertTrue(
                        clean.startswith("/static/"),
                        f"{path} links a stylesheet outside /static/: {href}",
                    )
                    rel_path = clean[len("/static/"):]
                    file_path, exists = _read_static(rel_path)
                    self.assertTrue(exists, f"{path} links missing stylesheet {href}")
                    size = file_path.stat().st_size
                    self.assertGreater(
                        size, 20, f"{path}'s stylesheet {href} is suspiciously tiny ({size} bytes)"
                    )

    def test_every_rendered_class_is_backed_by_css(self):
        pages = _discover_html_pages()
        failures: list[str] = []
        for path in pages:
            if path in SELF_CONTAINED_PAGES:
                continue
            html = self._render(path)
            hrefs = _extract_stylesheet_hrefs(html)
            rel_paths = [
                href.split("?", 1)[0].split("#", 1)[0][len("/static/"):]
                for href in hrefs
                if href.split("?", 1)[0].split("#", 1)[0].startswith("/static/")
            ]
            combined_css = "\n".join(self._css_text(rel) for rel in rel_paths)
            combined_css += "\n" + _extract_inline_style_css(html)
            classes = _extract_classes(html) - ALLOWLIST
            for class_name in sorted(classes):
                if not _selector_present(combined_css, class_name):
                    failures.append(f"{path}: .{class_name}")

        if failures:
            self.fail(
                "classes rendered with no matching CSS selector "
                "(real gap, or missing ALLOWLIST entry):\n  " + "\n  ".join(failures)
            )


class LoadBearingSelectorTests(unittest.TestCase):
    """Selectors the product depends on, checked directly against the CSS.

    Independent of page-derived class extraction above: these must exist in
    the core stylesheet regardless of what any single rendered page happens
    to use, because losing them silently degrades every incident card on
    every page at once -- exactly what cf6b799 was.
    """

    def test_load_bearing_selectors_exist(self):
        core_css = (STATIC_ROOT / "css" / "lcdash-core.css").read_text(encoding="utf-8")
        for selector in (
            "glass",
            "incident-card",
            "priority-5",
            "priority-10",
            "priority-15",
            "priority-20",
            "priority-30",
        ):
            with self.subTest(selector=selector):
                self.assertTrue(
                    _selector_present(core_css, selector),
                    f".{selector} is missing from lcdash-core.css",
                )


if __name__ == "__main__":
    unittest.main()
