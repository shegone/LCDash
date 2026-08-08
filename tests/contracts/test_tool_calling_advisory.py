"""Network-free contracts for the Bedrock Converse tool-calling loop."""

import unittest

from app.integrations.cloud_ai.live_tools import LiveToolRegistry
from app.integrations.cloud_ai.tool_calling_advisory import ToolCallingLiveAdvisory


class _CadState:
    def __init__(self, calls=()):
        self.calls = tuple(calls)
        self.units = ()


def _registry(calls=(), freshness="fresh"):
    return LiveToolRegistry(
        cad_state=_CadState(calls),
        cad_status={"freshness": freshness, "age_seconds": 5},
        analytics_overview_fn=None,
    )


def _tool_use_message(name, tool_use_id="t1", tool_input=None):
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": tool_use_id,
                            "name": name,
                            "input": tool_input or {},
                        }
                    }
                ],
            }
        },
        "stopReason": "tool_use",
    }


def _final_message(text):
    return {
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
        "stopReason": "end_turn",
    }


class _ScriptedClient:
    """Returns one scripted response per call, in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _AllowingBudget:
    def __init__(self):
        self.reserved = 0

    def reserve(self):
        self.reserved += 1
        return True


class _DenyingBudget:
    def reserve(self):
        return False


class ToolCallingLiveAdvisoryConstructionTests(unittest.TestCase):
    def test_rejects_tool_round_bounds_outside_1_10(self):
        with self.assertRaises(ValueError):
            ToolCallingLiveAdvisory(converse_client=_ScriptedClient([]), model_id="m", max_tool_rounds=0)
        with self.assertRaises(ValueError):
            ToolCallingLiveAdvisory(converse_client=_ScriptedClient([]), model_id="m", max_tool_rounds=11)

    def test_rejects_output_token_bounds_outside_32_1000(self):
        with self.assertRaises(ValueError):
            ToolCallingLiveAdvisory(converse_client=_ScriptedClient([]), model_id="m", max_output_tokens=10)
        with self.assertRaises(ValueError):
            ToolCallingLiveAdvisory(converse_client=_ScriptedClient([]), model_id="m", max_output_tokens=2000)


class ToolCallingLiveAdvisoryAnswerTests(unittest.TestCase):
    def test_zero_tool_calls_returns_none(self):
        client = _ScriptedClient([_final_message("I already know the answer.")])
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="what's the weather like?",
            registry=_registry(),
        )
        self.assertIsNone(response)

    def test_one_tool_round_then_final_answer(self):
        client = _ScriptedClient(
            [
                _tool_use_message("list_active_calls"),
                _final_message("There are 2 active calls."),
            ]
        )
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="how many active calls?",
            registry=_registry(calls=[{"cfs_number": "CFS26-1"}, {"cfs_number": "CFS26-2"}]),
        )
        self.assertIsNotNone(response)
        self.assertFalse(response.denied)
        self.assertEqual(response.answer, "There are 2 active calls.")
        self.assertEqual(len(response.data_sources), 1)
        self.assertEqual(len(client.calls), 2)

        # Second converse call must include the tool result from the first.
        # (``messages`` is mutated in place across the loop, so index by
        # position at the time of the second call rather than the final "-1".)
        second_call_messages = client.calls[1]["messages"]
        tool_result_message = second_call_messages[2]
        self.assertEqual(
            tool_result_message["content"][0]["toolResult"]["toolUseId"], "t1"
        )

    def test_repeated_tool_calls_deduplicate_identical_sources(self):
        client = _ScriptedClient(
            [
                _tool_use_message("list_active_calls", tool_use_id="t1"),
                _tool_use_message("list_active_calls", tool_use_id="t2"),
                _final_message("Still 0 active calls."),
            ]
        )
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="check again, how many calls?",
            registry=_registry(),
        )
        self.assertEqual(len(response.data_sources), 1)

    def test_unknown_tool_name_does_not_raise_and_still_completes(self):
        client = _ScriptedClient(
            [
                _tool_use_message("dispatch_unit"),
                _final_message("I cannot do that."),
            ]
        )
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="dispatch a unit to CFS26-1",
            registry=_registry(),
        )
        self.assertIsNotNone(response)
        self.assertEqual(response.answer, "I cannot do that.")

    def test_max_tool_rounds_is_enforced(self):
        # Always returns tool_use -- the loop must not spin forever.
        responses = [_tool_use_message("list_active_calls") for _ in range(10)]
        client = _ScriptedClient(responses)
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m", max_tool_rounds=3)
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="how many active calls?",
            registry=_registry(),
        )
        self.assertIsNotNone(response)
        self.assertTrue(response.denied)
        self.assertIn("tool-call limit", response.denial_reason)
        # max_tool_rounds tool-use rounds + 1 final attempt = 4 converse calls
        self.assertEqual(len(client.calls), 4)

    def test_budget_is_reserved_once_per_converse_round_trip(self):
        budget = _AllowingBudget()
        client = _ScriptedClient(
            [
                _tool_use_message("list_active_calls"),
                _final_message("2 active calls."),
            ]
        )
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m", budget=budget)
        advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="how many active calls?",
            registry=_registry(),
        )
        self.assertEqual(budget.reserved, 2)

    def test_budget_exhaustion_denies_without_calling_the_model(self):
        client = _ScriptedClient([])
        advisory = ToolCallingLiveAdvisory(
            converse_client=client, model_id="m", budget=_DenyingBudget()
        )
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="how many active calls?",
            registry=_registry(),
        )
        self.assertIsNotNone(response)
        self.assertTrue(response.denied)
        self.assertIn("daily advisory usage limit", response.denial_reason)
        self.assertEqual(client.calls, [])

    def test_answer_is_truncated_to_the_explicit_output_limit(self):
        client = _ScriptedClient(
            [
                _tool_use_message("list_active_calls"),
                _final_message("x" * 5000),
            ]
        )
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="how many active calls?",
            registry=_registry(),
        )
        self.assertLessEqual(len(response.answer), 800)

    def test_empty_question_returns_none_without_calling_the_model(self):
        client = _ScriptedClient([])
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m")
        response = advisory.answer(
            request_id="req-0000001",
            tenant_id="logan-synthetic",
            question="   ",
            registry=_registry(),
        )
        self.assertIsNone(response)
        self.assertEqual(client.calls, [])

    def test_invalid_request_identity_is_rejected_before_any_model_call(self):
        client = _ScriptedClient([])
        advisory = ToolCallingLiveAdvisory(converse_client=client, model_id="m")
        with self.assertRaises(ValueError):
            advisory.answer(
                request_id="bad",
                tenant_id="logan-synthetic",
                question="how many active calls?",
                registry=_registry(),
            )
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
