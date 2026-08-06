"""Offline Package 2B tests for profile-driven pronunciation."""

import socket
import unittest
from dataclasses import replace
from unittest.mock import patch

from app.core.county_profiles import load_builtin_county_profile
from app.services.voice_service import prepare_text_for_speech


class CountyProfileVoiceConfigurationTests(unittest.TestCase):
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
            patch("httpx.Client", side_effect=AssertionError("HTTP access blocked")),
        ]
        self.blocked_mocks = [blocker.start() for blocker in self.blockers]
        self.addCleanup(self._stop_blockers)

    def _stop_blockers(self):
        for blocker in reversed(self.blockers):
            blocker.stop()

    def assert_no_service_used(self):
        for blocked_mock in self.blocked_mocks:
            blocked_mock.assert_not_called()

    def test_default_pronunciation_preserves_inherited_behavior(self):
        prepared = prepare_text_for_speech("NGA911 protects 9-1-1 calls.")

        self.assertEqual(
            prepared,
            "N G A nine one one protects nine one one calls.",
        )
        self.assert_no_service_used()

    def test_both_synthetic_profiles_supply_safe_pronunciation(self):
        for name in ("logan-synthetic", "northstar-fictional"):
            with self.subTest(profile=name):
                profile = load_builtin_county_profile(name)
                prepared = prepare_text_for_speech(
                    "Call 911 for the synthetic demonstration.",
                    profile,
                )
                self.assertEqual(
                    prepared,
                    "Call nine one one for the synthetic demonstration.",
                )

        self.assert_no_service_used()

    def test_unvalidated_in_memory_pronunciation_fails_closed(self):
        profile = load_builtin_county_profile("logan-synthetic")
        unsafe_profile = replace(
            profile,
            voice_profile={
                **dict(profile.voice_profile),
                "pronunciation_911": "nine hundred eleven",
            },
        )

        with self.assertRaisesRegex(ValueError, "nine one one"):
            prepare_text_for_speech("Call 911.", unsafe_profile)
        self.assert_no_service_used()


if __name__ == "__main__":
    unittest.main()
