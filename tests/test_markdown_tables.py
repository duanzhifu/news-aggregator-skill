import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.push_to_obsidian import (
    build_article_markdown,
    build_daily_summary_markdown,
    clean_markdown_cell,
    clean_summary,
    encode_markdown_path,
    fetch_candidate_content,
    get_article_summary,
    is_publishable,
    parse_publish_datetime,
    format_publish_datetime,
    markdown_table,
    write_recommendation_audit,
    load_rejection_index,
    normalized_rejection_kind,
    rejection_identity,
    write_rejection_collection,
)


class MarkdownTableTests(unittest.TestCase):
    def test_summary_removes_aihot_read_original_suffix(self):
        summary = "完整文章总结。 🔗 阅读原文 via AI HOT · https://example.com/item/1"
        self.assertEqual("完整文章总结。", clean_summary(summary))

    def test_summary_removes_aihot_suffix_without_url(self):
        summary = "完整文章总结。 🔗 阅读原文 via AI HOT ·"
        self.assertEqual("完整文章总结。", clean_summary(summary))

    def test_summary_removes_all_link_forms(self):
        summary = "项目已经发布 [GitHub](https://example.com/repo)，文档见 <https://example.com/docs>。"
        self.assertEqual("项目已经发布，文档见。", clean_summary(summary))

    def test_summary_removes_link_call_to_action(self):
        summary = "这是产品愿景。 阅读我们构建它的原因以及未来方向：https://example.com/post"
        self.assertEqual("这是产品愿景。", clean_summary(summary))

    def test_summary_removes_learn_more_link(self):
        summary = "该工具可阻止高风险操作。了解更多：https://example.com/post"
        self.assertEqual("该工具可阻止高风险操作。", clean_summary(summary))

    def test_cell_cleans_pipes_newlines_and_long_text(self):
        cleaned = clean_markdown_cell("first | second\nthird", max_length=18)
        self.assertNotIn("|", cleaned)
        self.assertNotIn("\n", cleaned)
        self.assertLessEqual(len(cleaned), 18)

    def test_summary_falls_back_to_original_summary_when_translation_is_empty(self):
        item = {"summary_zh": "", "summary": "原始 RSS 摘要"}
        self.assertEqual("原始 RSS 摘要", get_article_summary(item))

    def test_summary_falls_back_to_content_when_feed_summary_is_empty(self):
        item = {"summary_zh": "", "summary": "", "content": "正文内容"}
        self.assertEqual("正文内容", get_article_summary(item))

    def test_article_uses_original_summary_when_translation_is_empty(self):
        markdown, _, _ = build_article_markdown(
            {"title": "Test", "summary_zh": "", "summary": "原始摘要"},
            "Feed",
            "",
        )
        self.assertIn("原始摘要", markdown)

    def test_recommendation_filter_and_failure_fallback(self):
        self.assertTrue(is_publishable({"recommendation_level": "strongly_recommended"}))
        self.assertTrue(is_publishable({"recommendation_level": "optional"}))
        self.assertFalse(is_publishable({"recommendation_level": "not_recommended"}))
        self.assertFalse(is_publishable({"recommendation_level": "evaluation_failed"}))

    def test_missing_publication_time_is_not_replaced_with_fetch_time(self):
        date_value, dt_value = parse_publish_datetime({"title": "No time", "time": "Today"})
        self.assertEqual("未知", date_value)
        self.assertEqual("发布时间未知", format_publish_datetime(dt_value))

    def test_source_timestamp_is_preserved(self):
        date_value, dt_value = parse_publish_datetime({"created_at_i": 1760000000})
        self.assertNotEqual("未知", date_value)
        self.assertNotEqual("发布时间未知", format_publish_datetime(dt_value))

    def test_candidate_content_fetch_preserves_order_and_skips_missing_urls(self):
        items = [{"title": "A", "url": "https://example.com/a"}, {"title": "B", "url": ""}]

        def fake_enrich(candidates):
            candidates[0]["content"] = "正文"
            return candidates

        with patch("scripts.fetch_news.enrich_items_with_content", side_effect=fake_enrich):
            result = fetch_candidate_content(items)
        self.assertEqual(["A", "B"], [item["title"] for item in result])
        self.assertEqual("正文", result[0]["content"])
        self.assertNotIn("content", result[1])

    def test_recommendation_audit_omits_body(self):
        item = {
            "title": "Low value",
            "source": "Feed",
            "url": "https://example.com/a",
            "content": "private body must not be logged",
            "quality_score": 20,
            "recommendation_level": "not_recommended",
            "recommendation_reason": "内容不完整",
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch(
            "scripts.push_to_obsidian.Path", side_effect=lambda value='.': Path(temp_dir) / value
        ):
            audit_path = write_recommendation_audit([item], "2026-08-01")
            record = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertNotIn("content", record)
        self.assertEqual("not_recommended", record["recommendation_level"])

    def test_article_contains_recommendation_metadata_and_advice(self):
        markdown, _, _ = build_article_markdown(
            {
                "title": "Test",
                "title_zh": "测试",
                "summary_zh": "中文总结",
                "recommendation_level": "optional",
                "quality_score": 65,
                "recommendation_reason": "有一定参考价值",
                "evaluation_status": "success",
                "prefilter_score": 72,
                "topic_match": "high",
                "prefilter_status": "success",
            },
            "Feed",
            "",
        )
        self.assertIn('推荐等级: "可选阅读"', markdown)
        self.assertIn("质量分数: 65", markdown)
        self.assertIn("评估状态: \"success\"", markdown)
        self.assertNotIn("公共评分", markdown)
        self.assertNotIn("专属评分", markdown)
        self.assertNotIn("预筛选分数", markdown)
        self.assertNotIn('主题匹配: "high"', markdown)
        self.assertIn("## 阅读建议", markdown)
        self.assertIn("有一定参考价值", markdown)

    def test_article_renders_specific_evidence_points(self):
        markdown, _, _ = build_article_markdown(
            {
                "title": "Test",
                "summary_zh": "中文总结",
                "recommendation_level": "optional",
                "recommendation_reason": "有参考价值",
                "evidence_status": "success",
                "evidence_quality": "good",
                "evidence_points": ["页面给出了安装命令", "快照说明了兼容范围"],
            },
            "Feed",
            "",
        )
        self.assertIn("## 判断依据", markdown)
        self.assertIn("页面给出了安装命令", markdown)
        self.assertNotIn("本笔记依据受限长度", markdown)

    def test_rejection_collection_indexes_only_definitive_items(self):
        definitive = {
            "title": "Low value",
            "title_zh": "低价值内容",
            "source": "Dev.to",
            "url": "https://example.com/a/",
            "recommendation_level": "not_recommended",
            "rejection_kind": "definitive",
            "recommendation_reason": "与关注主题无关",
            "evidence_points": ["标题和摘要均未涉及技术内容"],
            "ai_selected": False,
        }
        transient = {
            "title": "Unreadable",
            "source": "Dev.to",
            "url": "https://example.com/b",
            "recommendation_level": "not_recommended",
            "rejection_kind": "transient",
            "recommendation_reason": "正文乱码",
            "evidence_points": ["页面快照无法解码"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            daily_path, daily_count, index_count = write_rejection_collection(
                Path(temp_dir), [definitive, transient], "2026-08-04"
            )
            index = load_rejection_index(Path(temp_dir))
            daily = daily_path.read_text(encoding="utf-8")
            index_markdown = Path(temp_dir, "拒绝总索引.md")
        self.assertEqual(2, daily_count)
        self.assertEqual(1, index_count)
        self.assertIn(rejection_identity(definitive), index)
        self.assertNotIn(rejection_identity(transient), index)
        self.assertFalse(index_markdown.exists())
        self.assertIn("低价值内容", daily)
        self.assertNotIn("Low value", daily)
        self.assertIn("与关注主题无关", daily)
        self.assertIn("页面快照无法解码", daily)

    def test_rejection_identity_normalizes_url_and_source_alias(self):
        first = {"source": "Dev.to", "url": "https://example.com/post/?utm_source=daily"}
        second = {"source_key": "devto", "url": "https://example.com/post"}
        self.assertEqual(rejection_identity(first), rejection_identity(second))

    def test_rejection_identity_normalizes_source_key_and_source_name(self):
        first = {"source_key": "juejin", "source": "掘金热榜", "url": "https://juejin.cn/post/123"}
        second = {"source": "掘金热榜", "url": "https://juejin.cn/post/123"}
        self.assertEqual(rejection_identity(first), rejection_identity(second))

    def test_rejection_daily_page_accumulates_same_day_runs_without_duplicates(self):
        first = {
            "title": "First", "source": "Feed", "url": "https://example.com/first",
            "recommendation_level": "not_recommended", "rejection_kind": "definitive",
            "recommendation_reason": "主题不符",
        }
        second = {
            "title": "Second", "source": "Feed", "url": "https://example.com/second",
            "recommendation_level": "evaluation_failed", "rejection_kind": "transient",
            "evaluation_error": "请求超时",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            write_rejection_collection(Path(temp_dir), [first], "2026-08-04")
            daily_path, daily_count, _ = write_rejection_collection(Path(temp_dir), [second], "2026-08-04")
            daily = daily_path.read_text(encoding="utf-8")
        self.assertEqual(2, daily_count)
        self.assertIn("First", daily)
        self.assertIn("Second", daily)

    def test_missing_rejection_kind_treats_unreadable_snapshot_as_transient(self):
        self.assertEqual("transient", normalized_rejection_kind({
            "recommendation_level": "not_recommended",
            "recommendation_reason": "正文几乎完全是无法解码的乱码",
        }))

    def test_daily_summary_includes_reader_facing_recommendation_level(self):
        markdown = build_daily_summary_markdown(
            {},
            {"Feed": [{
                "title": "Test",
                "title_zh": "测试",
                "url": "https://example.com/a",
                "summary_zh": "中文总结",
                "recommendation_level": "optional",
            }]},
            "2026-08-03",
        )
        self.assertIn("推荐等级", markdown)
        self.assertIn("可选阅读", markdown)

    def test_daily_summary_accepts_list_source_summary(self):
        markdown = build_daily_summary_markdown(
            {"Feed": ["第一条趋势", "第二条趋势"]},
            {"Feed": [{
                "title": "Test", "title_zh": "测试", "url": "https://example.com/a",
                "summary_zh": "中文总结", "recommendation_level": "optional",
            }]},
            "2026-08-03",
        )
        self.assertIn("第一条趋势\n第二条趋势", markdown)

    def test_path_encodes_spaces_parentheses_and_unicode(self):
        encoded = encode_markdown_path("./信息源/A title (2026).md")
        self.assertTrue(encoded.startswith("./"))
        self.assertNotIn(" ", encoded)
        self.assertNotIn("(", encoded)
        self.assertIn("%E4%BF%A1", encoded)

    def test_table_rejects_inconsistent_column_count(self):
        with self.assertRaisesRegex(ValueError, "列数错误"):
            markdown_table(["A", "B"], [["only one"]])

    def test_table_renders_consistent_rows(self):
        lines = markdown_table(["A", "B"], [["x|y", "line\nbreak"]])
        self.assertEqual(3, len(lines))
        self.assertTrue(all(line.count("|") == 3 for line in lines))


if __name__ == "__main__":
    unittest.main()
