import unittest

from app.services.cloud_ai_streaming import sanitize_spoken_text


class SanitizeSpokenTextTimeExpansionTests(unittest.TestCase):
    # Regression test: cloud Polly speech (MAE/JACK Listen, station-alert
    # announcements) had no time-expansion at all, unlike on-prem's local
    # voice pipeline -- a compact/colon time like "13:40" was left for Polly
    # to read literally instead of the unambiguous "thirteen forty" on-prem
    # already produces. Reuses on-prem's exact spoken_24_hour_time() wording.

    def test_colon_time_is_expanded_to_unambiguous_speech(self):
        self.assertEqual(
            sanitize_spoken_text("The call came in at 13:40 today."),
            "The call came in at thirteen forty today.",
        )

    def test_midnight_and_single_digit_minute_are_expanded_correctly(self):
        self.assertEqual(sanitize_spoken_text("Dispatched at 00:00."), "Dispatched at zero zero hundred.")
        self.assertEqual(sanitize_spoken_text("Arrived at 09:05."), "Arrived at zero nine oh five.")

    def test_prefixed_dispatch_time_phrase_is_expanded(self):
        self.assertEqual(
            sanitize_spoken_text("Dispatch time is 1523 for this unit."),
            "Dispatch time is fifteen twenty-three for this unit.",
        )

    def test_source_urls_still_never_reach_polly(self):
        self.assertNotIn("http", sanitize_spoken_text("See https://example.com/report for details."))

    def test_non_time_colon_text_is_left_alone(self):
        self.assertEqual(sanitize_spoken_text("Note: check status."), "Note: check status.")


if __name__ == "__main__":
    unittest.main()
