"""Contracts for the MAE Ollama tool-calling loop (app/services/mae_tool_loop.py).

Network-free: ``httpx.post`` and the tool registry are patched. These assert
the loop's control flow and its safety-relevant fall-through behavior (returns
None -> caller uses the existing plain fallback) so a tool failure can never
break or hang a dispatcher's request.
"""

import unittest
from unittest.mock import Mock, patch

import httpx

import app.services.mae_tool_loop as tool_loop
from app.services.mae_live_tools import LiveToolResult
from app.services.mae_tool_loop import run_mae_tool_loop


def _resp(message: dict) -> Mock:
    r = Mock()
    r.raise_for_status.return_value = None
    r.json.return_value = {"message": message}
    return r


def _tool_call_msg(name="list_active_calls", args=None):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args or {}}}]}


def _final_msg(text):
    return {"role": "assistant", "content": text}


class _FakeRegistry:
    def __init__(self):
        self.calls = []

    def execute(self, name, args):
        self.calls.append((name, args))
        return LiveToolResult(
            tool_name=name,
            source={"name": "CentralSquare live operations", "kind": "live",
                    "detail": "snapshot", "available": True, "timestamp": ""},
            payload={"count": 2},
        )


class ToolLoopTests(unittest.TestCase):
    @patch("app.services.mae_tool_loop.httpx.post")
    def test_zero_tool_calls_returns_none(self, post_mock):
        post_mock.return_value = _resp(_final_msg("I already know this."))
        result = run_mae_tool_loop("what's the weather?", [])
        self.assertIsNone(result)

    @patch("app.services.mae_tool_loop.MaeLiveToolRegistry")
    @patch("app.services.mae_tool_loop.httpx.post")
    def test_one_round_then_final_answer(self, post_mock, registry_cls):
        registry_cls.return_value = _FakeRegistry()
        post_mock.side_effect = [
            _resp(_tool_call_msg()),
            _resp(_final_msg("There are 2 active calls.")),
        ]
        result = run_mae_tool_loop("how many active calls?", [])
        self.assertIsNotNone(result)
        self.assertEqual(result["answer"], "There are 2 active calls.")
        self.assertFalse(result["write_access"])
        self.assertEqual(len(result["sources"]), 1)
        self.assertTrue(result["research"]["live_verified"])
        self.assertEqual(post_mock.call_count, 2)

    @patch("app.services.mae_tool_loop.MaeLiveToolRegistry")
    @patch("app.services.mae_tool_loop.httpx.post")
    def test_tool_result_is_fed_back_with_role_tool(self, post_mock, registry_cls):
        registry_cls.return_value = _FakeRegistry()
        post_mock.side_effect = [
            _resp(_tool_call_msg(name="list_active_calls")),
            _resp(_final_msg("Done.")),
        ]
        run_mae_tool_loop("how many active calls?", [])
        second_messages = post_mock.call_args_list[1].kwargs["json"]["messages"]
        tool_msg = second_messages[-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertEqual(tool_msg["tool_name"], "list_active_calls")
        # payload serialized to a JSON string
        self.assertIn("count", tool_msg["content"])

    @patch("app.services.mae_tool_loop.MaeLiveToolRegistry")
    @patch("app.services.mae_tool_loop.httpx.post")
    def test_round_cap_returns_none(self, post_mock, registry_cls):
        registry_cls.return_value = _FakeRegistry()
        post_mock.return_value = _resp(_tool_call_msg())  # never finishes
        with patch.object(tool_loop.settings, "mae_tool_max_rounds", 3):
            result = run_mae_tool_loop("loop forever", [])
        self.assertIsNone(result)
        self.assertEqual(post_mock.call_count, 4)  # 3 rounds + 1 final attempt

    @patch("app.services.mae_tool_loop.httpx.post")
    def test_ollama_error_returns_none(self, post_mock):
        post_mock.side_effect = httpx.HTTPError("connection refused")
        result = run_mae_tool_loop("how many active calls?", [])
        self.assertIsNone(result)

    @patch("app.services.mae_tool_loop.MaeLiveToolRegistry")
    @patch("app.services.mae_tool_loop.httpx.post")
    def test_empty_final_content_returns_none(self, post_mock, registry_cls):
        registry_cls.return_value = _FakeRegistry()
        post_mock.side_effect = [_resp(_tool_call_msg()), _resp(_final_msg("   "))]
        self.assertIsNone(run_mae_tool_loop("q", []))

    @patch("app.services.mae_tool_loop.MaeLiveToolRegistry")
    @patch("app.services.mae_tool_loop.httpx.post")
    def test_thinking_tags_are_stripped(self, post_mock, registry_cls):
        registry_cls.return_value = _FakeRegistry()
        post_mock.side_effect = [
            _resp(_tool_call_msg()),
            _resp(_final_msg("<think>reasoning</think>The oldest call is CFS26-1.")),
        ]
        result = run_mae_tool_loop("oldest call?", [])
        self.assertEqual(result["answer"], "The oldest call is CFS26-1.")
        self.assertNotIn("think", result["answer"].lower())

    @patch("app.services.mae_tool_loop.MaeLiveToolRegistry")
    @patch("app.services.mae_tool_loop.httpx.post")
    def test_token_callback_emits_final_answer_once(self, post_mock, registry_cls):
        registry_cls.return_value = _FakeRegistry()
        post_mock.side_effect = [_resp(_tool_call_msg()), _resp(_final_msg("Answer."))]
        tokens = []
        run_mae_tool_loop("q", [], token_callback=tokens.append)
        self.assertEqual(tokens, ["Answer."])

    @patch("app.services.mae_tool_loop.MaeLiveToolRegistry")
    @patch("app.services.mae_tool_loop.httpx.post")
    def test_tool_loop_never_streams_to_ollama(self, post_mock, registry_cls):
        registry_cls.return_value = _FakeRegistry()
        post_mock.side_effect = [_resp(_tool_call_msg()), _resp(_final_msg("Answer."))]
        run_mae_tool_loop("q", [])
        for call in post_mock.call_args_list:
            self.assertFalse(call.kwargs["json"]["stream"])
            self.assertFalse(call.kwargs["json"]["think"])

    def test_empty_question_returns_none_without_calling_ollama(self):
        with patch("app.services.mae_tool_loop.httpx.post") as post_mock:
            self.assertIsNone(run_mae_tool_loop("   ", []))
            post_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
