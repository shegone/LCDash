import unittest
from unittest.mock import patch

from scripts.register_centralsquare_subscriptions import register_subscriptions


class FakeSubscriptionClient:
    def __init__(self, existing=None):
        self.existing = existing or {}
        self.calls = []
        self.next_id = 40

    def post(self, url, json=None, params=None):
        self.calls.append((url, json, params))
        if url.endswith("/subscriptions/search"):
            callback = json["CallbackURL"]
            existing_id = self.existing.get(callback)
            return {
                "Subscriptions": (
                    [
                        {
                            "CallbackURL": callback,
                            "WebhookUniqueIdentifier": existing_id,
                        }
                    ]
                    if existing_id
                    else []
                )
            }

        self.next_id += 1
        return {"SubscriptionUniqueIdentifier": self.next_id}


class RegisterSubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.agency = {
            "UniqueIdentifier": 1,
            "Abbreviation": "LCEOC",
            "Name": "Logan County Emergency Operations Center",
        }

    @patch(
        "scripts.register_centralsquare_subscriptions.settings.system_base_url",
        "https://system.example/api",
    )
    @patch(
        "scripts.register_centralsquare_subscriptions.settings.cad_base_url",
        "https://cad.example/api",
    )
    def test_registers_precisely_scoped_cfs_and_unit_subscriptions(self):
        client = FakeSubscriptionClient()

        result = register_subscriptions(
            client,
            "https://supervisor.logan911.com",
            "secret:value",
            self.agency,
        )

        self.assertTrue(result["cfs_created"])
        self.assertTrue(result["unit_created"])
        create_calls = [
            call for call in client.calls if not call[0].endswith("/search")
        ]
        self.assertEqual(len(create_calls), 2)
        cfs_body = create_calls[0][1]
        self.assertEqual(cfs_body["DispatchAgency"], [self.agency])
        self.assertTrue(cfs_body["CurrentlyActive"])
        self.assertTrue(cfs_body["ExcludeHistoricalRecordUpdates"])
        self.assertIn("lcdash:secret%3Avalue@", cfs_body["CallbackURL"])
        self.assertNotIn("AVLOnly", create_calls[1][1])

    @patch(
        "scripts.register_centralsquare_subscriptions.settings.system_base_url",
        "https://system.example/api",
    )
    @patch(
        "scripts.register_centralsquare_subscriptions.settings.cad_base_url",
        "https://cad.example/api",
    )
    def test_existing_exact_callbacks_are_not_created_again(self):
        cfs_callback = (
            "https://lcdash:secret@supervisor.logan911.com"
            "/api/integrations/centralsquare/webhooks/cfs"
        )
        unit_callback = (
            "https://lcdash:secret@supervisor.logan911.com"
            "/api/integrations/centralsquare/webhooks/units"
        )
        client = FakeSubscriptionClient(
            {cfs_callback: 10, unit_callback: 11}
        )

        result = register_subscriptions(
            client,
            "https://supervisor.logan911.com",
            "secret",
            self.agency,
        )

        self.assertEqual(result["cfs_subscription_id"], 10)
        self.assertEqual(result["unit_subscription_id"], 11)
        self.assertFalse(result["cfs_created"])
        self.assertFalse(result["unit_created"])
        self.assertEqual(
            [call for call in client.calls if not call[0].endswith("/search")],
            [],
        )


if __name__ == "__main__":
    unittest.main()
