"""Radio "possible noise" flag: the pure rule, the listed field, alert handling.

The flag is display only. It never alters or removes transcript text, and it is
never true for a human-corrected segment. Synthetic data only.
"""

from __future__ import annotations

import unittest

from app.services import radio_text
from app.services.radio_text import is_possible_noise

from .test_radio_corrections import (
    ACTOR,
    add_fix,
    add_term,
    alert_phrases,
    listed,
)
from .test_radio_routes import SEGMENT_ID, build_service, ingest_record

SHORT_START = "2026-10-16T14:05:00+00:00"
SHORT_END = "2026-10-16T14:05:01+00:00"  # 1.0 s
LOW_SEGMENTS = [{"start": 0, "end": 1, "text": "x",
                 "avg_logprob": -1.4, "no_speech_prob": 0.1}]


def short_clip(service, transcript, *, segment_id=SEGMENT_ID, low=False):
    """Ingest a 1-second synthetic clip, optionally low confidence."""
    overrides = {
        "segment_id": segment_id,
        "transcript": transcript,
        "started_at": SHORT_START,
        "ended_at": SHORT_END,
    }
    if low:
        overrides["segments"] = LOW_SEGMENTS
    record, status = ingest_record(service, **overrides)
    assert status == "created", status
    return record


class BoilerplatePhraseTests(unittest.TestCase):
    def flagged(self, text, duration=10.0):
        # Long, confident clip: only the phrase rule can flag it.
        return is_possible_noise(text, duration, False)

    def test_the_documented_examples_are_flagged_on_a_long_clip(self):
        for text in (
            "Thanks for watching!",
            "Thank you for watching.",
            "Bye.",
            "Beep.",
            "Transcription by CastingWords",
            "Subtitles by the Amara.org community",
            "Subtitle Workshop",
            "TAMARA KEITH, CBC NEWS ANCHOR...",
            "Thank you.",
            "Thanks.",
            "you",
            "Bye bye",
            "Music",
            "Applause",
            "Captioned by Media Access Group",
            "amara.org",
            "Like and subscribe",
            "See you next time",
        ):
            self.assertTrue(self.flagged(text), text)

    def test_case_punctuation_and_spacing_variants_still_match(self):
        for text in (
            "  THANK YOU!!  ",
            "thank   you",
            "Thank-you.",
            "bye, bye",
            "...Beep...",
            "(Music)",
            "[applause]",
            "\tYou.\n",
            "SUBTITLES BY THE AMARA.ORG COMMUNITY",
            "Transcription  by   CastingWords",
        ):
            self.assertTrue(self.flagged(text), repr(text))

    def test_real_traffic_is_not_flagged(self):
        for text in (
            "10-4 thank you",
            "Go for 50",
            "Medic 4 en route",
            "Thank you, medic 4 is on scene",
            "Roger, thanks for the update",
            "Bye for now from unit 12",
            "Unit 5 you are clear",
            "Music Street and Elm",
            "Captioned",
            "Subtitles",
            "Transcription needed on Elm",
            "Say again you broke up",
        ):
            self.assertFalse(self.flagged(text), text)

    def test_prefix_family_needs_a_word_boundary(self):
        self.assertFalse(self.flagged("Subtitles byte count"))
        self.assertFalse(self.flagged("Tamarack Road"))
        self.assertTrue(self.flagged("Subtitles by Someone"))

    def test_a_substring_is_not_enough(self):
        self.assertFalse(self.flagged("Thanks for watching the door"))
        self.assertFalse(self.flagged("Beep beep beep, unit 9"))
        self.assertFalse(self.flagged("Thank you for watching my back"))

    def test_the_phrase_lists_are_normalized_and_nonempty(self):
        self.assertTrue(radio_text.NOISE_EXACT_PHRASES)
        self.assertTrue(radio_text.NOISE_PREFIX_PHRASES)
        for phrase in radio_text.NOISE_EXACT_PHRASES:
            self.assertEqual(phrase, phrase.lower())
        for phrase in ("thank you", "thanks", "you", "bye", "bye bye", "beep",
                       "music", "applause", "like and subscribe",
                       "see you next time", "thanks for watching",
                       "thank you for watching"):
            self.assertIn(phrase, radio_text.NOISE_EXACT_PHRASES)
        for phrase in ("subtitles by", "transcription by", "captioned by",
                       "amara.org"):
            self.assertIn(phrase, radio_text.NOISE_PREFIX_PHRASES)


class ShortLowConfidenceTests(unittest.TestCase):
    def test_short_few_words_low_confidence_is_flagged(self):
        self.assertTrue(is_possible_noise("mumble", 1.0, True))
        self.assertTrue(is_possible_noise("unit four go", 1.49, True))
        self.assertTrue(is_possible_noise("a b c", 0.2, True))

    def test_each_condition_is_required(self):
        self.assertFalse(is_possible_noise("mumble", 1.0, False))  # confident
        self.assertFalse(is_possible_noise("mumble", 1.5, True))   # boundary: not under
        self.assertFalse(is_possible_noise("mumble", 3.0, True))   # long
        self.assertFalse(is_possible_noise("one two three four", 1.0, True))  # 4 words

    def test_a_six_word_one_second_clip_is_not_flagged(self):
        self.assertFalse(is_possible_noise("unit four go ahead and copy", 1.0, True))
        self.assertFalse(is_possible_noise("unit four go ahead and copy", 1.0, False))

    def test_empty_text_is_never_noise(self):
        for text in ("", "   ", "\n", None):
            self.assertFalse(is_possible_noise(text, 0.5, True), repr(text))
            self.assertFalse(is_possible_noise(text, 10.0, False), repr(text))

    def test_a_bad_duration_does_not_raise(self):
        self.assertFalse(is_possible_noise("mumble", None, True))
        self.assertFalse(is_possible_noise("mumble", "n/a", True))
        self.assertTrue(is_possible_noise("Bye.", None, False))


class CorrectedIsNeverNoiseTests(unittest.TestCase):
    def test_corrected_segments_are_never_flagged(self):
        self.assertFalse(is_possible_noise("Thanks for watching!", 0.5, True, corrected=True))
        self.assertFalse(is_possible_noise("Bye.", 10.0, False, corrected=True))
        self.assertFalse(is_possible_noise("mumble", 1.0, True, corrected=True))

    def test_the_default_is_not_corrected(self):
        self.assertTrue(is_possible_noise("Bye.", 10.0, False))


class ListedFieldTests(unittest.TestCase):
    def test_every_listed_item_carries_a_boolean_possible_noise(self):
        service, _, _ = build_service()
        short_clip(service, "Go for 50")
        item = listed(service)[SEGMENT_ID]
        self.assertIn("possible_noise", item)
        self.assertIs(item["possible_noise"], False)

    def test_boilerplate_is_flagged_and_the_text_is_untouched(self):
        service, _, _ = build_service()
        short_clip(service, "Thanks for watching!")
        item = listed(service)[SEGMENT_ID]
        self.assertIs(item["possible_noise"], True)
        self.assertEqual(item["text"], "Thanks for watching!")
        self.assertEqual(item["machine_transcript"], "Thanks for watching!")

    def test_a_short_low_confidence_clip_is_flagged(self):
        service, _, _ = build_service()
        short_clip(service, "mmm", low=True)
        item = listed(service)[SEGMENT_ID]
        self.assertTrue(item["low_confidence"])
        self.assertTrue(item["possible_noise"])

    def test_a_correction_clears_the_flag_and_a_revert_restores_it(self):
        service, _, _ = build_service()
        short_clip(service, "Bye.")
        self.assertTrue(listed(service)[SEGMENT_ID]["possible_noise"])
        corrected = service.correct_segment(SEGMENT_ID, "Bye.", actor=ACTOR)
        self.assertIs(corrected["possible_noise"], False)
        self.assertIs(listed(service)[SEGMENT_ID]["possible_noise"], False)
        reverted = service.revert_correction(SEGMENT_ID, actor=ACTOR)
        self.assertIs(reverted["possible_noise"], True)

    def test_the_flag_follows_the_displayed_text(self):
        service, _, _ = build_service()
        add_fix(service, "bye", "Unit Bravo copies the traffic")
        short_clip(service, "bye")
        item = listed(service)[SEGMENT_ID]
        self.assertEqual(item["text"], "Unit Bravo copies the traffic")
        self.assertFalse(item["possible_noise"])


class AlertSuppressionTests(unittest.TestCase):
    def test_boilerplate_noise_raises_no_alert(self):
        service, store, _ = build_service()
        add_term(service, "watching")
        short_clip(service, "Thanks for watching!")
        self.assertEqual(alert_phrases(service), [])
        self.assertEqual(store.alerts, [])
        # Stored and listed as usual.
        self.assertIn(SEGMENT_ID, listed(service))

    def test_short_low_confidence_noise_raises_no_alert(self):
        service, _, _ = build_service()
        add_term(service, "fire")
        short_clip(service, "fire", low=True)
        self.assertEqual(alert_phrases(service), [])

    def test_the_same_words_in_normal_traffic_still_alert(self):
        service, _, _ = build_service()
        add_term(service, "fire")
        short_clip(service, "fire", low=False)  # short but confident
        self.assertEqual(alert_phrases(service), ["fire"])

    def test_normal_text_still_alerts(self):
        service, _, _ = build_service()
        add_term(service, "structure fire")
        ingest_record(service, transcript="Dispatch reports a structure fire on Elm.")
        self.assertEqual(alert_phrases(service), ["structure fire"])

    def test_a_correction_of_a_noise_segment_still_alerts(self):
        service, _, _ = build_service()
        add_term(service, "structure fire")
        short_clip(service, "Thank you.", low=True)
        self.assertEqual(alert_phrases(service), [])
        service.correct_segment(SEGMENT_ID, "Structure fire at 5 Elm.", actor=ACTOR)
        self.assertEqual(alert_phrases(service), ["structure fire"])

    def test_the_stored_transcript_is_unchanged(self):
        service, store, _ = build_service()
        short_clip(service, "Subtitles by the Amara.org community")
        self.assertEqual(
            listed(service)[SEGMENT_ID]["machine_transcript"],
            "Subtitles by the Amara.org community",
        )


if __name__ == "__main__":
    unittest.main()
