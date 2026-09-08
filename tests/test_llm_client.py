import unittest
from unittest.mock import patch

from scripts import llm_client
from scripts.llm_client import _parse_response_text, _ProtocolUnsupported


class ParseResponseTextTests(unittest.TestCase):
    """_parse_response_text 抽文本规则（2026-09-08 补测）：
    chat 分支 content 为空时不得回退推理字段（reasoning_content/reasoning）——
    那是思考过程不是答案，直接返回会让推理文本冒充结果（09-08 画像 6029 字推理文本根因）。"""

    def test_chat_content_returns_text(self):
        res = {"choices": [{"message": {"content": "  答案正文  "}}]}
        self.assertEqual("答案正文", _parse_response_text(res))

    def test_chat_content_missing_raises_instead_of_reasoning_fallback(self):
        # content 空、仅 reasoning_content：必须抛异常，不得返回推理文本
        res = {"choices": [{"message": {"reasoning_content": "分析材料...规划结构...", "content": ""}}]}
        with self.assertRaises(RuntimeError):
            _parse_response_text(res)

    def test_chat_content_missing_with_reasoning_key_raises(self):
        # content None、仅 reasoning 字段：同样抛异常
        res = {"choices": [{"message": {"reasoning": "思考过程", "content": None}}]}
        with self.assertRaises(RuntimeError):
            _parse_response_text(res)

    def test_responses_message_output_text(self):
        res = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "画像正文"}]}]}
        self.assertEqual("画像正文", _parse_response_text(res))

    def test_responses_reasoning_only_raises_protocol_unsupported(self):
        # /responses 只有 reasoning 条目：抛 _ProtocolUnsupported 触发降级（09-07 修复行为不变）
        res = {"output": [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "思考"}]}]}
        with self.assertRaises(_ProtocolUnsupported):
            _parse_response_text(res)

    def test_empty_choices_falls_through_to_output_text(self):
        res = {"output_text": "兜底文本"}
        self.assertEqual("兜底文本", _parse_response_text(res))


class PayloadMaxTokensTests(unittest.TestCase):
    """max_tokens=None 时 payload 不发该字段（2026-09-08：distill 取消限制落地）"""

    def test_chat_payload_omits_max_tokens_when_none(self):
        captured = {}

        def fake_post(path, payload):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "ok"}}]}

        with patch.object(llm_client, "_http_post_json", side_effect=fake_post):
            llm_client._call_chat_completions_api(
                [{"role": "user", "content": "hi"}], "m", 0.2, None, False)
        self.assertNotIn("max_tokens", captured["payload"])

    def test_chat_payload_includes_max_tokens_when_set(self):
        captured = {}

        def fake_post(path, payload):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "ok"}}]}

        with patch.object(llm_client, "_http_post_json", side_effect=fake_post):
            llm_client._call_chat_completions_api(
                [{"role": "user", "content": "hi"}], "m", 0.2, 3000, False)
        self.assertEqual(3000, captured["payload"]["max_tokens"])

    def test_responses_payload_omits_max_output_tokens_when_none(self):
        captured = {}

        def fake_post(path, payload):
            captured["payload"] = payload
            return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}

        with patch.object(llm_client, "_http_post_json", side_effect=fake_post):
            llm_client._call_responses_api(
                [{"role": "user", "content": "hi"}], "m", 0.2, None, False)
        self.assertNotIn("max_output_tokens", captured["payload"])


if __name__ == "__main__":
    unittest.main()
