import json
import unittest
from unittest.mock import patch

from app.services.cloud_pilot_readiness_service import get_cloud_pilot_readiness


class CloudPilotReadinessContractTests(unittest.TestCase):
    def test_static_view_has_all_expected_modules_and_keeps_cad_disconnected(self):
        view = get_cloud_pilot_readiness().to_dict()
        modules = {module["key"]: module for module in view["modules"]}

        self.assertEqual(view["contract_version"], "1.0")
        self.assertEqual(view["deployment_mode"], "synthetic-disconnected")
        self.assertEqual(view["overall_state"], "not-activated")
        self.assertEqual(view["source"], "static-contract")
        self.assertEqual(
            set(modules),
            {
                "dashboard",
                "analytics_import",
                "document_library",
                "rag",
                "voice",
                "cad_read_only",
            },
        )
        self.assertEqual(modules["cad_read_only"]["state"], "disconnected")
        self.assertTrue(all(module["action_available"] is False for module in modules.values()))

    def test_view_contains_no_connection_or_sensitive_details(self):
        payload = get_cloud_pilot_readiness().to_dict()
        serialized = json.dumps(payload).lower()

        for forbidden in (
            "arn:",
            "http://",
            "https://",
            "password",
            "credential",
            "endpoint",
            "access_key",
            "secret_key",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)

        for module in payload["modules"]:
            self.assertEqual(
                set(module),
                {"key", "label", "state", "summary", "advisory_only", "action_available"},
            )

    def test_api_is_static_and_does_not_initialize_external_clients(self):
        from fastapi.testclient import TestClient

        from app.main import app

        with (
            patch("app.main.CentralSquareClient") as cad_client,
            patch("app.main.get_access_token") as access_token,
        ):
            response = TestClient(app).get("/api/pilot/readiness")

        cad_client.assert_not_called()
        access_token.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), get_cloud_pilot_readiness().to_dict())
        self.assertNotIn("Internal Server Error", response.text)


if __name__ == "__main__":
    unittest.main()
