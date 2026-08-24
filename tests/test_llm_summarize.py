import json
import unittest
from unittest.mock import patch

from scripts import llm_summarize


def response(*pairs):
    return json.dumps({"items": [
        {"title_zh": title, "summary_zh": summary, "content_zh": ""}
        for title, summary in pairs
    ]})


class TranslateItemsTests(unittest.TestCase):
    def run_translate(self, items, side_effect, **kwargs):
        with patch.object(llm_summarize, "call_llm", side_effect=side_effect), patch.object(
            llm_summarize.time, "sleep"
        ):
            return llm_summarize.translate_items(items, **kwargs)

    def test_normal_translation_marks_success(self):
        items = [{"source": "Feed", "title": "Original", "content": "Body"}]
        results = self.run_translate(items, [response(("中文标题", "这是两句中文总结。说明核心价值。"))])
        self.assertEqual("中文标题", results[0]["title_zh"])
        self.assertEqual(llm_summarize.TRANSLATION_SUCCESS, results[0]["translation_status"])

    def test_batch_failure_splits_and_isolates_bad_item(self):
        items = [
            {"source": "Feed", "title": "A"},
            {"source": "Feed", "title": "B", "summary": "原始 RSS 摘要"},
        ]
        calls = iter([
            RuntimeError("batch rejected"),
            response(("甲", "甲的中文总结。")),
            RuntimeError("policy"),
            RuntimeError("policy again"),
        ])

        def fake_call(*args, **kwargs):
            value = next(calls)
            if isinstance(value, Exception):
                raise value
            return value

        results = self.run_translate(items, fake_call, batch_size=2)
        self.assertEqual(llm_summarize.TRANSLATION_RETRY_SUCCESS, results[0]["translation_status"])
        self.assertEqual(llm_summarize.TRANSLATION_FAILED, results[1]["translation_status"])
        self.assertEqual("", results[1]["title_zh"])
        self.assertEqual("", results[1]["summary_zh"])
        self.assertEqual("原始 RSS 摘要", results[1]["summary"])

    def test_single_item_uses_minimal_retry(self):
        results = self.run_translate(
            [{"source": "Feed", "title": "A", "content": "sensitive full body"}],
            [RuntimeError("policy"), response(("标题", "简洁的中文总结。"))],
        )
        self.assertEqual(llm_summarize.TRANSLATION_RETRY_SUCCESS, results[0]["translation_status"])

    def test_incomplete_response_is_retried_not_padded_with_english(self):
        items = [{"source": "Feed", "title": "A"}, {"source": "Feed", "title": "B"}]
        results = self.run_translate(
            items,
            [
                json.dumps({"items": [{"title_zh": "甲", "summary_zh": "甲总结"}]}),
                response(("甲", "甲总结")),
                response(("乙", "乙总结")),
            ],
            batch_size=2,
        )
        self.assertEqual(["甲", "乙"], [item["title_zh"] for item in results])
        self.assertTrue(all(item["translation_status"] == llm_summarize.TRANSLATION_RETRY_SUCCESS for item in results))

    def test_sensitive_content_is_sanitized_only_in_prompt(self):
        item = {
            "title": "安全测试 exploit demo",
            "summary": "api_key=secret-value",
            "content": "```bash\nreverse shell command\n```",
        }
        prompt = llm_summarize.build_translate_prompt([item])
        prompt_text = json.dumps(prompt, ensure_ascii=False)
        self.assertNotIn("secret-value", prompt_text)
        self.assertNotIn("reverse shell command", prompt_text)
        self.assertEqual("api_key=secret-value", item["summary"])
        self.assertEqual("安全测试 exploit demo", item["title"])

    def test_feed_summary_is_not_saved_as_translated_article_body(self):
        item = {"source": "Feed", "title": "A", "summary": "RSS 摘要"}
        raw = json.dumps({"items": [{
            "title_zh": "标题",
            "summary_zh": "摘要",
            "content_zh": "模型不应把摘要伪装成正文",
        }]})
        results = self.run_translate([item], [raw])
        self.assertNotIn("content", results[0])

class AiFetchPipelineTests(unittest.TestCase):
    def test_candidate_selection_preserves_ai_time_interpretation(self):
        raw = json.dumps({"items": [{
            "selected": True,
            "title_zh": "中文标题",
            "selection_reason": "当前热榜且主题相关。",
            "published_at": "2026-08-04T09:30:00+08:00",
            "time_kind": "published",
            "time_confidence": "high",
            "time_evidence": "列表显示今天 09:30",
            "rejection_kind": "not_applicable",
            "evidence_points": ["当前热榜且主题相关"],
        }]})
        with patch.object(llm_summarize, "call_llm", return_value=raw), patch.object(
            llm_summarize.time, "sleep"
        ):
            result = llm_summarize.select_candidates([
                {"source": "Feed", "title": "A", "time": "今天 09:30"}
            ])[0]
        self.assertTrue(result["ai_selected"])
        self.assertEqual("published", result["time_kind"])
        self.assertEqual("high", result["time_confidence"])
        self.assertEqual("列表显示今天 09:30", result["time_evidence"])
        self.assertEqual(["当前热榜且主题相关"], result["evidence_points"])
        self.assertEqual("中文标题", result["title_zh"])

    def test_snapshot_processing_combines_translation_and_quality(self):
        chunk_raw = json.dumps({
            "chunk_summary": "项目说明了目标和使用方式。",
            "evidence_points": ["项目提供了明确的使用方式"],
            "time_evidence": "",
            "concerns": "",
        })
        raw = json.dumps({"items": [{
            "title_zh": "中文标题",
            "summary_zh": "快照说明了项目目标和使用方式。",
            "quality_score": 82,
            "recommendation_level": "strongly_recommended",
            "recommendation_reason": "包含可复用的技术信息。",
            "published_at": "",
            "time_kind": "unknown",
            "time_confidence": "unknown",
            "time_evidence": "页面没有明确时间",
            "rejection_kind": "not_applicable",
            "evidence_quality": "good",
            "evidence_points": ["快照说明了项目目标和使用方式"],
        }]})
        with patch.object(llm_summarize, "call_llm", side_effect=[chunk_raw, raw, json.dumps({"Feed": "今日有一条高价值内容。"})]), patch.object(
            llm_summarize.time, "sleep"
        ):
            result = llm_summarize.process_selected_snapshots([
                {"source": "Feed", "title": "A", "evidence_snapshot": "项目快照"}
            ], "batch")
        self.assertEqual("中文标题", result["items"][0]["title_zh"])
        self.assertEqual(82, result["items"][0]["quality_score"])
        self.assertEqual("strongly_recommended", result["items"][0]["recommendation_level"])
        self.assertEqual("good", result["items"][0]["evidence_quality"])

    def test_full_text_chunks_preserve_every_character(self):
        text = "第一段" * 2500 + "\n\n" + "第二段" * 2500
        chunks = llm_summarize.split_evidence_chunks(text, max_chars=1000)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(text.replace("\n\n", ""), "".join(chunks).replace("\n\n", ""))

    def test_full_text_processing_requires_every_chunk_before_final_decision(self):
        item = {"source": "Feed", "title": "A", "evidence_snapshot": "甲" * 6500}
        finding = json.dumps({"chunk_summary": "分段事实", "evidence_points": ["具体事实"], "time_evidence": "", "concerns": ""})
        final = json.dumps({"items": [{
            "title_zh": "中文标题", "summary_zh": "完整文章总结。", "quality_score": 80,
            "recommendation_level": "optional", "recommendation_reason": "完整内容有价值。",
            "published_at": "", "time_kind": "unknown", "time_confidence": "unknown",
            "time_evidence": "", "rejection_kind": "not_applicable",
            "evidence_quality": "good", "evidence_points": ["具体事实"],
        }]})
        with patch.object(llm_summarize, "call_llm", side_effect=[finding, finding, final]):
            result = llm_summarize._process_full_text_item(item)
        self.assertEqual(2, result["evidence_chunk_count"])
        self.assertEqual("optional", result["recommendation_level"])

if __name__ == "__main__":
    unittest.main()
