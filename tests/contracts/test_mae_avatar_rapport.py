"""Rapport.cloud avatar embed on /mae/avatar, behind MAE_RAPPORT_ENABLED.

Same "flag off must be byte-for-byte today's page" contract the rest of this
project uses for dormant integrations: the widget must be a config-gated
add-on, never a required rewrite of the page every dispatcher already uses.
Three things pinned here:

1. Flag off (the shipped default): no rapport-cloud CDN URL, no
   ``<rapport-scene>`` tag, anywhere in the rendered page.
2. Flag on with a token configured: the container, the CDN script tag, and
   the token itself all render.
3. Flag on with NO token configured: treated as disabled (defensive AND in
   mae_avatar_page(), app/main.py) -- env drift between the two vars must
   not hand the browser a broken widget with an empty project-token.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import app

RAPPORT_CDN_URL = "https://cdn.rapport.cloud/rapport-web-viewer/rapport.js"


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


AVATAR = _identity("booth-demo@911logan.com", "lcdash-pilot-avatar")


class _RapportTestCase(unittest.TestCase):
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
        sign_in = patch("app.main.resolve_alb_identity", return_value=AVATAR)
        self.addCleanup(sign_in.stop)
        sign_in.start()

    def _patch_rapport(self, *, enabled: bool, token: str) -> None:
        for name, value in (
            ("mae_rapport_enabled", enabled),
            ("mae_rapport_project_token", token),
        ):
            patcher = patch.object(settings, name, value)
            self.addCleanup(patcher.stop)
            patcher.start()


class RapportFlagOffTests(_RapportTestCase):
    def test_default_settings_render_nothing_rapport_related(self):
        """The shipped default (no env override at all): dormant."""
        response = self.client.get("/mae/avatar")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertNotIn(RAPPORT_CDN_URL, body)
        self.assertNotIn("rapport-scene", body)
        self.assertNotIn("rapport-stage", body)

    def test_explicit_flag_off_renders_nothing_rapport_related(self):
        self._patch_rapport(enabled=False, token="pub-token-123")
        response = self.client.get("/mae/avatar")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertNotIn(RAPPORT_CDN_URL, body)
        self.assertNotIn("rapport-scene", body)

    def test_flag_on_with_no_token_is_treated_as_disabled(self):
        """Env drift (flag on, token blank) must fail closed, not render a
        broken widget with an empty project-token attribute."""
        self._patch_rapport(enabled=True, token="")
        response = self.client.get("/mae/avatar")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertNotIn(RAPPORT_CDN_URL, body)
        self.assertNotIn("rapport-scene", body)

    def test_rest_of_the_page_is_unchanged_when_the_flag_is_off(self):
        """Byte-for-byte-in-substance check on the surrounding markup: the
        existing renderer stage, portrait fallback, and Sumerian credit all
        still render exactly as they do without this feature existing."""
        response = self.client.get("/mae/avatar")
        body = response.text
        self.assertIn('id="avatar-stage"', body)
        self.assertIn('id="portrait-fallback"', body)
        self.assertIn("Amazon Sumerian Hosts", body)


class RapportFlagOnTests(_RapportTestCase):
    def test_flag_on_with_token_renders_container_script_and_token(self):
        self._patch_rapport(enabled=True, token="pub-token-abc123")
        response = self.client.get("/mae/avatar")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn(RAPPORT_CDN_URL, body)
        self.assertIn("<rapport-scene", body)
        self.assertIn('project-token="pub-token-abc123"', body)
        self.assertIn('id="rapport-stage"', body)

    def test_flag_on_still_keeps_the_local_renderer_and_credit(self):
        """Rapport is layered on top, not swapped in for, the local chain --
        the Sumerian model/credit stays so the per-utterance fallback in
        lcdash-mae-avatar.js has something to fall back to."""
        self._patch_rapport(enabled=True, token="pub-token-abc123")
        response = self.client.get("/mae/avatar")
        body = response.text
        self.assertIn('id="avatar-stage"', body)
        self.assertIn('id="portrait-fallback"', body)
        self.assertIn("Amazon Sumerian Hosts", body)


if __name__ == "__main__":
    unittest.main()
