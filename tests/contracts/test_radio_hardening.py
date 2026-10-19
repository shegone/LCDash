"""Two fixes found by running the radio SQL against a real PostgreSQL (2026-10-18)."""

from __future__ import annotations

import unittest
from unittest import mock

import psycopg

from app.services import radio_service
from app.services.radio_service import (
    RadioDatabaseError,
    RadioQueryError,
    RadioService,
)


class _Repo:
    """Minimal repository whose schema apply fails a set number of times."""

    def __init__(self, failures, error_factory):
        self.failures = failures
        self.error_factory = error_factory
        self.calls = 0
        self.entered = 0
        self.exited = 0

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.exited += 1

    def initialize_schema(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error_factory()


def _deadlock():
    error = RadioDatabaseError("A PostgreSQL radio operation failed.")
    error.__cause__ = psycopg.errors.DeadlockDetected("deadlock detected")
    return error


def _other_failure():
    error = RadioDatabaseError("A PostgreSQL radio operation failed.")
    error.__cause__ = psycopg.errors.UniqueViolation("duplicate key")
    return error


def _service(repo):
    return RadioService(
        bucket="test-bucket",
        audio_prefix="radio/audio/",
        repository_factory=lambda: repo,
    )


class SchemaApplyRetryTests(unittest.TestCase):
    def test_a_deadlocked_schema_apply_is_retried_and_then_succeeds(self):
        repo = _Repo(failures=2, error_factory=_deadlock)
        service = _service(repo)
        with mock.patch.object(radio_service.time, "sleep") as sleep:
            service._open()
        self.assertEqual(repo.calls, 3)
        self.assertTrue(service._schema_ready)
        self.assertEqual(sleep.call_count, 2)

    def test_it_gives_up_after_three_attempts_and_releases_the_connection(self):
        repo = _Repo(failures=5, error_factory=_deadlock)
        service = _service(repo)
        with mock.patch.object(radio_service.time, "sleep"):
            with self.assertRaises(RadioDatabaseError):
                service._open()
        self.assertEqual(repo.calls, 3)
        self.assertFalse(service._schema_ready)
        self.assertEqual(repo.exited, 1)

    def test_other_database_errors_are_not_retried(self):
        repo = _Repo(failures=1, error_factory=_other_failure)
        service = _service(repo)
        with mock.patch.object(radio_service.time, "sleep") as sleep:
            with self.assertRaises(RadioDatabaseError):
                service._open()
        self.assertEqual(repo.calls, 1)
        sleep.assert_not_called()


class QueryControlCharacterTests(unittest.TestCase):
    def test_control_characters_in_q_are_a_400_not_a_database_error(self):
        service = _service(_Repo(failures=0, error_factory=_deadlock))
        for bad in ("a\x00b", "line\nbreak", "tab\there", "\x1b[0m"):
            with self.subTest(q=bad):
                with self.assertRaises(RadioQueryError):
                    service.list_segments(actor="a@example.test", q=bad)

    def test_ordinary_text_is_not_rejected_by_the_control_character_check(self):
        service = _service(_Repo(failures=0, error_factory=_deadlock))
        # Anything other than the control-character message means it got past
        # that check (the fake repository then fails for its own reasons).
        try:
            service.list_segments(actor="a@example.test", q="10-4 medic 4")
        except RadioQueryError as exc:
            self.assertNotIn("invalid characters", str(exc))
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
