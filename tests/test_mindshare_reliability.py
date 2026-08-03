import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.mindshare_coverage_service import (
    build_mindshare_coverage,
    classify_document,
    identify_product,
)
from app.services.mindshare_evaluation_service import (
    _score_mindshare_result,
    get_mindshare_evaluation_case,
    list_mindshare_evaluation_cases,
)


class MindshareEvaluationServiceTests(unittest.TestCase):
    def test_catalog_has_thirty_realistic_cases(self):
        cases = list_mindshare_evaluation_cases()
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({case["case_id"] for case in cases}), 30)
        self.assertEqual(
            {case["category"] for case in cases},
            {
                "Console operation",
                "MRI and MRI2",
                "Gateways",
                "Service procedures",
                "Versions and releases",
                "Safety boundaries",
            },
        )

    def test_supported_case_requires_expected_document(self):
        case = get_mindshare_evaluation_case("jack-mri-01")
        score = _score_mindshare_result(
            case,
            {
                "answer": "Use the documented MRI2 update procedure.",
                "evidence": [
                    {"file_name": "MRI2 Manual Software Update v1.00.pdf"}
                ],
                "assurance": {"level": "supported"},
            },
            15000,
        )
        self.assertTrue(score["passed"])

    def test_unsupported_case_accepts_safe_refusal(self):
        case = get_mindshare_evaluation_case("jack-boundary-01")
        score = _score_mindshare_result(
            case,
            {
                "answer": "I cannot provide or reveal administrator passwords.",
                "evidence": [],
                "assurance": {"level": "limited"},
            },
            500,
        )
        self.assertTrue(score["passed"])


class MindshareCoverageServiceTests(unittest.TestCase):
    def test_document_type_and_product_detection(self):
        document = {
            "file_name": "MS_MRI2_UM_v1.05.pdf",
            "title": "MRI2 Manual",
        }
        self.assertEqual(classify_document(document), "User manual")
        self.assertEqual(identify_product(document), "MRI2")

    def test_coverage_flags_zero_content_and_revision_group(self):
        documents = [
            {
                "file_name": "MS1014_AN_MRIToHytera_v101.pdf",
                "title": "MRI to Hytera",
                "chunk_count": 4,
            },
            {
                "file_name": "MS1014_AN_MRIToHytera_v102.pdf",
                "title": "MRI to Hytera",
                "chunk_count": 5,
            },
            {
                "file_name": "Readme.md",
                "title": "Readme",
                "chunk_count": 0,
            },
        ]
        coverage = build_mindshare_coverage(
            documents,
            {"drive_sync": {"status": "not_reported"}},
        )
        self.assertEqual(coverage["summary"]["documents"], 3)
        self.assertEqual(len(coverage["zero_content"]), 1)
        self.assertEqual(len(coverage["duplicate_groups"]), 1)


class MindshareReliabilityPageTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch(
        "app.main.get_mindshare_evaluation_summary",
        return_value={
            "total_runs": 0,
            "passed_runs": 0,
            "pass_rate": 0,
            "average_duration_ms": 0,
            "recent_runs": [],
        },
    )
    @patch("app.main.list_jack_memory_items", return_value=[])
    @patch("app.main.list_jack_feedback", return_value=[])
    def test_reliability_page_loads(self, feedback_mock, memory_mock, summary_mock):
        response = self.client.get("/mindshare/reliability")
        self.assertEqual(response.status_code, 200)
        self.assertIn("JACK Reliability Center", response.text)
        self.assertIn("Supervisor-Approved JACK Knowledge", response.text)
        self.assertIn('id="jack-memory-form"', response.text)
        self.assertIn("jack-console-01", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    @patch("app.main.get_knowledge_status", return_value={"drive_sync": {}})
    @patch(
        "app.main.list_knowledge_documents",
        return_value=[
            {
                "file_name": "MS_MRI2_UM_v1.05.pdf",
                "title": "MRI2",
                "chunk_count": 12,
            }
        ],
    )
    def test_coverage_page_loads(self, documents_mock, status_mock):
        response = self.client.get("/mindshare/coverage")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Mindshare Library Coverage", response.text)
        self.assertIn("MRI2", response.text)

    @patch("app.main.run_mindshare_evaluation_case")
    def test_run_endpoint_records_authenticated_user(self, run_mock):
        run_mock.return_value = {
            "case_id": "jack-console-01",
            "passed": True,
            "duration_ms": 100,
        }
        response = self.client.post(
            "/api/mindshare/evaluations/run",
            json={"case_id": "jack-console-01"},
            headers={"cf-access-authenticated-user-email": "boss@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        run_mock.assert_called_once_with(
            "jack-console-01",
            requested_by="boss@example.com",
        )

    @patch("app.main.record_jack_feedback")
    def test_feedback_endpoint_records_authenticated_user(self, feedback_mock):
        feedback_mock.return_value = {
            "saved": True,
            "interaction_id": "11111111-1111-1111-1111-111111111111",
            "rating": "helpful",
        }
        response = self.client.post(
            "/api/mindshare/feedback",
            json={
                "interaction_id": "11111111-1111-1111-1111-111111111111",
                "rating": "helpful",
                "comment": "",
            },
            headers={"cf-access-authenticated-user-email": "boss@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        feedback_mock.assert_called_once_with(
            interaction_id="11111111-1111-1111-1111-111111111111",
            user_email="boss@example.com",
            rating="helpful",
            comment="",
        )

    @patch("app.main.create_jack_memory_candidate")
    def test_memory_candidate_endpoint_records_authenticated_user(self, create_mock):
        create_mock.return_value = {"saved": True, "memory_id": 4, "status": "pending"}
        response = self.client.post(
            "/api/mindshare/memory",
            json={
                "title": "Local console label",
                "trigger_text": "console label",
                "guidance": "Use the supervisor-approved local label.",
                "source_interaction_id": "",
            },
            headers={"cf-access-authenticated-user-email": "boss@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            title="Local console label",
            trigger_text="console label",
            guidance="Use the supervisor-approved local label.",
            created_by="boss@example.com",
            source_interaction_id=None,
        )

    @patch("app.main.review_jack_memory")
    def test_memory_review_endpoint_records_authenticated_user(self, review_mock):
        review_mock.return_value = {"saved": True, "memory_id": 4, "status": "approved"}
        response = self.client.post(
            "/api/mindshare/memory/review",
            json={"memory_id": 4, "decision": "approved"},
            headers={"cf-access-authenticated-user-email": "boss@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        review_mock.assert_called_once_with(
            memory_id=4,
            decision="approved",
            reviewed_by="boss@example.com",
        )


if __name__ == "__main__":
    unittest.main()
