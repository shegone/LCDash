"""Stored attribution must come from the verified assertion, not a header.

``_authenticated_user_email`` feeds ten call sites that write a name into the
database: report ``author_subject``, saved-widget ``created_by``, JACK memory
``created_by``/``reviewed_by``, and the MAE and JACK audit rows.

It used to read ``x-forwarded-user``, ``x-auth-request-email``, and
``cf-access-authenticated-user-email`` directly. Those are safe behind an
on-prem front end that overwrites client-supplied copies (Cloudflare Access,
oauth2-proxy). The pilot's ALB does neither -- it asserts identity in
``x-amzn-oidc-*`` and passes these three through untouched -- so any client
could name itself as the author of a stored report. That was unreachable while
report writes sat behind a hardcoded role, and became reachable the moment
``AlbIdentityEnabled`` was turned on.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.core.alb_identity import AlbIdentity
from app.main import _authenticated_user_email


_FORGEABLE_HEADERS = {
    "x-forwarded-user": "attacker@example.com",
    "x-auth-request-email": "attacker@example.com",
    "cf-access-authenticated-user-email": "attacker@example.com",
}


def _probe_app() -> FastAPI:
    probe = FastAPI()

    @probe.get("/who")
    def who(request: Request):
        return {"attributed_to": _authenticated_user_email(request)}

    return probe


class AttributionIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(_probe_app())
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

    def _with_identity(self, identity):
        patcher = patch("app.main.resolve_alb_identity", return_value=identity)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_verified_email_is_used(self):
        self._with_identity(
            AlbIdentity(
                subject="sub-1",
                groups=("lcdash-pilot-supervisor",),
                email="ted@911logan.com",
            )
        )
        body = self.client.get("/who").json()

        self.assertEqual(body["attributed_to"], "ted@911logan.com")

    def test_forged_headers_cannot_override_the_verified_identity(self):
        """The header must lose to the assertion, not win over it."""
        self._with_identity(
            AlbIdentity(
                subject="sub-1",
                groups=("lcdash-pilot-supervisor",),
                email="ted@911logan.com",
            )
        )
        body = self.client.get("/who", headers=_FORGEABLE_HEADERS).json()

        self.assertEqual(body["attributed_to"], "ted@911logan.com")
        self.assertNotIn("attacker", body["attributed_to"])

    def test_forged_headers_alone_attribute_to_nobody(self):
        """No verified identity means no name, never the claimed one."""
        self._with_identity(None)
        body = self.client.get("/who", headers=_FORGEABLE_HEADERS).json()

        self.assertEqual(body["attributed_to"], "")

    def test_each_forgeable_header_is_ignored_individually(self):
        self._with_identity(None)
        for header, value in _FORGEABLE_HEADERS.items():
            with self.subTest(header=header):
                body = self.client.get("/who", headers={header: value}).json()
                self.assertEqual(body["attributed_to"], "")

    def test_subject_is_used_when_the_assertion_carries_no_email(self):
        self._with_identity(AlbIdentity(subject="sub-2", groups=("lcdash-pilot-user",)))
        body = self.client.get("/who", headers=_FORGEABLE_HEADERS).json()

        self.assertEqual(body["attributed_to"], "sub-2")

    def test_onprem_front_end_headers_still_work_when_identity_is_off(self):
        """On-prem runs behind a proxy that does overwrite these; keep it."""
        with patch.object(settings, "alb_identity_enabled", False):
            body = self.client.get(
                "/who", headers={"x-forwarded-user": "dispatcher@911logan.com"}
            ).json()

        self.assertEqual(body["attributed_to"], "dispatcher@911logan.com")

    def test_onprem_default_is_unchanged_with_no_headers(self):
        with patch.object(settings, "alb_identity_enabled", False):
            body = self.client.get("/who").json()

        self.assertEqual(body["attributed_to"], "local-session")


if __name__ == "__main__":
    unittest.main()
