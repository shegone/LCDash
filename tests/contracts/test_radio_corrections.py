"""Mindshare radio v2: corrections, fixes, watch terms, keyword alerts.

Contract under test: docs/MINDSHARE_RADIO_CORRECTIONS.md. Offline and
synthetic, on the in-memory fakes from test_radio_routes.py (a fake S3 and a
repository that reproduces the real one's transaction semantics, so "no audit
record, no change" is provable for every write).

What is proved:

* fixes: literal, case-insensitive, whole-word, longest first, one pass, no
  regular expressions, cached 60 s, never applied on top of a human
  correction, never altering the stored machine transcript;
* corrections: S3 copy to the corrected prefix on the first correction, a
  365-day retain_until extended on every edit, append-only history, revert,
  audio proxy on both prefixes, audio_available rules, retention-aware purge;
* watch terms and alerts: validation, matching at ingest and on correction,
  one alert per (segment, term), shared dismissal, and the rule that a
  matching failure can never fail an ingest or a correction;
* every write is audited in the same transaction and fails closed.
"""

from __future__ import annotations

import time
import unittest
from datetime import timedelta

import psycopg

from app.core import radio_access

from app.services import radio_text
from app.services.radio_service import (
    RadioConflictError,
    RadioDatabaseError,
    RadioNotFoundError,
    RadioQueryError,
    RadioService,
    RadioStorageError,
)
from app.services.radio_text import FixRule, FixSet

from .test_radio_routes import (
    ADMIN,
    ADMIN_EMAIL,
    DENIED_CALLERS,
    FIXED_NOW,
    HUMAN_ROLES,
    NON_ADMIN_HUMANS,
    SEGMENT_ID,
    FakeCursor,
    FakeS3,
    _identity,
    _RouteCase,
    build_service,
    encode,
    ingest_record,
    make_metadata,
    make_wav,
    repository_with,
    sign,
)

ACTOR = "reader@911logan.com"
OTHER_ACTOR = "second@911logan.com"


def seed(service, segment_id=SEGMENT_ID, **overrides):
    """Ingest one synthetic segment and return its id."""
    record, status = ingest_record(service, segment_id=segment_id, **overrides)
    assert status == "created", status
    return record.segment_id


def listed(service, **filters):
    return {
        item["segment_id"]: item
        for item in service.list_segments(actor=ACTOR, **filters)["segments"]
    }


def add_fix(service, find, replace, **extra):
    return service.add_replacement(
        {"find": find, "replace": replace, **extra}, actor=ADMIN_EMAIL
    )


def add_term(service, phrase, **extra):
    return service.add_watch_term({"phrase": phrase, **extra}, actor=ADMIN_EMAIL)


def audit_types(store):
    return [event["event_type"] for event in store.audit]


# ============================================================= text rules


class FixRuleTests(unittest.TestCase):
    def apply(self, text, *rules):
        return FixSet(FixRule(*rule) for rule in rules).apply(text)

    def test_whole_word_does_not_touch_the_inside_of_a_longer_word(self):
        self.assertEqual(
            self.apply("log in the login page", ("log in", "Logan", True)),
            "Logan the login page",
        )
        self.assertEqual(
            self.apply("a catalog and a cat", ("cat", "dog", True)),
            "a catalog and a dog",
        )

    def test_without_whole_word_a_rule_matches_inside_words(self):
        self.assertEqual(self.apply("catalog", ("cat", "dog", False)), "dogalog")

    def test_matching_is_case_insensitive_and_the_replacement_is_literal_case(self):
        self.assertEqual(
            self.apply("LOG IN and Log In and log in", ("log in", "Logan", True)),
            "Logan and Logan and Logan",
        )

    def test_the_longest_find_text_wins_when_rules_overlap(self):
        rules = (("unit", "U", True), ("unit five", "Engine 5", True))
        self.assertEqual(self.apply("unit five and unit", *rules), "Engine 5 and U")
        # Order of definition does not matter.
        self.assertEqual(self.apply("unit five and unit", *reversed(rules)), "Engine 5 and U")

    def test_rules_never_cascade_one_pass_only(self):
        rules = (("a", "b", True), ("b", "c", True))
        self.assertEqual(self.apply("a b", *rules), "b c")

    def test_rules_are_literal_text_never_regular_expressions(self):
        self.assertEqual(self.apply("abc a.c", ("a.c", "X", False)), "abc X")
        self.assertEqual(self.apply("price $5 (a+)+$", ("(a+)+$", "ok", False)), "price $5 ok")
        # A backslash group reference in the replacement is just characters.
        self.assertEqual(self.apply("foo", ("foo", r"\1 \g<0>", True)), r"\1 \g<0>")

    def test_a_hostile_rule_cannot_cause_catastrophic_backtracking(self):
        text = "a" * 20000 + "!"
        fixes = FixSet([FixRule("(a+)+$", "X", True), FixRule("a" * 79 + "b", "Y", False)])
        started = time.perf_counter()
        self.assertEqual(fixes.apply(text), text)
        self.assertLess(time.perf_counter() - started, 2.0)

    def test_rules_with_punctuation_at_the_edges_still_mean_what_they_say(self):
        self.assertEqual(self.apply("copy 10-4 now", ("10-4", "ten four", True)), "copy ten four now")
        self.assertEqual(self.apply("(x) and (x)y", ("(x)", "X", True)), "X and (x)y")

    def test_whitespace_in_a_rule_matches_any_whitespace_run(self):
        self.assertEqual(
            self.apply("log\n  in", ("log in", "Logan", True)), "Logan"
        )

    def test_an_empty_set_and_empty_text_are_untouched(self):
        self.assertEqual(FixSet([]).apply("anything"), "anything")
        self.assertEqual(self.apply("", ("a", "b", True)), "")

    def test_a_replacement_may_delete_a_word(self):
        self.assertEqual(self.apply("uh unit uh", ("uh", "", True)), " unit ")

    def test_at_most_200_rules_are_compiled(self):
        rules = [FixRule(f"w{i:04d}", "x", True) for i in range(250)]
        self.assertEqual(FixSet(rules).rule_count, 200)


class TextValidationTests(unittest.TestCase):
    def test_correction_text_rules(self):
        clean = radio_text.clean_correction_text
        self.assertEqual(clean("  Unit 4 clear.  "), "Unit 4 clear.")
        self.assertEqual(clean("a\tb\nc"), "a\tb\nc")
        self.assertEqual(clean("a\r\nb"), "a\nb")
        self.assertEqual(len(clean("x" * 20000)), 20000)
        for bad in ("", "   ", "x" * 20001, "a\x00b", "a\x07b", "a\rb", "a\x7fb",
                    "a‮b", None, 5, ["x"]):
            with self.subTest(bad=repr(bad)[:20]):
                with self.assertRaises(radio_text.TextRuleError):
                    clean(bad)

    def test_single_line_values(self):
        line = radio_text.clean_single_line
        self.assertEqual(line(" ab ", label="p", minimum=2, maximum=5), "ab")
        for bad in ("a", "abcdef", "a\nb", "a\tb", "a\x00", 3):
            with self.subTest(bad=repr(bad)):
                with self.assertRaises(radio_text.TextRuleError):
                    line(bad, label="p", minimum=2, maximum=5)

    def test_channels(self):
        self.assertEqual(radio_text.clean_channels(None), [])
        self.assertEqual(radio_text.clean_channels(["law", "law", "ems"]), ["law", "ems"])
        for bad in ("law", ["LAW"], [""], [3], ["a" * 33], ["x"] * 17, {"a": 1}):
            with self.subTest(bad=repr(bad)[:30]):
                with self.assertRaises(radio_text.TextRuleError):
                    radio_text.clean_channels(bad)

    def test_snippet_is_capped_and_flattened(self):
        self.assertEqual(radio_text.make_snippet("a  b\n c"), "a b c")
        long = radio_text.make_snippet("word " * 200, 300)
        self.assertLessEqual(len(long), 300)
        self.assertTrue(long.endswith("…"))

    def test_phrase_matching_is_literal_whole_word_and_case_insensitive(self):
        match = lambda phrase, text: radio_text.find_match(radio_text.compile_phrase(phrase), text)
        self.assertTrue(match("structure fire", "Reported STRUCTURE   FIRE on Elm"))
        self.assertTrue(match("fire", "a fire."))
        self.assertFalse(match("fire", "fireworks"))
        self.assertFalse(match("fire", "bonfire"))
        self.assertTrue(match("10-33", "code 10-33 now"))
        self.assertFalse(match("a.c", "abc"))
        self.assertTrue(match("(a+)+", "see (a+)+ here"))
        self.assertFalse(match("fire", ""))
        started = time.perf_counter()
        match("(a+)+$", "a" * 20000 + "!")
        self.assertLess(time.perf_counter() - started, 2.0)

    def test_channel_filter(self):
        applies = radio_text.term_applies_to_channel
        self.assertTrue(applies([], "law"))
        self.assertTrue(applies(None, "law"))
        self.assertTrue(applies(["law", "ems"], "ems"))
        self.assertFalse(applies(["law"], "fire"))


# ================================================================== fixes


class FixServiceTests(unittest.TestCase):
    def test_fixes_apply_at_read_time_and_never_alter_the_stored_transcript(self):
        service, store, _ = build_service()
        seed(service, transcript="Unit four to log in county, copy.")
        add_fix(service, "log in", "Logan")
        item = listed(service)[SEGMENT_ID]
        self.assertEqual(item["text"], "Unit four to Logan county, copy.")
        self.assertEqual(item["transcript"], item["text"])  # v1 alias
        self.assertEqual(item["machine_transcript"], "Unit four to log in county, copy.")
        self.assertFalse(item["corrected"])
        self.assertEqual(store.segments[SEGMENT_ID][6], "Unit four to log in county, copy.")

    def test_fixes_never_apply_on_top_of_a_human_correction(self):
        service, _, _ = build_service()
        seed(service, transcript="Unit four to log in county.")
        add_fix(service, "log in", "Logan")
        service.correct_segment(SEGMENT_ID, "Unit four to log in county.", actor=ACTOR)
        item = listed(service)[SEGMENT_ID]
        self.assertEqual(item["text"], "Unit four to log in county.")
        self.assertTrue(item["corrected"])

    def test_the_rules_are_cached_for_60_seconds(self):
        now = [1000.0]
        service, store, _ = build_service(clock=lambda: now[0])
        seed(service, transcript="log in")
        service.list_segments(actor=ACTOR)
        service.list_segments(actor=ACTOR)
        self.assertEqual(store.replacement_reads, 1)
        # A rule written behind the service's back appears only after expiry.
        store.replacements.append({
            "id": 99, "find": "log in", "replace": "Logan", "whole_word": True,
            "created_by": "x", "created_at": FIXED_NOW,
        })
        now[0] += 59
        self.assertEqual(listed(service)[SEGMENT_ID]["text"], "log in")
        now[0] += 2
        self.assertEqual(listed(service)[SEGMENT_ID]["text"], "Logan")
        self.assertEqual(store.replacement_reads, 2)

    def test_adding_or_removing_a_fix_takes_effect_in_this_process_immediately(self):
        service, _, _ = build_service(clock=lambda: 1000.0)
        seed(service, transcript="log in")
        self.assertEqual(listed(service)[SEGMENT_ID]["text"], "log in")
        rule = add_fix(service, "log in", "Logan")
        self.assertEqual(listed(service)[SEGMENT_ID]["text"], "Logan")
        service.remove_replacement(rule["replacement_id"], actor=ADMIN_EMAIL)
        self.assertEqual(listed(service)[SEGMENT_ID]["text"], "log in")

    def test_add_validation(self):
        service, store, _ = build_service()
        cases = {
            "empty find": {"find": "", "replace": "x"},
            "blank find": {"find": "   ", "replace": "x"},
            "missing find": {"replace": "x"},
            "find too long": {"find": "a" * 81, "replace": "x"},
            "find equals replace": {"find": "same", "replace": "same"},
            "replace too long": {"find": "a", "replace": "b" * 201},
            "find control char": {"find": "a\x00b", "replace": "x"},
            "find newline": {"find": "a\nb", "replace": "x"},
            "replace not a string": {"find": "a", "replace": 5},
            "whole_word not a bool": {"find": "a", "replace": "b", "whole_word": "yes"},
            "unknown key": {"find": "a", "replace": "b", "regex": True},
        }
        for label, body in cases.items():
            with self.subTest(label):
                with self.assertRaises(RadioQueryError):
                    service.add_replacement(body, actor=ADMIN_EMAIL)
        for not_object in (None, [], "x", 5):
            with self.subTest(body=repr(not_object)):
                with self.assertRaises(RadioQueryError):
                    service.add_replacement(not_object, actor=ADMIN_EMAIL)
        self.assertEqual(store.replacements, [])
        self.assertEqual(store.audit, [])

    def test_find_at_the_limit_and_empty_replace_are_accepted(self):
        service, _, _ = build_service()
        self.assertEqual(add_fix(service, "a" * 80, "")["replace"], "")

    def test_duplicates_are_rejected_case_insensitively(self):
        service, store, _ = build_service()
        add_fix(service, "Log In", "Logan")
        with self.assertRaises(RadioConflictError):
            add_fix(service, "log in", "Other")
        self.assertEqual(len(store.replacements), 1)

    def test_at_most_200_rules(self):
        service, store, _ = build_service()
        for index in range(200):
            add_fix(service, f"word{index}", "x")
        with self.assertRaises(RadioConflictError):
            add_fix(service, "one more", "x")
        self.assertEqual(len(store.replacements), 200)

    def test_add_and_remove_are_audited_and_listed(self):
        service, store, _ = build_service()
        rule = add_fix(service, "log in", "Logan", whole_word=False)
        self.assertEqual(rule["find"], "log in")
        self.assertFalse(rule["whole_word"])
        self.assertEqual(rule["created_by"], ADMIN_EMAIL)
        listing = service.list_replacements()
        self.assertEqual([r["replacement_id"] for r in listing["replacements"]], [rule["replacement_id"]])
        self.assertEqual(listing["max_rules"], 200)
        service.remove_replacement(str(rule["replacement_id"]), actor=ADMIN_EMAIL)
        self.assertEqual(audit_types(store), ["radio.fix.add", "radio.fix.remove"])
        self.assertEqual(store.audit[0]["actor"], ADMIN_EMAIL)
        self.assertEqual(service.list_replacements()["replacements"], [])

    def test_removing_an_unknown_or_malformed_id_is_not_found(self):
        service, store, _ = build_service()
        for bad in ("999", "0", "-1", "abc", "1.5", "", "1" * 30):
            with self.subTest(bad=bad):
                with self.assertRaises(RadioNotFoundError):
                    service.remove_replacement(bad, actor=ADMIN_EMAIL)
        self.assertEqual(store.audit, [])

    def test_a_failed_audit_write_means_no_change(self):
        service, store, _ = build_service()
        store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            add_fix(service, "log in", "Logan")
        self.assertEqual(store.replacements, [])
        store.fail_audit = False
        rule = add_fix(service, "log in", "Logan")
        store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            service.remove_replacement(rule["replacement_id"], actor=ADMIN_EMAIL)
        self.assertEqual(len(store.replacements), 1)


# ============================================================ corrections


class CorrectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.service, self.store, self.s3 = build_service()
        seed(self.service, transcript="unit four log in county")
        self.normal_key = "radio/audio/law/2026/10/16/law_rx_20261016_140500_0001.wav"
        self.corrected_key = "radio/corrected/law/2026/10/16/law_rx_20261016_140500_0001.wav"

    def row(self):
        return self.store.segments[SEGMENT_ID]

    def test_the_first_correction_copies_the_audio_to_the_corrected_prefix(self):
        item = self.service.correct_segment(SEGMENT_ID, "Unit four, Logan County.", actor=ACTOR)
        self.assertEqual(len(self.s3.copy_calls), 1)
        copy = self.s3.copy_calls[0]
        self.assertEqual(copy["Bucket"], "test-content-bucket")
        self.assertEqual(copy["Key"], self.corrected_key)
        self.assertEqual(copy["CopySource"], {"Bucket": "test-content-bucket", "Key": self.normal_key})
        self.assertEqual(copy["ServerSideEncryption"], "AES256")
        self.assertEqual(self.row()[10], self.corrected_key)
        self.assertIn(self.corrected_key, self.s3.objects)
        self.assertEqual(item["text"], "Unit four, Logan County.")
        self.assertEqual(item["transcript"], item["text"])
        self.assertEqual(item["machine_transcript"], "unit four log in county")
        self.assertTrue(item["corrected"])
        self.assertEqual(item["corrected_by"], ACTOR)
        self.assertEqual(item["corrected_at"], FIXED_NOW.isoformat())
        self.assertEqual(item["retained_until"], (FIXED_NOW + timedelta(days=365)).isoformat())
        self.assertTrue(item["audio_available"])

    def test_the_audio_proxy_works_for_the_corrected_prefix(self):
        self.service.correct_segment(SEGMENT_ID, "Fixed.", actor=ACTOR)
        self.assertEqual(self.service.fetch_audio(SEGMENT_ID, actor=OTHER_ACTOR), make_wav())
        listen = [e for e in self.store.audit if e["event_type"] == "radio.segment.listen"]
        self.assertEqual([e["actor"] for e in listen], [OTHER_ACTOR])

    def test_history_is_append_only_and_records_previous_text(self):
        self.service.correct_segment(SEGMENT_ID, "First fix.", actor=ACTOR)
        self.service.correct_segment(SEGMENT_ID, "Second fix.", actor=OTHER_ACTOR)
        history = self.store.corrections
        self.assertEqual(
            [(h["previous_text"], h["new_text"], h["actor"]) for h in history],
            [
                ("unit four log in county", "First fix.", ACTOR),
                ("First fix.", "Second fix.", OTHER_ACTOR),
            ],
        )

    def test_a_second_correction_does_not_copy_again_and_extends_retention(self):
        self.service.correct_segment(SEGMENT_ID, "First.", actor=ACTOR)
        self.store.db_now = FIXED_NOW + timedelta(days=10)
        item = self.service.correct_segment(SEGMENT_ID, "Second.", actor=OTHER_ACTOR)
        self.assertEqual(len(self.s3.copy_calls), 1)
        self.assertEqual(
            item["retained_until"], (FIXED_NOW + timedelta(days=375)).isoformat()
        )
        self.assertEqual(item["corrected_by"], OTHER_ACTOR)
        self.assertEqual(self.row()[10], self.corrected_key)

    def test_a_correction_is_audited_without_the_text(self):
        self.service.correct_segment(SEGMENT_ID, "Secret words here.", actor=ACTOR)
        event = self.store.audit[-1]
        self.assertEqual(event["event_type"], "radio.segment.correct")
        self.assertEqual(event["actor"], ACTOR)
        self.assertEqual(event["segment_id"], SEGMENT_ID)
        self.assertTrue(event["detail"]["first_correction"])
        self.assertTrue(event["detail"]["audio_retained"])
        self.assertNotIn("Secret", str(event))

    def test_revert_restores_machine_text_keeps_retention_and_records_history(self):
        self.service.correct_segment(SEGMENT_ID, "Corrected.", actor=ACTOR)
        add_fix(self.service, "log in", "Logan")
        item = self.service.revert_correction(SEGMENT_ID, actor=OTHER_ACTOR)
        self.assertFalse(item["corrected"])
        self.assertIsNone(item["corrected_by"])
        self.assertIsNone(item["corrected_at"])
        # Back to the machine text, with the fixes applied again.
        self.assertEqual(item["text"], "unit four Logan county")
        # Once kept, kept until retain_until.
        self.assertEqual(item["retained_until"], (FIXED_NOW + timedelta(days=365)).isoformat())
        self.assertTrue(item["audio_available"])
        self.assertEqual(self.row()[10], self.corrected_key)
        last = self.store.corrections[-1]
        self.assertEqual((last["previous_text"], last["new_text"], last["actor"]),
                         ("Corrected.", None, OTHER_ACTOR))
        self.assertEqual(audit_types(self.store)[-1], "radio.segment.correct_revert")
        self.assertEqual(len(self.store.corrections), 2)  # nothing was rewritten

    def test_a_correction_after_a_revert_does_not_copy_the_audio_again(self):
        self.service.correct_segment(SEGMENT_ID, "One.", actor=ACTOR)
        self.service.revert_correction(SEGMENT_ID, actor=ACTOR)
        self.service.correct_segment(SEGMENT_ID, "Two.", actor=ACTOR)
        self.assertEqual(len(self.s3.copy_calls), 1)

    def test_reverting_an_uncorrected_segment_is_a_quiet_no_op(self):
        before = list(self.store.audit)
        item = self.service.revert_correction(SEGMENT_ID, actor=ACTOR)
        self.assertFalse(item["corrected"])
        self.assertEqual(self.store.audit, before)
        self.assertEqual(self.store.corrections, [])

    def test_when_the_audio_is_already_gone_the_text_is_still_saved(self):
        service, store, s3 = build_service()
        store.received_at = FIXED_NOW - timedelta(days=16)
        seed(service, transcript="old words")
        item = service.correct_segment(SEGMENT_ID, "Fixed old words.", actor=ACTOR)
        self.assertEqual(s3.copy_calls, [])
        self.assertTrue(item["corrected"])
        self.assertFalse(item["audio_available"])
        self.assertEqual(item["retained_until"], (FIXED_NOW + timedelta(days=365)).isoformat())
        self.assertEqual(store.segments[SEGMENT_ID][10], self.normal_key)
        self.assertFalse(store.audit[-1]["detail"]["audio_retained"])

    def test_a_missing_source_object_still_saves_the_text(self):
        self.s3.copy_failure = "missing"
        item = self.service.correct_segment(SEGMENT_ID, "Saved anyway.", actor=ACTOR)
        self.assertEqual(item["text"], "Saved anyway.")
        self.assertEqual(self.row()[10], self.normal_key)
        self.assertEqual(self.s3.copy_calls, [])

    def test_any_other_storage_failure_changes_nothing(self):
        self.s3.copy_failure = "down"
        audit_before = list(self.store.audit)
        with self.assertRaises(RadioStorageError):
            self.service.correct_segment(SEGMENT_ID, "Will not save.", actor=ACTOR)
        self.assertIsNone(self.row()[12])
        self.assertEqual(self.store.corrections, [])
        self.assertEqual(self.store.audit, audit_before)

    def test_a_failed_audit_write_means_no_correction(self):
        self.store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            self.service.correct_segment(SEGMENT_ID, "Will not save.", actor=ACTOR)
        self.assertIsNone(self.row()[12])
        self.assertIsNone(self.row()[15])
        self.assertEqual(self.store.corrections, [])
        self.assertEqual(self.row()[10], self.normal_key)

    def test_a_failed_audit_write_means_no_revert(self):
        self.service.correct_segment(SEGMENT_ID, "Keep me.", actor=ACTOR)
        self.store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            self.service.revert_correction(SEGMENT_ID, actor=ACTOR)
        self.assertEqual(self.row()[12], "Keep me.")
        self.assertEqual(len(self.store.corrections), 1)

    def test_text_validation_happens_before_anything_is_written(self):
        for bad in ("", "   ", "x" * 20001, "a\x00b", None, 12, "a‮b"):
            with self.subTest(bad=repr(bad)[:20]):
                with self.assertRaises(RadioQueryError):
                    self.service.correct_segment(SEGMENT_ID, bad, actor=ACTOR)
        self.assertEqual(self.s3.copy_calls, [])
        self.assertEqual(self.store.corrections, [])

    def test_text_at_the_limit_and_with_newlines_is_accepted(self):
        item = self.service.correct_segment(SEGMENT_ID, "x" * 20000, actor=ACTOR)
        self.assertEqual(len(item["text"]), 20000)
        item = self.service.correct_segment(SEGMENT_ID, "line one\nline\ttwo\r\nthree", actor=ACTOR)
        self.assertEqual(item["text"], "line one\nline\ttwo\nthree")

    def test_unknown_or_malformed_segment_ids_are_not_found(self):
        for bad in ("law_rx_never_stored", "short", "../../etc/passwd", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(RadioNotFoundError):
                    self.service.correct_segment(bad, "text", actor=ACTOR)
                with self.assertRaises(RadioNotFoundError):
                    self.service.revert_correction(bad, actor=ACTOR)

    def test_the_list_search_covers_the_correction_text(self):
        self.service.correct_segment(SEGMENT_ID, "Needle in a haystack.", actor=ACTOR)
        self.assertEqual(list(listed(self.service, q="needle")), [SEGMENT_ID])
        self.assertEqual(listed(self.service, q="log in"), {})

    def test_list_items_carry_the_v2_fields_for_uncorrected_segments_too(self):
        other = "ems_rx_plain_0001"
        seed(self.service, segment_id=other, channel="ems",
             started_at="2026-10-16T14:06:00+00:00", ended_at="2026-10-16T14:06:05+00:00")
        item = listed(self.service)[other]
        for key in ("text", "transcript", "machine_transcript", "corrected",
                    "corrected_by", "corrected_at", "retained_until"):
            self.assertIn(key, item)
        self.assertFalse(item["corrected"])
        self.assertIsNone(item["corrected_by"])
        self.assertIsNone(item["corrected_at"])
        self.assertIsNone(item["retained_until"])
        self.assertNotIn("audio_key", item)


class AudioAvailabilityTests(unittest.TestCase):
    def test_normal_prefix_follows_the_15_day_rule(self):
        service, store, _ = build_service()
        seed(service)
        store.db_now = FIXED_NOW + timedelta(days=14)
        self.assertTrue(listed(service)[SEGMENT_ID]["audio_available"])
        store.db_now = FIXED_NOW + timedelta(days=16)
        self.assertFalse(listed(service)[SEGMENT_ID]["audio_available"])

    def test_corrected_prefix_follows_retain_until(self):
        service, store, _ = build_service()
        seed(service)
        service.correct_segment(SEGMENT_ID, "Kept.", actor=ACTOR)
        store.db_now = FIXED_NOW + timedelta(days=100)  # well past 15 days
        self.assertTrue(listed(service)[SEGMENT_ID]["audio_available"])
        store.db_now = FIXED_NOW + timedelta(days=364)
        self.assertTrue(listed(service)[SEGMENT_ID]["audio_available"])
        store.db_now = FIXED_NOW + timedelta(days=366)
        self.assertFalse(listed(service)[SEGMENT_ID]["audio_available"])

    def test_a_blank_corrected_prefix_falls_back_to_the_default(self):
        service = RadioService(
            bucket="b", audio_prefix="radio/audio/", corrected_prefix="  ",
            s3_client=FakeS3(), repository_factory=lambda: None,
        )
        self.assertEqual(service._corrected_prefix, "radio/corrected/")
        service = RadioService(
            bucket="b", audio_prefix="radio/audio/", corrected_prefix="/custom/keep",
            s3_client=FakeS3(), repository_factory=lambda: None,
        )
        self.assertEqual(service._corrected_prefix, "custom/keep/")


class RetentionPurgeTests(unittest.TestCase):
    """The lazy purge keeps a corrected segment until its retain_until passes."""

    def build(self):
        now = [1000.0]
        service, store, s3 = build_service(clock=lambda: now[0])
        return service, store, s3, now

    def test_uncorrected_old_segments_are_purged_corrected_ones_kept(self):
        service, store, s3, now = self.build()
        store.received_at = FIXED_NOW - timedelta(days=5)
        seed(service, segment_id="law_rx_kept_000001", transcript="keep")
        seed(service, segment_id="law_rx_gone_000001", transcript="gone",
             started_at="2026-10-16T14:06:00+00:00", ended_at="2026-10-16T14:06:05+00:00")
        service.correct_segment("law_rx_kept_000001", "Kept by a person.", actor=ACTOR)
        # 40 days later: both are past 30 days; only the corrected one is held.
        store.db_now = FIXED_NOW + timedelta(days=40)
        store.received_at = store.db_now
        now[0] += 7200
        seed(service, segment_id="law_rx_fresh_000001",
             started_at="2026-11-25T10:00:00+00:00", ended_at="2026-11-25T10:00:05+00:00")
        self.assertEqual(
            sorted(store.segments), ["law_rx_fresh_000001", "law_rx_kept_000001"]
        )
        # The kept segment's history survives with it.
        self.assertEqual(len(store.corrections), 1)

    def test_a_corrected_segment_is_purged_once_retain_until_has_passed(self):
        service, store, s3, now = self.build()
        seed(service, segment_id="law_rx_kept_000001")
        service.correct_segment("law_rx_kept_000001", "Kept a year.", actor=ACTOR)
        store.db_now = FIXED_NOW + timedelta(days=366)
        store.received_at = store.db_now
        now[0] += 7200
        seed(service, segment_id="law_rx_fresh_000001",
             started_at="2027-10-18T10:00:00+00:00", ended_at="2027-10-18T10:00:05+00:00")
        self.assertEqual(sorted(store.segments), ["law_rx_fresh_000001"])
        self.assertEqual(store.corrections, [])  # ON DELETE CASCADE
        self.assertEqual(
            [e["event_type"] for e in store.audit if e["event_type"] == "radio.segment.correct"],
            ["radio.segment.correct"],
        )  # audit events are never purged

    def test_a_reverted_segment_is_still_held_until_retain_until(self):
        service, store, s3, now = self.build()
        seed(service, segment_id="law_rx_kept_000001")
        service.correct_segment("law_rx_kept_000001", "Kept.", actor=ACTOR)
        service.revert_correction("law_rx_kept_000001", actor=ACTOR)
        store.db_now = FIXED_NOW + timedelta(days=40)
        store.received_at = store.db_now
        now[0] += 7200
        seed(service, segment_id="law_rx_fresh_000001",
             started_at="2026-11-25T10:00:00+00:00", ended_at="2026-11-25T10:00:05+00:00")
        self.assertIn("law_rx_kept_000001", store.segments)


# ============================================================ watch terms


class WatchTermServiceTests(unittest.TestCase):
    def test_add_list_change_remove_with_audit(self):
        service, store, _ = build_service()
        term = add_term(service, "structure fire", channels=["fire", "ems"])
        self.assertEqual(term["phrase"], "structure fire")
        self.assertEqual(term["channels"], ["fire", "ems"])
        self.assertTrue(term["enabled"])
        listing = service.list_watch_terms()
        self.assertEqual(listing["max_terms"], 100)
        self.assertEqual([t["term_id"] for t in listing["terms"]], [term["term_id"]])
        changed = service.update_watch_term(
            str(term["term_id"]), {"enabled": False, "channels": []}, actor=ADMIN_EMAIL
        )
        self.assertFalse(changed["enabled"])
        self.assertEqual(changed["channels"], [])
        service.update_watch_term(term["term_id"], {"enabled": True}, actor=ADMIN_EMAIL)
        self.assertEqual(service.list_watch_terms()["terms"][0]["channels"], [])
        service.remove_watch_term(term["term_id"], actor=ADMIN_EMAIL)
        self.assertEqual(service.list_watch_terms()["terms"], [])
        self.assertEqual(
            audit_types(store),
            ["radio.watch.add", "radio.watch.change", "radio.watch.change",
             "radio.watch.remove"],
        )
        self.assertTrue(all(e["actor"] == ADMIN_EMAIL for e in store.audit))

    def test_add_validation(self):
        service, store, _ = build_service()
        cases = {
            "missing phrase": {},
            "one character": {"phrase": "a"},
            "81 characters": {"phrase": "a" * 81},
            "blank": {"phrase": "   "},
            "control character": {"phrase": "a\x00b"},
            "newline": {"phrase": "two\nlines"},
            "phrase not a string": {"phrase": 12},
            "channel not a list": {"phrase": "fire", "channels": "law"},
            "bad channel": {"phrase": "fire", "channels": ["LAW"]},
            "enabled not a bool": {"phrase": "fire", "enabled": "yes"},
            "unknown key": {"phrase": "fire", "regex": True},
        }
        for label, body in cases.items():
            with self.subTest(label):
                with self.assertRaises(RadioQueryError):
                    service.add_watch_term(body, actor=ADMIN_EMAIL)
        self.assertEqual(store.terms, [])
        self.assertEqual(store.audit, [])

    def test_phrase_length_boundaries_are_accepted(self):
        service, _, _ = build_service()
        add_term(service, "ab")
        add_term(service, "a" * 80)

    def test_duplicates_are_rejected_case_insensitively_and_there_are_at_most_100(self):
        service, store, _ = build_service()
        add_term(service, "Structure Fire")
        with self.assertRaises(RadioConflictError):
            add_term(service, "structure fire")
        for index in range(99):
            add_term(service, f"term number {index}")
        self.assertEqual(len(store.terms), 100)
        with self.assertRaises(RadioConflictError):
            add_term(service, "one too many")

    def test_change_validation(self):
        service, store, _ = build_service()
        term = add_term(service, "fire")
        for body in ({}, {"phrase": "x"}, {"enabled": "no"}, {"channels": ["BAD"]}, [], None):
            with self.subTest(body=body):
                with self.assertRaises(RadioQueryError):
                    service.update_watch_term(term["term_id"], body, actor=ADMIN_EMAIL)
        with self.assertRaises(RadioNotFoundError):
            service.update_watch_term("999", {"enabled": True}, actor=ADMIN_EMAIL)
        with self.assertRaises(RadioNotFoundError):
            service.update_watch_term("abc", {"enabled": True}, actor=ADMIN_EMAIL)
        with self.assertRaises(RadioNotFoundError):
            service.remove_watch_term("999", actor=ADMIN_EMAIL)

    def test_a_failed_audit_write_means_no_change(self):
        service, store, _ = build_service()
        store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            add_term(service, "fire")
        self.assertEqual(store.terms, [])
        store.fail_audit = False
        term = add_term(service, "fire")
        store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            service.update_watch_term(term["term_id"], {"enabled": False}, actor=ADMIN_EMAIL)
        with self.assertRaises(RadioDatabaseError):
            service.remove_watch_term(term["term_id"], actor=ADMIN_EMAIL)
        self.assertTrue(store.terms[0]["enabled"])
        self.assertEqual(len(store.terms), 1)


# ================================================================== alerts


def alert_phrases(service):
    return [a["phrase"] for a in service.list_alerts()["alerts"]]


class AlertMatchingTests(unittest.TestCase):
    def test_a_matching_segment_raises_an_alert_at_ingest(self):
        service, store, _ = build_service()
        add_term(service, "structure fire")
        seed(service, transcript="Dispatch reports a STRUCTURE fire on Elm Street.")
        alerts = service.list_alerts()["alerts"]
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["phrase"], "structure fire")
        self.assertEqual(alert["segment_id"], SEGMENT_ID)
        self.assertEqual(alert["channel"], "law")
        self.assertEqual(alert["direction"], "rx")
        self.assertEqual(alert["snippet"], "Dispatch reports a STRUCTURE fire on Elm Street.")
        self.assertFalse(alert["text_corrected"])
        self.assertTrue(alert["audio_available"])
        self.assertEqual(
            set(alert),
            {"alert_id", "segment_id", "channel", "direction", "started_at", "phrase",
             "snippet", "created_at", "audio_available", "text_corrected"},
        )

    def test_matching_is_whole_word_literal_and_case_insensitive(self):
        service, _, _ = build_service()
        add_term(service, "fire")
        add_term(service, "10-33 (a+)")
        seed(service, segment_id="law_rx_word_000001", transcript="Setting off fireworks, bonfire.")
        self.assertEqual(alert_phrases(service), [])
        seed(service, segment_id="law_rx_hit_0000001", transcript="Code 10-33 (A+) and FIRE!",
             started_at="2026-10-16T14:06:00+00:00", ended_at="2026-10-16T14:06:05+00:00")
        self.assertEqual(sorted(alert_phrases(service)), ["10-33 (a+)", "fire"])

    def test_the_machine_text_is_matched_with_the_fixes_applied(self):
        service, _, _ = build_service()
        add_fix(service, "log in", "Logan")
        add_term(service, "Logan")
        seed(service, transcript="Unit four to log in county.")
        self.assertEqual(alert_phrases(service), ["Logan"])

    def test_a_term_with_a_channel_filter_only_applies_to_those_channels(self):
        service, _, _ = build_service()
        add_term(service, "fire", channels=["fire"])
        seed(service, segment_id="law_rx_law_000001", transcript="fire at the lot")
        self.assertEqual(alert_phrases(service), [])
        seed(service, segment_id="fire_rx_fire_0001", channel="fire", transcript="fire at the lot",
             started_at="2026-10-16T14:06:00+00:00", ended_at="2026-10-16T14:06:05+00:00")
        self.assertEqual(alert_phrases(service), ["fire"])

    def test_disabled_terms_do_not_alert_and_empty_text_never_matches(self):
        service, _, _ = build_service()
        term = add_term(service, "fire")
        service.update_watch_term(term["term_id"], {"enabled": False}, actor=ADMIN_EMAIL)
        seed(service, segment_id="law_rx_off_0000001", transcript="fire")
        seed(service, segment_id="law_rx_empty_00001", transcript="",
             started_at="2026-10-16T14:06:00+00:00", ended_at="2026-10-16T14:06:05+00:00")
        self.assertEqual(alert_phrases(service), [])

    def test_terms_added_later_do_not_alert_on_older_segments(self):
        service, _, _ = build_service()
        seed(service, transcript="fire")
        add_term(service, "fire")
        self.assertEqual(alert_phrases(service), [])

    def test_a_correction_that_adds_a_phrase_raises_an_alert(self):
        service, _, _ = build_service()
        add_term(service, "structure fire")
        seed(service, transcript="garbled noise")
        self.assertEqual(alert_phrases(service), [])
        service.correct_segment(SEGMENT_ID, "Structure fire at 5 Elm.", actor=ACTOR)
        alerts = service.list_alerts()["alerts"]
        self.assertEqual([a["phrase"] for a in alerts], ["structure fire"])
        self.assertTrue(alerts[0]["text_corrected"])
        self.assertEqual(alerts[0]["snippet"], "Structure fire at 5 Elm.")

    def test_one_alert_per_segment_and_term_even_when_checked_again(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        seed(service, transcript="fire")
        service.correct_segment(SEGMENT_ID, "fire fire", actor=ACTOR)
        service.correct_segment(SEGMENT_ID, "a fire again", actor=ACTOR)
        self.assertEqual(len(store.alerts), 1)
        # A repeat ingest of the same segment is a duplicate and changes nothing.
        _, status = ingest_record(service, transcript="fire")
        self.assertEqual(status, "duplicate")
        self.assertEqual(len(store.alerts), 1)

    def test_a_dismissed_alert_is_not_revived_by_a_later_correction(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        seed(service, transcript="fire")
        alert_id = service.list_alerts()["alerts"][0]["alert_id"]
        service.dismiss_alert(alert_id, actor=ACTOR)
        service.correct_segment(SEGMENT_ID, "fire on Elm", actor=ACTOR)
        self.assertEqual(service.list_alerts()["alerts"], [])
        self.assertEqual(len(store.alerts), 1)

    def test_one_segment_can_raise_an_alert_for_each_matching_term(self):
        service, _, _ = build_service()
        add_term(service, "fire")
        add_term(service, "Elm Street")
        seed(service, transcript="fire on elm street")
        self.assertEqual(sorted(alert_phrases(service)), ["Elm Street", "fire"])

    def test_removing_a_term_removes_its_alerts(self):
        service, store, _ = build_service()
        term = add_term(service, "fire")
        seed(service, transcript="fire")
        service.remove_watch_term(term["term_id"], actor=ADMIN_EMAIL)
        self.assertEqual(store.alerts, [])
        self.assertEqual(service.list_alerts()["alerts"], [])


class MatchingFailureTests(unittest.TestCase):
    """Matching is advisory. It must never fail an ingest or a correction."""

    def test_an_unreadable_watch_term_table_does_not_fail_the_ingest(self):
        service, store, _ = build_service()
        store.fail_term_read = True
        _, status = ingest_record(service)
        self.assertEqual(status, "created")
        self.assertIn(SEGMENT_ID, store.segments)

    def test_a_failing_alert_insert_does_not_fail_the_ingest(self):
        service, store, _ = build_service()
        add_term(service, "traffic stop")
        store.fail_alert_insert = True
        _, status = ingest_record(service)
        self.assertEqual(status, "created")
        self.assertEqual(store.alerts, [])

    def test_an_unexpected_error_in_the_matcher_does_not_fail_the_ingest(self):
        service, store, _ = build_service()
        add_term(service, "traffic stop")
        original = radio_text.compile_phrase

        def boom(_phrase):
            raise RuntimeError("matcher exploded")

        radio_text.compile_phrase = boom
        try:
            _, status = ingest_record(service)
        finally:
            radio_text.compile_phrase = original
        self.assertEqual(status, "created")
        self.assertIn(SEGMENT_ID, store.segments)

    def test_a_failure_while_loading_the_fixes_does_not_fail_the_ingest(self):
        service, store, _ = build_service()

        def broken(self):
            raise RadioDatabaseError("fixes unavailable")

        from .test_radio_routes import FakeRepository

        original = FakeRepository.list_replacements
        FakeRepository.list_replacements = broken
        try:
            _, status = ingest_record(service)
        finally:
            FakeRepository.list_replacements = original
        self.assertEqual(status, "created")

    def test_a_failing_alert_does_not_undo_a_saved_correction(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        seed(service, transcript="noise")
        store.fail_alert_insert = True
        item = service.correct_segment(SEGMENT_ID, "fire on Elm", actor=ACTOR)
        self.assertEqual(item["text"], "fire on Elm")
        self.assertEqual(store.segments[SEGMENT_ID][12], "fire on Elm")
        self.assertEqual(store.alerts, [])

    def test_one_failing_term_does_not_stop_the_others(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        add_term(service, "Elm")
        original = store.fail_alert_insert
        from .test_radio_routes import FakeRepository

        real = FakeRepository.insert_alert
        calls = []

        def flaky(self, segment_id, term_id, phrase):
            calls.append(term_id)
            if len(calls) == 1:
                raise RadioDatabaseError("first insert fails")
            return real(self, segment_id, term_id, phrase)

        FakeRepository.insert_alert = flaky
        try:
            seed(service, transcript="fire on Elm")
        finally:
            FakeRepository.insert_alert = real
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(store.alerts), 1)


class AlertListAndDismissTests(unittest.TestCase):
    def test_only_undismissed_alerts_from_the_last_24_hours_newest_first(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        store.db_now = FIXED_NOW - timedelta(hours=30)
        seed(service, segment_id="law_rx_old_0000001", transcript="old fire")
        store.db_now = FIXED_NOW - timedelta(hours=2)
        seed(service, segment_id="law_rx_mid_0000001", transcript="mid fire",
             started_at="2026-10-16T14:06:00+00:00", ended_at="2026-10-16T14:06:05+00:00")
        store.db_now = FIXED_NOW - timedelta(hours=1)
        seed(service, segment_id="law_rx_new_0000001", transcript="new fire",
             started_at="2026-10-16T14:07:00+00:00", ended_at="2026-10-16T14:07:05+00:00")
        store.db_now = FIXED_NOW - timedelta(minutes=30)
        seed(service, segment_id="law_rx_done_000001", transcript="done fire",
             started_at="2026-10-16T14:08:00+00:00", ended_at="2026-10-16T14:08:05+00:00")
        store.db_now = FIXED_NOW
        done = next(a for a in service.list_alerts()["alerts"]
                    if a["segment_id"] == "law_rx_done_000001")
        service.dismiss_alert(done["alert_id"], actor=ACTOR)
        self.assertEqual(
            [a["segment_id"] for a in service.list_alerts()["alerts"]],
            ["law_rx_new_0000001", "law_rx_mid_0000001"],
        )

    def test_at_most_50_alerts(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        for index in range(55):
            store.db_now = FIXED_NOW - timedelta(minutes=120 - index)
            seed(service, segment_id=f"law_rx_many_{index:05d}", transcript="fire",
                 started_at=f"2026-10-16T12:{index % 60:02d}:00+00:00",
                 ended_at=f"2026-10-16T12:{index % 60:02d}:05+00:00")
        store.db_now = FIXED_NOW
        alerts = service.list_alerts()["alerts"]
        self.assertEqual(len(alerts), 50)
        self.assertEqual(alerts[0]["segment_id"], "law_rx_many_00054")

    def test_the_snippet_is_display_text_capped_at_300_characters(self):
        service, _, _ = build_service()
        add_term(service, "fire")
        add_fix(service, "log in", "Logan")
        seed(service, transcript="fire " + "log in " * 100)
        snippet = service.list_alerts()["alerts"][0]["snippet"]
        self.assertLessEqual(len(snippet), 300)
        self.assertIn("Logan", snippet)
        self.assertNotIn("log in", snippet)

    def test_audio_availability_follows_the_segment(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        seed(service, transcript="fire")
        store.db_now = FIXED_NOW + timedelta(hours=20)
        self.assertTrue(service.list_alerts()["alerts"][0]["audio_available"])
        service.correct_segment(SEGMENT_ID, "fire kept", actor=ACTOR)
        store.db_now = FIXED_NOW + timedelta(hours=23)
        self.assertTrue(service.list_alerts()["alerts"][0]["audio_available"])

    def test_listing_alerts_is_not_audited_per_poll(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        seed(service, transcript="fire")
        before = len(store.audit)
        for _ in range(5):
            service.list_alerts()
        self.assertEqual(len(store.audit), before)

    def test_dismissal_is_shared_audited_and_idempotent(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        seed(service, transcript="fire")
        alert_id = service.list_alerts()["alerts"][0]["alert_id"]
        self.assertEqual(
            service.dismiss_alert(str(alert_id), actor=ACTOR),
            {"alert_id": alert_id, "dismissed": True},
        )
        # Anyone else sees it gone: dismissal is shared.
        self.assertEqual(service.list_alerts()["alerts"], [])
        self.assertEqual(store.alerts[0]["dismissed_by"], ACTOR)
        self.assertEqual(store.alerts[0]["dismissed_at"], FIXED_NOW)
        # A second dismissal succeeds without a second audit row.
        self.assertEqual(
            service.dismiss_alert(alert_id, actor=OTHER_ACTOR),
            {"alert_id": alert_id, "dismissed": True},
        )
        dismissals = [e for e in store.audit if e["event_type"] == "radio.alert.dismiss"]
        self.assertEqual(len(dismissals), 1)
        self.assertEqual(dismissals[0]["actor"], ACTOR)
        self.assertEqual(dismissals[0]["segment_id"], SEGMENT_ID)
        self.assertEqual(store.alerts[0]["dismissed_by"], ACTOR)

    def test_dismissing_an_unknown_or_malformed_alert_is_not_found(self):
        service, store, _ = build_service()
        for bad in ("999", "0", "-3", "abc", "", "9" * 30):
            with self.subTest(bad=bad):
                with self.assertRaises(RadioNotFoundError):
                    service.dismiss_alert(bad, actor=ACTOR)
        self.assertEqual(store.audit, [])

    def test_a_failed_audit_write_means_the_alert_stays_open(self):
        service, store, _ = build_service()
        add_term(service, "fire")
        seed(service, transcript="fire")
        alert_id = service.list_alerts()["alerts"][0]["alert_id"]
        store.fail_audit = True
        with self.assertRaises(RadioDatabaseError):
            service.dismiss_alert(alert_id, actor=ACTOR)
        self.assertIsNone(store.alerts[0]["dismissed_at"])
        store.fail_audit = False
        self.assertEqual(len(service.list_alerts()["alerts"]), 1)


# ====================================================== repository SQL


class ScriptedConnection:
    """Returns queued rows per statement and records commits and rollbacks."""

    def __init__(self, results=None, fail_on=None):
        self.calls = []
        self.results = list(results or [])
        self.fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0

    def execute(self, query, params=None):
        self.calls.append((query, params))
        if self.fail_on and self.fail_on in query:
            raise psycopg.OperationalError("simulated failure")
        rows = self.results.pop(0) if self.results else []
        return FakeCursor(rows=rows, rowcount=1)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


SEGMENT_ROW = (
    SEGMENT_ID, "law", "rx", FIXED_NOW, FIXED_NOW, 5.0, "machine text", "en", "m",
    False, "radio/audio/k.wav", FIXED_NOW, None, None, None, None,
)


class RepositoryV2SqlTests(unittest.TestCase):
    def test_save_correction_locks_the_row_binds_the_text_and_does_not_commit(self):
        connection = ScriptedConnection(
            results=[[(None, "machine text")], [], [], [SEGMENT_ROW]]
        )
        repository = repository_with(connection)
        hostile = "x'; DROP TABLE lcdash_radio.segments; --"
        row = repository.save_correction(
            SEGMENT_ID, hostile, ACTOR, FIXED_NOW, FIXED_NOW + timedelta(days=365),
            "radio/corrected/k.wav",
        )
        self.assertEqual(row, SEGMENT_ROW)
        queries = [query for query, _ in connection.calls]
        self.assertIn("FOR UPDATE", queries[0])
        self.assertIn("INSERT INTO lcdash_radio.corrections", queries[1])
        self.assertIn("UPDATE lcdash_radio.segments SET corrected_text", queries[2])
        self.assertIn("audio_key = COALESCE(%s, audio_key)", queries[2])
        self.assertTrue(all("DROP" not in query for query in queries))
        # The text and the history's previous_text travel as bound parameters.
        self.assertEqual(connection.calls[1][1][:3], (SEGMENT_ID, "machine text", hostile))
        self.assertEqual(connection.calls[2][1][0], hostile)
        # Only the audit insert (record_audit) commits; the change alone never does.
        self.assertEqual(connection.commits, 0)

    def test_previous_text_is_the_prior_correction_when_there_is_one(self):
        connection = ScriptedConnection(
            results=[[("earlier fix", "machine text")], [], [], [SEGMENT_ROW]]
        )
        repository_with(connection).save_correction(
            SEGMENT_ID, "new", ACTOR, FIXED_NOW, FIXED_NOW, None
        )
        self.assertEqual(connection.calls[1][1][1], "earlier fix")

    def test_save_correction_on_a_missing_segment_writes_nothing(self):
        connection = ScriptedConnection(results=[[]])
        self.assertIsNone(
            repository_with(connection).save_correction(
                SEGMENT_ID, "x", ACTOR, FIXED_NOW, FIXED_NOW, None
            )
        )
        self.assertEqual(len(connection.calls), 1)

    def test_clear_correction_writes_a_null_history_row_and_keeps_retention(self):
        connection = ScriptedConnection(results=[[("fixed",)], [], [], [SEGMENT_ROW]])
        row, changed = repository_with(connection).clear_correction(
            SEGMENT_ID, ACTOR, FIXED_NOW
        )
        self.assertTrue(changed)
        history_query, history_params = connection.calls[1]
        self.assertIn("VALUES (%s, %s, NULL, %s, %s)", history_query)
        self.assertEqual(history_params[:2], (SEGMENT_ID, "fixed"))
        update_query = connection.calls[2][0]
        self.assertIn("corrected_text = NULL", update_query)
        self.assertNotIn("retain_until", update_query)
        self.assertEqual(connection.commits, 0)

    def test_clear_correction_on_an_uncorrected_segment_changes_nothing(self):
        connection = ScriptedConnection(results=[[(None,)], [SEGMENT_ROW]])
        row, changed = repository_with(connection).clear_correction(
            SEGMENT_ID, ACTOR, FIXED_NOW
        )
        self.assertFalse(changed)
        self.assertEqual(len(connection.calls), 2)

    def test_a_failed_audit_insert_rolls_back_the_uncommitted_change(self):
        connection = ScriptedConnection(
            results=[[(None, "machine text")], [], [], [SEGMENT_ROW]],
            fail_on="audit_events",
        )
        repository = repository_with(connection)
        repository.save_correction(SEGMENT_ID, "x", ACTOR, FIXED_NOW, FIXED_NOW, None)
        with self.assertRaises(RadioDatabaseError):
            repository.record_audit("radio.segment.correct", ACTOR, SEGMENT_ID, {})
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 0)

    def test_a_successful_audit_insert_commits_the_change_with_it(self):
        connection = ScriptedConnection(results=[[(None, "m")], [], [], [SEGMENT_ROW], []])
        repository = repository_with(connection)
        repository.save_correction(SEGMENT_ID, "x", ACTOR, FIXED_NOW, FIXED_NOW, None)
        repository.record_audit("radio.segment.correct", ACTOR, SEGMENT_ID, {})
        self.assertEqual(connection.commits, 1)
        self.assertIn("audit_events", connection.calls[-1][0])

    def test_replacement_and_term_inserts_are_idempotent_on_the_lowercase_key(self):
        connection = ScriptedConnection(results=[[], []])
        repository = repository_with(connection)
        self.assertIsNone(repository.insert_replacement("a", "b", True, ACTOR))
        self.assertIsNone(repository.insert_watch_term("fire", [], True, ACTOR))
        self.assertIn("ON CONFLICT ((lower(find_text))) DO NOTHING", connection.calls[0][0])
        self.assertIn("ON CONFLICT ((lower(phrase))) DO NOTHING", connection.calls[1][0])
        self.assertIn("%s::TEXT[]", connection.calls[1][0])
        self.assertEqual(connection.commits, 0)

    def test_update_watch_term_sets_only_the_supplied_fields(self):
        connection = ScriptedConnection(results=[[], []])
        repository = repository_with(connection)
        repository.update_watch_term(3, enabled=False, channels=None)
        repository.update_watch_term(3, enabled=None, channels=["law"])
        first, second = connection.calls
        self.assertIn("SET enabled = %s WHERE", first[0])
        self.assertNotIn("channels = ", first[0])
        self.assertEqual(first[1], (False, 3))
        self.assertIn("SET channels = %s::TEXT[] WHERE", second[0])
        self.assertEqual(second[1], (["law"], 3))

    def test_alert_insert_is_deduplicated_by_the_database(self):
        connection = ScriptedConnection(results=[[]])
        repository_with(connection).insert_alert(SEGMENT_ID, 4, "fire")
        self.assertIn("ON CONFLICT (segment_id, term_id) DO NOTHING", connection.calls[0][0])
        self.assertEqual(connection.commits, 1)

    def test_open_alerts_query_is_windowed_ordered_limited_and_bound(self):
        connection = ScriptedConnection(results=[[]])
        repository_with(connection).list_open_alerts(FIXED_NOW, 50)
        query, params = connection.calls[0]
        self.assertIn("a.dismissed_at IS NULL", query)
        self.assertIn("a.created_at >= %s", query)
        self.assertIn("ORDER BY a.created_at DESC, a.alert_id DESC LIMIT %s", query)
        self.assertEqual(params, (FIXED_NOW, 50))

    def test_dismiss_updates_only_open_alerts_and_tells_already_from_missing(self):
        connection = ScriptedConnection(results=[[(SEGMENT_ID,)]])
        repository = repository_with(connection)
        self.assertEqual(repository.dismiss_alert(1, ACTOR, FIXED_NOW), ("dismissed", SEGMENT_ID))
        self.assertIn("AND dismissed_at IS NULL RETURNING segment_id", connection.calls[0][0])
        connection = ScriptedConnection(results=[[], [(SEGMENT_ID,)]])
        self.assertEqual(
            repository_with(connection).dismiss_alert(1, ACTOR, FIXED_NOW),
            ("already", SEGMENT_ID),
        )
        connection = ScriptedConnection(results=[[], []])
        self.assertIsNone(repository_with(connection).dismiss_alert(1, ACTOR, FIXED_NOW))


# ===================================================== tier access helper


class RadioAccessHelperTests(unittest.TestCase):
    def test_only_the_listed_shapes_are_human_paths(self):
        yes = (
            "/mindshare/radio", "/mindshare/radio/", "/mindshare/radio?x=1",
            "/api/radio/segments", "/api/radio/stats", "/api/radio/alerts",
            "/api/radio/segments/abc/audio", "/api/radio/segments/abc/correction",
            "/api/radio/alerts/12/dismiss",
        )
        no = (
            "", "/", "/api/radio/ingest", "/api/radio/heartbeat",
            "/api/radio/heartbeat/", "/api/radio/hints", "/api/radio/hints/1",
            "/api/radio/replacements",
            "/api/radio/replacements/1", "/api/radio/watch-terms",
            "/api/radio/watch-terms/1", "/api/radio", "/api/radio/segments/abc",
            "/api/radio/segments/abc/audio/x", "/api/radio/segments//audio",
            "/api/radio/segments/a/b/correction", "/api/radio/alerts/1",
            "/api/radio/alerts/1/dismiss/x", "/mindshare", "/mindshare/radio/x",
            "/mindshare/radiox", "/api/mindshare/status",
        )
        for path in yes:
            with self.subTest(path=path):
                self.assertTrue(radio_access.is_radio_human_path(path))
        for path in no:
            with self.subTest(path=path):
                self.assertFalse(radio_access.is_radio_human_path(path))

    def test_the_page_exception_is_exact(self):
        self.assertTrue(radio_access.is_radio_page("/mindshare/radio"))
        self.assertTrue(radio_access.is_radio_page("/mindshare/radio/?a=b"))
        self.assertFalse(radio_access.is_radio_page("/mindshare/radio/extra"))
        self.assertFalse(radio_access.is_radio_page("/api/radio/segments"))


# ============================================================== HTTP routes


class CorrectionRouteTests(_RouteCase):
    def setUp(self):
        super().setUp()
        self.service, self.store, self.s3 = build_service()
        seed(self.service, transcript="unit four log in county")
        self.use_service(self.service)
        self.sign_in_as(ADMIN)
        self.url = f"/api/radio/segments/{SEGMENT_ID}/correction"

    def test_put_saves_a_correction_and_returns_the_updated_item(self):
        response = self.client.put(self.url, json={"text": "Unit four, Logan County."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        item = response.json()
        self.assertEqual(item["text"], "Unit four, Logan County.")
        self.assertTrue(item["corrected"])
        self.assertEqual(item["corrected_by"], ADMIN_EMAIL)
        self.assertEqual(self.store.corrections[0]["actor"], ADMIN_EMAIL)

    def test_every_human_role_can_correct_and_revert(self):
        for role, identity in HUMAN_ROLES:
            self.sign_in_as(identity)
            with self.subTest(role=role):
                saved = self.client.put(self.url, json={"text": f"By {role}."})
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()["corrected_by"], identity.email)
                reverted = self.client.delete(self.url)
                self.assertEqual(reverted.status_code, 200)
                self.assertFalse(reverted.json()["corrected"])

    def test_delete_reverts_and_keeps_the_retention(self):
        self.client.put(self.url, json={"text": "Kept."})
        response = self.client.delete(self.url)
        self.assertEqual(response.status_code, 200)
        item = response.json()
        self.assertFalse(item["corrected"])
        self.assertIsNotNone(item["retained_until"])
        self.assertEqual(item["text"], "unit four log in county")

    def test_bad_bodies_are_400_415_or_413(self):
        for label, kwargs, status in (
            ("not json", {"content": b"{{", "headers": {"content-type": "application/json"}}, 400),
            ("empty body", {"content": b"", "headers": {"content-type": "application/json"}}, 400),
            ("array", {"json": ["x"]}, 400),
            ("string", {"json": "x"}, 400),
            ("no text", {"json": {}}, 400),
            ("extra key", {"json": {"text": "x", "by": "me"}}, 400),
            ("text not a string", {"json": {"text": 5}}, 400),
            ("blank", {"json": {"text": "   "}}, 400),
            ("too long", {"json": {"text": "x" * 20001}}, 400),
            ("control char", {"json": {"text": "a\u0000b"}}, 400),
            ("form encoded", {"data": {"text": "x"}}, 415),
            ("plain text", {"content": b'{"text":"x"}', "headers": {"content-type": "text/plain"}}, 415),
        ):
            with self.subTest(label):
                response = self.client.put(self.url, **kwargs)
                self.assertEqual(response.status_code, status, response.text[:120])
        self.assertEqual(self.store.corrections, [])

    def test_an_oversized_body_is_413(self):
        response = self.client.put(
            self.url,
            content=b'{"text":"' + b"x" * (130 * 1024) + b'"}',
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_unknown_segment_is_404(self):
        for method in ("put", "delete"):
            kwargs = {"json": {"text": "x"}} if method == "put" else {}
            for segment_id in ("law_rx_never_stored", "short"):
                with self.subTest(method=method, segment=segment_id):
                    response = getattr(self.client, method)(
                        f"/api/radio/segments/{segment_id}/correction", **kwargs
                    )
                    self.assertEqual(response.status_code, 404)

    def test_a_cross_site_browser_write_is_refused(self):
        headers = {"sec-fetch-site": "cross-site"}
        self.assertEqual(
            self.client.put(self.url, json={"text": "x"}, headers=headers).status_code, 403
        )
        self.assertEqual(self.client.delete(self.url, headers=headers).status_code, 403)
        self.assertEqual(
            self.client.post("/api/radio/alerts/1/dismiss", headers=headers).status_code, 403
        )
        self.assertEqual(self.store.corrections, [])
        # Same-origin and header-less (script) requests are fine.
        same = {"sec-fetch-site": "same-origin"}
        self.assertEqual(
            self.client.put(self.url, json={"text": "x"}, headers=same).status_code, 200
        )

    def test_a_failed_audit_write_is_503_and_nothing_changes(self):
        self.store.fail_audit = True
        response = self.client.put(self.url, json={"text": "Will not save."})
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.store.segments[SEGMENT_ID][12])
        self.assertEqual(self.store.corrections, [])
        self.assertNotIn("Will not save", response.text)

    def test_a_storage_outage_is_503_and_nothing_changes(self):
        self.s3.copy_failure = "down"
        response = self.client.put(self.url, json={"text": "Will not save."})
        self.assertEqual(response.status_code, 503)
        self.assertIsNone(self.store.segments[SEGMENT_ID][12])

    def test_the_corrected_audio_plays_through_the_proxy(self):
        self.client.put(self.url, json={"text": "Kept."})
        response = self.client.get(f"/api/radio/segments/{SEGMENT_ID}/audio")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, make_wav())
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.store.segments[SEGMENT_ID][10].split("/")[1], "corrected")

    def test_the_list_carries_the_new_fields_over_http(self):
        self.client.put(self.url, json={"text": "Seen by all."})
        self.sign_in_as(HUMAN_ROLES[2][1])  # a plain user
        item = self.client.get("/api/radio/segments").json()["segments"][0]
        self.assertEqual(item["text"], "Seen by all.")
        self.assertEqual(item["transcript"], "Seen by all.")
        self.assertEqual(item["machine_transcript"], "unit four log in county")
        self.assertTrue(item["corrected"])
        self.assertEqual(item["corrected_by"], ADMIN_EMAIL)
        self.assertIsNotNone(item["retained_until"])


class FixRouteTests(_RouteCase):
    def setUp(self):
        super().setUp()
        self.service, self.store, _ = build_service()
        self.use_service(self.service)
        self.sign_in_as(ADMIN)

    def test_add_list_and_remove(self):
        created = self.client.post(
            "/api/radio/replacements", json={"find": "log in", "replace": "Logan"}
        )
        self.assertEqual(created.status_code, 201, created.text)
        rule = created.json()
        self.assertEqual((rule["find"], rule["replace"], rule["whole_word"]), ("log in", "Logan", True))
        listing = self.client.get("/api/radio/replacements").json()
        self.assertEqual(len(listing["replacements"]), 1)
        self.assertEqual(listing["max_rules"], 200)
        removed = self.client.delete(f"/api/radio/replacements/{rule['replacement_id']}")
        self.assertEqual(removed.status_code, 200)
        self.assertEqual(self.client.get("/api/radio/replacements").json()["replacements"], [])
        self.assertEqual(audit_types(self.store), ["radio.fix.add", "radio.fix.remove"])

    def test_error_statuses(self):
        post = lambda body: self.client.post("/api/radio/replacements", json=body)
        self.assertEqual(post({"find": "", "replace": "x"}).status_code, 400)
        self.assertEqual(post({"find": "a", "replace": "a"}).status_code, 400)
        self.assertEqual(post({"find": "a", "replace": "b", "regex": 1}).status_code, 400)
        self.assertEqual(post({"find": "a", "replace": "b"}).status_code, 201)
        self.assertEqual(post({"find": "A", "replace": "c"}).status_code, 409)
        self.assertEqual(self.client.delete("/api/radio/replacements/999").status_code, 404)
        self.assertEqual(self.client.delete("/api/radio/replacements/abc").status_code, 404)
        self.assertEqual(
            self.client.post("/api/radio/replacements", content=b"{",
                             headers={"content-type": "application/json"}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/radio/replacements", data={"find": "a"}).status_code, 415)

    def test_a_failed_audit_write_is_503_and_nothing_changes(self):
        self.store.fail_audit = True
        response = self.client.post("/api/radio/replacements", json={"find": "a", "replace": "b"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.store.replacements, [])


class WatchTermRouteTests(_RouteCase):
    def setUp(self):
        super().setUp()
        self.service, self.store, _ = build_service()
        self.use_service(self.service)
        self.sign_in_as(ADMIN)

    def test_add_list_change_remove(self):
        created = self.client.post(
            "/api/radio/watch-terms", json={"phrase": "structure fire", "channels": ["fire"]}
        )
        self.assertEqual(created.status_code, 201, created.text)
        term_id = created.json()["term_id"]
        listing = self.client.get("/api/radio/watch-terms").json()
        self.assertEqual(listing["terms"][0]["phrase"], "structure fire")
        self.assertEqual(listing["max_terms"], 100)
        changed = self.client.patch(f"/api/radio/watch-terms/{term_id}", json={"enabled": False})
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(changed.json()["enabled"])
        self.assertEqual(self.client.delete(f"/api/radio/watch-terms/{term_id}").status_code, 200)
        self.assertEqual(
            audit_types(self.store),
            ["radio.watch.add", "radio.watch.change", "radio.watch.remove"],
        )

    def test_error_statuses(self):
        post = lambda body: self.client.post("/api/radio/watch-terms", json=body)
        self.assertEqual(post({"phrase": "a"}).status_code, 400)
        self.assertEqual(post({"phrase": "fire", "channels": ["BAD"]}).status_code, 400)
        self.assertEqual(post({"phrase": "fire"}).status_code, 201)
        self.assertEqual(post({"phrase": "FIRE"}).status_code, 409)
        self.assertEqual(self.client.patch("/api/radio/watch-terms/1", json={}).status_code, 400)
        self.assertEqual(self.client.patch("/api/radio/watch-terms/999", json={"enabled": True}).status_code, 404)
        self.assertEqual(self.client.delete("/api/radio/watch-terms/999").status_code, 404)
        self.assertEqual(self.client.delete("/api/radio/watch-terms/abc").status_code, 404)


class AlertRouteTests(_RouteCase):
    def setUp(self):
        super().setUp()
        self.service, self.store, _ = build_service()
        add_term(self.service, "traffic stop")
        seed(self.service)  # "...traffic stop at the test lot."
        self.use_service(self.service)

    def test_every_human_role_sees_and_dismisses_the_shared_alert(self):
        self.sign_in_as(HUMAN_ROLES[2][1])
        alerts = self.client.get("/api/radio/alerts")
        self.assertEqual(alerts.status_code, 200)
        self.assertEqual(alerts.headers["cache-control"], "private, no-store")
        body = alerts.json()
        self.assertEqual(len(body["alerts"]), 1)
        alert_id = body["alerts"][0]["alert_id"]
        # A different role sees the same alert, then dismisses it for everyone.
        self.sign_in_as(HUMAN_ROLES[4][1])
        self.assertEqual(len(self.client.get("/api/radio/alerts").json()["alerts"]), 1)
        dismissed = self.client.post(f"/api/radio/alerts/{alert_id}/dismiss")
        self.assertEqual(dismissed.status_code, 200)
        self.assertEqual(dismissed.json(), {"alert_id": alert_id, "dismissed": True})
        self.sign_in_as(ADMIN)
        self.assertEqual(self.client.get("/api/radio/alerts").json()["alerts"], [])
        event = [e for e in self.store.audit if e["event_type"] == "radio.alert.dismiss"][0]
        self.assertEqual(event["actor"], HUMAN_ROLES[4][1].email)

    def test_polling_does_not_write_audit_rows(self):
        self.sign_in_as(ADMIN)
        before = len(self.store.audit)
        for _ in range(3):
            self.client.get("/api/radio/alerts")
        self.assertEqual(len(self.store.audit), before)

    def test_dismiss_errors(self):
        self.sign_in_as(ADMIN)
        self.assertEqual(self.client.post("/api/radio/alerts/999/dismiss").status_code, 404)
        self.assertEqual(self.client.post("/api/radio/alerts/abc/dismiss").status_code, 404)
        self.store.fail_audit = True
        self.assertEqual(self.client.post("/api/radio/alerts/1/dismiss").status_code, 503)
        self.assertIsNone(self.store.alerts[0]["dismissed_at"])

    def test_the_avatar_and_unidentified_callers_see_nothing(self):
        for role, identity in DENIED_CALLERS:
            self.sign_in_as(identity)
            with self.subTest(role=role):
                self.assertEqual(self.client.get("/api/radio/alerts").status_code, 403)
                self.assertEqual(self.client.post("/api/radio/alerts/1/dismiss").status_code, 403)
        self.assertIsNone(self.store.alerts[0]["dismissed_at"])

    def test_a_signed_ingest_raises_an_alert_that_humans_then_see(self):
        # End to end through the machine route: signature in, alert out.
        segment_id = "law_rx_20261016_150000_0002"
        metadata = encode(make_metadata(
            segment_id=segment_id,
            transcript="Second traffic stop on Route 10.",
            started_at="2026-10-16T15:00:00+00:00",
            ended_at="2026-10-16T15:00:05+00:00",
        ))
        audio = make_wav()
        timestamp = int(time.time())
        self.sign_in_as(None)
        posted = self.client.post(
            "/api/radio/ingest",
            headers={
                "X-Radio-Timestamp": str(timestamp),
                "X-Radio-Signature": sign(timestamp, segment_id, audio, metadata),
            },
            files={
                "metadata": ("metadata.json", metadata, "application/json"),
                "audio": ("segment.wav", audio, "audio/wav"),
            },
        )
        self.assertEqual(posted.status_code, 201, posted.text)
        self.sign_in_as(HUMAN_ROLES[3][1])
        alerts = self.client.get("/api/radio/alerts").json()["alerts"]
        self.assertIn(segment_id, [a["segment_id"] for a in alerts])


if __name__ == "__main__":
    unittest.main()
