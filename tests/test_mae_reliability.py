import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.mae_evaluation_service import (
    _score_result,
    get_evaluation_case,
    list_evaluation_cases,
)
from app.services.mae_tool_registry import get_mae_tool_catalog


class MAEReliabilityServiceTests(unittest.TestCase):
    def test_catalog_contains_fifty_supervisor_style_cases(self):
        cases = list_evaluation_cases()
        self.assertEqual(len(cases), 50)
        self.assertEqual(len({case["case_id"] for case in cases}), 50)

    def test_live_case_requires_live_source_and_read_only_result(self):
        case = get_evaluation_case("live-01")
        score = _score_result(
            case,
            {
                "answer": "There are three active calls.",
                "sources": [
                    {
                        "kind": "live",
                        "available": True,
                    }
                ],
                "write_access": False,
            },
        )
        self.assertTrue(score["passed"])

    def test_write_safety_case_requires_inquiry_only_wording(self):
        case = get_evaluation_case("safety-01")
        score = _score_result(
            case,
            {
                "answer": "I completed that dispatch.",
                "sources": [],
                "write_access": False,
            },
        )
        self.assertFalse(score["passed"])
        self.assertFalse(score["answer_check_passed"])

    def test_tool_catalog_has_no_write_tools(self):
        catalog = get_mae_tool_catalog()
        self.assertFalse(catalog["write_tools_enabled"])
        self.assertEqual(catalog["mode"], "read-only")
        self.assertGreaterEqual(catalog["tool_count"], 10)

    def test_cad_inquiry_broker_is_allowlisted_and_excludes_commands(self):
        catalog = get_mae_tool_catalog()
        operations = catalog["cad_inquiry_operations"]
        operation_ids = {operation["id"] for operation in operations}
        routes = " ".join(operation["route"] for operation in operations)

        self.assertEqual(
            operation_ids,
            {
                "cfs_detail",
                "active_operations",
                "recent_arrivals",
                "unit_roster",
            },
        )
        self.assertNotIn("run_command", routes)
        self.assertNotIn("PUT ", routes)


class MAEReliabilityPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.main.list_memory_items", return_value=[])
    @patch("app.main.list_feedback_review", return_value=[])
    @patch(
        "app.main.get_evaluation_summary",
        return_value={
            "total_runs": 2,
            "passed_runs": 2,
            "pass_rate": 100,
            "average_duration_ms": 900,
        },
    )
    def test_reliability_page_loads(
        self,
        summary_mock,
        feedback_mock,
        memory_mock,
    ):
        response = self.client.get("/mae/reliability")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Reliability Center", response.text)
        self.assertIn("live-01", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    @patch("app.main.run_evaluation_case")
    def test_run_endpoint_uses_authenticated_user(self, run_mock):
        run_mock.return_value = {
            "case_id": "live-01",
            "passed": True,
            "duration_ms": 100,
        }
        response = self.client.post(
            "/api/mae/evaluations/run",
            json={"case_id": "live-01"},
            headers={"cf-access-authenticated-user-email": "boss@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        run_mock.assert_called_once_with(
            "live-01",
            requested_by="boss@example.com",
            tenant_context=None,
        )


if __name__ == "__main__":
    unittest.main()
