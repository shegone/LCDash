"""The /callflow-cards page: the Nexis Call Flow Cards prototype.

The page is a self-contained HTML build served verbatim (FileResponse, not
Jinja), so the contract here is small: supervisors and dispatchers reach it,
the restricted ``user`` tier does not (its allowlist never listed this path --
the sweep in tests/contracts/test_sanitized_tier.py also proves that
property across the whole route table), and the served document is really
the cards build, not an error page or an accidentally-templated variant.

Roles are signed in the way production does it, by patching
``app.main.resolve_alb_identity`` to return the Cognito group claim the ALB
would have verified.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import app


def _identity(email: str, group: str) -> AlbIdentity:
    return AlbIdentity(subject=f"sub-{email}", groups=(group,), email=email)


DISPATCHER = _identity("dispatch@911logan.com", "lcdash-pilot-dispatcher")
SUPERVISOR = _identity("s@911logan.com", "lcdash-pilot-supervisor")
USER = _identity("vendor@nga911.com", "lcdash-pilot-user")


class CallflowCardsPageTests(unittest.TestCase):
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

    def _sign_in_as(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_supervisor_gets_the_cards_page(self):
        self._sign_in_as(SUPERVISOR)
        response = self.client.get("/callflow-cards")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/html"))
        # The real build, served whole: title, inlined card data, and the
        # not-for-operational-use banner it must keep until NGA911 authorizes.
        self.assertIn("Nexis Call Flow Cards", response.text)
        self.assertIn("window.NEXIS_DATA", response.text)
        self.assertIn("PENDING AGENCY AUTHORIZATION", response.text.upper())

    def test_dispatcher_gets_the_cards_page(self):
        self._sign_in_as(DISPATCHER)
        response = self.client.get("/callflow-cards")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nexis Call Flow Cards", response.text)

    def test_restricted_user_tier_is_denied(self):
        self._sign_in_as(USER)
        response = self.client.get("/callflow-cards")
        self.assertEqual(response.status_code, 403)

    def test_sidebar_links_the_page_for_dispatchers(self):
        self._sign_in_as(DISPATCHER)
        response = self.client.get("/station-alerts")
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/callflow-cards"', response.text)


if __name__ == "__main__":
    unittest.main()
