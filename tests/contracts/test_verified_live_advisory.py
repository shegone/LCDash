"""Network-free contracts for the verified-live-fact phrasing advisory."""

import unittest

from app.integrations.cloud_ai.live_data import LiveDataSource, VerifiedFact
from app.integrations.cloud_ai.verified_live_advisory import (
    VerifiedLiveAdvisory,
    VerifiedLiveResponse,
)


class _Client:
    def __init__(self, text="There are currently 3 active calls."):
        self.calls = []
        self._text = text

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "output": {"message": {"content": [{"text": self._text}]}},
        }


class _DenyingBudget:
    def reserve(self):
        return False


class _AllowingBudget:
    def __init__(self):
        self.reserved = 0

    def reserve(self):
        self.reserved += 1
        return True


def _facts():
    return (VerifiedFact("Currently active calls", "3"),)


def _sources():
    return (
        LiveDataSource(
            "CentralSquare CAD (current read-only snapshot)",
            "live",
            "Freshness: fresh",
            True,
            "5s old",
        ),
    )


class VerifiedLiveResponseTests(unittest.TestCase):
    def test_supported_requires_nonempty_answer(self):
        with self.assertRaises(ValueError):
            VerifiedLiveResponse("req-0000001", "", (), denied=False)

    def test_supported_rejects_a_denial_reason(self):
        with self.assertRaises(ValueError):
            VerifiedLiveResponse(
                "req-0000001", "answer", (), denied=False, denial_reason="x"
            )

    def test_denied_requires_a_reason_and_forbids_an_answer(self):
        with self.assertRaises(ValueError):
            VerifiedLiveResponse("req-0000001", "", (), denied=True)
        with self.assertRaises(ValueError):
            VerifiedLiveResponse(
                "req-0000001", "answer", (), denied=True, denial_reason="x"
            )

    def test_action_execution_is_never_allowed(self):
        with self.assertRaises(ValueError):
            VerifiedLiveResponse(
                "req-0000001", "answer", (), denied=False, action_executed=True
            )

    def test_answer_length_is_bounded(self):
        with self.assertRaises(ValueError):
            VerifiedLiveResponse("req-0000001", "x" * 801, (), denied=False)

    def test_supported_and_deny_factories_round_trip(self):
        response = VerifiedLiveResponse.supported("req-0000001", "3 calls", _sources())
        self.assertFalse(response.denied)
        self.assertEqual(response.data_sources, _sources())

        denial = VerifiedLiveResponse.deny("req-0000001", "no data")
        self.assertTrue(denial.denied)
        self.assertEqual(denial.answer, "")


class VerifiedLiveAdvisoryTests(unittest.TestCase):
    def test_rejects_output_token_bounds_outside_32_300(self):
        with self.assertRaises(ValueError):
            VerifiedLiveAdvisory(converse_client=_Client(), model_id="m", max_output_tokens=10)
        with self.assertRaises(ValueError):
            VerifiedLiveAdvisory(converse_client=_Client(), model_id="m", max_output_tokens=400)

    def test_no_facts_denies_without_calling_the_model(self):
        client = _Client()
        advisory = VerifiedLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            facts=(),
            data_sources=(),
        )
        self.assertTrue(response.denied)
        self.assertEqual(response.denial_reason, "No verified live data matched this question.")
        self.assertEqual(client.calls, [])

    def test_supported_answer_phrases_the_given_facts_at_zero_temperature(self):
        client = _Client("There are currently 3 active calls.")
        advisory = VerifiedLiveAdvisory(converse_client=client, model_id="test-model")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            facts=_facts(),
            data_sources=_sources(),
        )
        self.assertFalse(response.denied)
        self.assertEqual(response.answer, "There are currently 3 active calls.")
        self.assertEqual(response.data_sources, _sources())
        self.assertEqual(len(client.calls), 1)
        call = client.calls[0]
        self.assertEqual(call["modelId"], "test-model")
        self.assertEqual(call["inferenceConfig"]["temperature"], 0.0)
        self.assertIn("Currently active calls: 3", call["messages"][0]["content"][0]["text"])
        self.assertIn("only the facts given", call["system"][0]["text"])

    def test_empty_model_response_denies_cleanly(self):
        client = _Client("")
        advisory = VerifiedLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            facts=_facts(),
            data_sources=_sources(),
        )
        self.assertTrue(response.denied)
        self.assertEqual(response.denial_reason, "The verified-live response was empty.")

    def test_budget_exhaustion_denies_without_calling_the_model(self):
        client = _Client()
        advisory = VerifiedLiveAdvisory(
            converse_client=client, model_id="m", budget=_DenyingBudget()
        )
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            facts=_facts(),
            data_sources=_sources(),
        )
        self.assertTrue(response.denied)
        self.assertIn("daily advisory usage limit", response.denial_reason)
        self.assertEqual(client.calls, [])

    def test_shared_budget_is_actually_reserved_once_per_answer(self):
        budget = _AllowingBudget()
        advisory = VerifiedLiveAdvisory(converse_client=_Client(), model_id="m", budget=budget)
        advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            facts=_facts(),
            data_sources=_sources(),
        )
        self.assertEqual(budget.reserved, 1)

    def test_answer_is_truncated_to_the_explicit_output_limit(self):
        client = _Client("x" * 5000)
        advisory = VerifiedLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            facts=_facts(),
            data_sources=_sources(),
        )
        self.assertLessEqual(len(response.answer), 800)

    def test_invalid_request_identity_is_rejected_before_any_model_call(self):
        client = _Client()
        advisory = VerifiedLiveAdvisory(converse_client=client, model_id="m")
        with self.assertRaises(ValueError):
            advisory.answer(
                request_id="bad",
                tenant_id="logan-synthetic",
                facts=_facts(),
                data_sources=_sources(),
            )
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
