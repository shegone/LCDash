import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.config.settings import settings
from app.services.ems_delay_alert_service import (
    build_delay_alert_message,
    classify_delayed_ems_call,
    evaluate_ems_delay_alerts,
)


NOW = datetime(2026, 7, 27, 16, 0, tzinfo=timezone.utc)


def call_record(**overrides):
    call = {
        "cfs_number": "CFS26-30001",
        "incident_code": "TRANSFER",
        "incident_description": "Transfer / Inter-Facility",
        "location": "100 Main Street",
        "call_datetime": "2026-07-27T15:00:00Z",
        "incident_datetime": "2026-07-27T15:30:00Z",
        "is_scheduled": False,
        "assigned_units": [],
    }
    call.update(overrides)
    return call


def supervisor_unit(**overrides):
    unit = {
        "unit_number": "EMS104",
        "status": "Available",
        "responder": "Test Supervisor",
        "responder_unique_identifier": "7001",
        "responder_username": "test.supervisor",
        "responder_call_sign": "SUP1",
        "cfs_number": "",
        "semantic_status": {},
    }
    unit.update(overrides)
    return unit


class FakeAlertRepository:
    def __init__(self):
        self.initialized = False
        self.states = {}
        self.dry_runs = []
        self.issues = []
        self.resolutions = []
        self.missing_resolutions = []

    def initialize_schema(self):
        self.initialized = True

    def observe_candidate(self, candidate, observed_at):
        return self.states.get(
            candidate["cfs_number"],
            {
                "alert_count": 0,
                "next_notification_at": candidate["eligible_at"],
                "status": "waiting",
            },
        )

    def record_dry_run(self, candidate, **kwargs):
        self.dry_runs.append((candidate, kwargs))

    def record_delivery_issue(self, candidate, **kwargs):
        self.issues.append((candidate, kwargs))

    def resolve_alert(self, cfs_number, **kwargs):
        self.resolutions.append((cfs_number, kwargs))
        return True

    def resolve_missing_alerts(self, cfs_numbers, **kwargs):
        self.missing_resolutions.append((cfs_numbers, kwargs))
        return 0


class EMSDelayAlertTests(unittest.TestCase):
    def test_transfer_becomes_due_from_call_datetime(self):
        candidate = classify_delayed_ems_call(call_record(), now=NOW)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["alert_type"], "transfer")
        self.assertTrue(candidate["is_due"])
        self.assertEqual(
            candidate["eligible_at"].isoformat(),
            "2026-07-27T15:30:00+00:00",
        )

    def test_scheduled_call_uses_incident_datetime(self):
        candidate = classify_delayed_ems_call(
            call_record(
                incident_code="PRESCHED",
                is_scheduled=True,
                call_datetime="2026-07-27T12:00:00Z",
                incident_datetime="2026-07-27T15:45:00Z",
            ),
            now=NOW,
        )

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["alert_type"], "scheduled")
        self.assertFalse(candidate["is_due"])
        self.assertEqual(
            candidate["eligible_at"].isoformat(),
            "2026-07-27T16:15:00+00:00",
        )

    def test_non_monitored_call_is_ignored(self):
        candidate = classify_delayed_ems_call(
            call_record(incident_code="MEDICAL"),
            now=NOW,
        )

        self.assertIsNone(candidate)

    def test_ems_enroute_stops_alerting(self):
        candidate = classify_delayed_ems_call(
            call_record(
                assigned_units=[
                    {
                        "unit_number": "MED60",
                        "agency": "LEASA",
                        "status": "Enroute",
                    }
                ]
            ),
            now=NOW,
        )

        self.assertTrue(candidate["response_started"])

    def test_assigned_only_does_not_stop_alerting(self):
        candidate = classify_delayed_ems_call(
            call_record(
                assigned_units=[
                    {
                        "unit_number": "MED60",
                        "agency": "LEASA",
                        "status": "Assigned",
                    }
                ]
            ),
            now=NOW,
        )

        self.assertFalse(candidate["response_started"])

    def test_message_contains_sequence_cfs_and_delay(self):
        candidate = classify_delayed_ems_call(call_record(), now=NOW)

        message = build_delay_alert_message(candidate, 2)

        self.assertIn("2nd notification", message)
        self.assertIn("CFS26-30001", message)
        self.assertIn("more than 30 minutes", message)

    @patch(
        "app.services.ems_delay_alert_service.get_all_units",
        return_value=[supervisor_unit()],
    )
    @patch(
        "app.services.ems_delay_alert_service.get_active_calls",
        return_value=[
            call_record(),
            call_record(
                cfs_number="CFS26-30002",
                incident_code="PRESCHED",
                is_scheduled=True,
                incident_datetime="2026-07-27T15:45:00Z",
            ),
            call_record(
                cfs_number="CFS26-30003",
                assigned_units=[
                    {
                        "unit_number": "MED60",
                        "agency": "LEASA",
                        "status": "On Scene",
                    }
                ],
            ),
        ],
    )
    def test_evaluator_tracks_waiting_due_and_resolved_calls(
        self,
        active_calls_mock,
        all_units_mock,
    ):
        repository = FakeAlertRepository()

        with patch.object(settings, "ems_delay_alert_mode", "dry_run"):
            result = evaluate_ems_delay_alerts(
                now=NOW,
                client=object(),
                repository=repository,
            )

        self.assertTrue(repository.initialized)
        self.assertEqual(result["monitored_calls"], 3)
        self.assertEqual(result["waiting_calls"], 1)
        self.assertEqual(result["due_calls"], 1)
        self.assertEqual(result["dry_run_notifications"], 1)
        self.assertEqual(result["resolved_alerts"], 1)
        self.assertEqual(result["recipient_units"], ["EMS104"])
        self.assertEqual(len(repository.dry_runs), 1)
        self.assertEqual(repository.dry_runs[0][1]["sequence_number"], 1)
        self.assertEqual(repository.resolutions[0][0], "CFS26-30003")
        self.assertEqual(
            repository.missing_resolutions[0][0],
            {"CFS26-30001", "CFS26-30002", "CFS26-30003"},
        )

    @patch(
        "app.services.ems_delay_alert_service.get_all_units",
        return_value=[],
    )
    @patch(
        "app.services.ems_delay_alert_service.get_active_calls",
        return_value=[call_record()],
    )
    def test_evaluator_records_issue_when_no_supervisor_is_available(
        self,
        active_calls_mock,
        all_units_mock,
    ):
        repository = FakeAlertRepository()

        result = evaluate_ems_delay_alerts(
            now=NOW,
            client=object(),
            repository=repository,
        )

        self.assertEqual(result["dry_run_notifications"], 0)
        self.assertEqual(len(repository.issues), 1)
        self.assertIn(
            "No eligible EMS supervisor",
            repository.issues[0][1]["issue"],
        )


if __name__ == "__main__":
    unittest.main()
