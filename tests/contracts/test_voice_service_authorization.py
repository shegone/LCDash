"""Offline trusted-context authorization tests for speech synthesis."""

import socket
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch

from app.core.county_profiles import (
    CountyProfileValidationError,
    load_builtin_county_profile,
)
from app.core.tenancy import TenantContext
from app.core.tenant_authorization import TenantAuthorizationDenied
from app.services.voice_service import synthesize_speech


def trusted_context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        subject="synthetic-voice-viewer",
        identity_source="synthetic-trusted-binding",
        roles=frozenset({"viewer"}),
        request_id=f"{tenant_id}-voice",
        authenticated_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )


class SyntheticSpeechResponse:
    content = b"synthetic-audio"
    headers = {"content-type": "audio/mpeg"}

    def raise_for_status(self):
        return None


class SyntheticHttpClient:
    def __init__(self):
        self.posts = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def post(self, url, json):
        self.posts.append((url, json))
        return SyntheticSpeechResponse()


class VoiceServiceAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.blockers = [
            patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("network access blocked"),
            ),
            patch(
                "socket.create_connection",
                side_effect=AssertionError("network access blocked"),
            ),
            patch("httpx.get", side_effect=AssertionError("HTTP access blocked")),
            patch("httpx.post", side_effect=AssertionError("HTTP access blocked")),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_service_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_trusted_logan_context_authorizes_safe_pronunciation_before_fake_http(self):
        client = SyntheticHttpClient()
        with patch("httpx.Client", return_value=client):
            content, media_type = synthesize_speech(
                "Call 911 for the synthetic test.",
                tenant_context=trusted_context("logan-synthetic"),
            )

        self.assertEqual(content, b"synthetic-audio")
        self.assertEqual(media_type, "audio/mpeg")
        self.assertEqual(
            client.posts[0][1]["input"],
            "Call nine one one for the synthetic test.",
        )
        self.assert_no_service_used()

    def test_disabled_and_cross_tenant_deny_before_preparation_or_http(self):
        logan = load_builtin_county_profile("logan-synthetic")
        disabled = replace(logan, modules=logan.modules - {"voice"})
        northstar = load_builtin_county_profile("northstar-fictional")

        for profile, error_pattern in (
            (disabled, "not enabled"),
            (northstar, "binding mismatch"),
        ):
            with self.subTest(error=error_pattern):
                with (
                    patch(
                        "app.services.voice_service.resolve_county_profile",
                        return_value=profile,
                    ),
                    patch(
                        "app.services.voice_service.prepare_text_for_speech",
                        side_effect=AssertionError("text prepared before denial"),
                    ) as prepare_mock,
                    patch(
                        "httpx.Client",
                        side_effect=AssertionError("HTTP client built before denial"),
                    ) as client_mock,
                ):
                    with self.assertRaisesRegex(
                        TenantAuthorizationDenied,
                        error_pattern,
                    ):
                        synthesize_speech(
                            "Call 911.",
                            tenant_context=trusted_context("logan-synthetic"),
                        )
                prepare_mock.assert_not_called()
                client_mock.assert_not_called()
        self.assert_no_service_used()

    def test_unknown_tenant_denies_before_preparation_or_http(self):
        with (
            patch(
                "app.services.voice_service.prepare_text_for_speech",
                side_effect=AssertionError("text prepared for unknown tenant"),
            ) as prepare_mock,
            patch(
                "httpx.Client",
                side_effect=AssertionError("HTTP client built for unknown tenant"),
            ) as client_mock,
        ):
            with self.assertRaises(CountyProfileValidationError):
                synthesize_speech(
                    "Call 911.",
                    tenant_context=trusted_context("unknown-synthetic"),
                )
        prepare_mock.assert_not_called()
        client_mock.assert_not_called()
        self.assert_no_service_used()

    def test_direct_profile_and_trusted_context_cannot_be_combined(self):
        profile = load_builtin_county_profile("logan-synthetic")

        with self.assertRaisesRegex(TenantAuthorizationDenied, "cannot be combined"):
            synthesize_speech(
                "Call 911.",
                county_profile=profile,
                tenant_context=trusted_context("logan-synthetic"),
            )
        self.assert_no_service_used()

    def test_direct_profile_and_no_context_paths_remain_compatible(self):
        profile = load_builtin_county_profile("logan-synthetic")
        for supplied_profile in (profile, None):
            with self.subTest(profile=supplied_profile is not None):
                client = SyntheticHttpClient()
                with (
                    patch("httpx.Client", return_value=client),
                    patch(
                        "app.services.voice_service.resolve_county_profile",
                        side_effect=AssertionError("legacy path must not resolve"),
                    ) as resolve_mock,
                    patch(
                        "app.services.voice_service.authorize_tenant_action",
                        side_effect=AssertionError("legacy path must not authorize"),
                    ) as authorize_mock,
                ):
                    synthesize_speech(
                        "Call 911.",
                        county_profile=supplied_profile,
                    )
                self.assertEqual(client.posts[0][1]["input"], "Call nine one one.")
                resolve_mock.assert_not_called()
                authorize_mock.assert_not_called()
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
