import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.push_to_obsidian import (
    article_read_key,
    build_article_markdown,
    build_daily_summary_markdown,
    clean_markdown_cell,
    clean_summary,
    encode_markdown_path,
    fetch_candidate_content,
    get_article_summary,
    is_publishable,
    parse_daily_read_states,
    parse_daily_saved_states,
    parse_publish_datetime,
    format_publish_datetime,
    format_time_display,
    markdown_table,
    write_recommendation_audit,
    load_rejection_index,
    normalized_rejection_kind,
    rejection_identity,
    write_rejection_collection,
    write_saved_collection,
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
        self.assertEqual("未知", format_time_display({"title": "No time", "time": "Today"}))

    def test_ranking_observed_displays_as_seen_on_list(self):
        self.assertEqual("热榜见到", format_time_display({
            "title": "Repo",
            "time_kind": "ranking_observed",
        }))

    def test_published_datetime_still_shows_stamp(self):
        label = format_time_display({
            "title": "Post",
            "time_kind": "published",
            "published_at": "2026-08-25T15:05:00+08:00",
        })
        self.assertEqual("2026-08-25 15:05", label)

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
        table_lines = [line for line in markdown.splitlines() if line.startswith('|')]
        self.assertEqual('| 是否阅读 | 发布时间 | 中文标题 | 推荐等级 | 文章总结 | 是否收藏 |', table_lines[0])
        self.assertTrue(table_lines[2].startswith('| [ ] |'))
        self.assertTrue(table_lines[2].rstrip().endswith('| [ ] |'))
        self.assertEqual(table_lines[0].count('|'), table_lines[1].count('|'))
        self.assertEqual(table_lines[0].count('|'), table_lines[2].count('|'))
        self.assertIn("推荐等级", markdown)
        self.assertIn("可选阅读", markdown)

    def test_daily_summary_adds_one_unread_checkbox_per_article(self):
        markdown = build_daily_summary_markdown(
            {},
            {"Feed": [
                {
                    "title": "First", "title_zh": "第一篇", "url": "https://example.com/first",
                    "summary_zh": "第一篇总结", "recommendation_level": "optional",
                },
                {
                    "title": "Second", "title_zh": "第二篇", "url": "https://example.com/second",
                    "summary_zh": "第二篇总结", "recommendation_level": "optional",
                },
            ]},
            "2026-08-03",
        )
        table_rows = [line for line in markdown.splitlines() if line.startswith('| [ ] |')]
        self.assertEqual(2, len(table_rows))
        self.assertIn('[第一篇](', table_rows[0])
        self.assertIn('[第二篇](', table_rows[1])
        self.assertIn('/Feed/', table_rows[0])
        self.assertIn('/Feed/', table_rows[1])

    def _github_item(self, title_zh, summary="摘要"):
        return {
            "title": title_zh,
            "title_zh": title_zh,
            "url": "https://github.com/example/repo",
            "summary_zh": summary,
            "recommendation_level": "optional",
        }

    def test_parse_daily_read_states_uses_decoded_link_path(self):
        filename = "未知-Anthropic官方Claude Code插件目录高Star但需安全注意.md"
        checked_href = encode_markdown_path(f"./信息源/GitHub Trending/{filename}")
        unread_href = encode_markdown_path("./信息源/GitHub Trending/未知-另一篇.md")
        markdown = "\n".join([
            "| 是否阅读 | 发布时间 | 中文标题 | 推荐等级 | 文章总结 |",
            "| --- | --- | --- | --- | --- |",
            f"| [x] | 发布时间未知 | [Anthropic官方]({checked_href}) | 可选阅读 | 旧摘要 |",
            f"| [ ] | 发布时间未知 | [另一篇]({unread_href}) | 可选阅读 | 旧摘要 |",
        ])
        states = parse_daily_read_states(markdown)
        self.assertTrue(states[article_read_key("GitHub Trending", filename)])
        self.assertFalse(states[article_read_key("GitHub Trending", "未知-另一篇.md")])

    def test_rebuild_daily_summary_preserves_checked_checkbox(self):
        title = "Anthropic官方Claude Code插件目录高Star但需安全注意"
        filename = "未知-Anthropic官方Claude Code插件目录高Star但需安全注意.md"
        old_markdown = "\n".join([
            "| 是否阅读 | 发布时间 | 中文标题 | 推荐等级 | 文章总结 |",
            "| --- | --- | --- | --- | --- |",
            f"| [x] | 发布时间未知 | [旧标题]({encode_markdown_path('./信息源/GitHub Trending/' + filename)}) | 可选阅读 | 旧摘要 |",
        ])
        rebuilt = build_daily_summary_markdown(
            {},
            {"GitHub Trending": [self._github_item(title, "新摘要")]},
            "2026-08-26",
            read_states=parse_daily_read_states(old_markdown),
        )
        rows = [line for line in rebuilt.splitlines() if line.startswith("| [")]
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0].startswith("| [x] |"))
        self.assertIn("新摘要", rows[0])

    def test_rebuild_daily_summary_keeps_new_and_unchecked_rows_unread(self):
        old_title = "已有未读文章"
        old_filename = "未知-已有未读文章.md"
        old_markdown = "\n".join([
            "| 是否阅读 | 发布时间 | 中文标题 | 推荐等级 | 文章总结 |",
            "| --- | --- | --- | --- | --- |",
            f"| [ ] | 发布时间未知 | [已有未读文章]({encode_markdown_path('./信息源/GitHub Trending/' + old_filename)}) | 可选阅读 | 旧摘要 |",
        ])
        rebuilt = build_daily_summary_markdown(
            {},
            {"GitHub Trending": [
                self._github_item(old_title),
                self._github_item("新来的一篇"),
            ]},
            "2026-08-26",
            read_states=parse_daily_read_states(old_markdown),
        )
        rows = [line for line in rebuilt.splitlines() if line.startswith("| [")]
        self.assertEqual(2, len(rows))
        self.assertTrue(all(row.startswith("| [ ] |") for row in rows))

    def test_parse_daily_read_states_treats_uppercase_x_as_read(self):
        href = encode_markdown_path("./信息源/掘金热榜/未知-测试.md")
        markdown = f"| [X] | 发布时间未知 | [测试]({href}) | 可选阅读 | 摘要 |"
        states = parse_daily_read_states(markdown)
        self.assertTrue(states[article_read_key("掘金热榜", "未知-测试.md")])

    def _saved_table(self, filename, read="[x]", saved="[x]", title="Foo"):
        href = encode_markdown_path(f"./信息源/GitHub Trending/{filename}")
        return "\n".join([
            "| 是否阅读 | 发布时间 | 中文标题 | 推荐等级 | 文章总结 | 是否收藏 |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| {read} | 发布时间未知 | [{title}]({href}) | 可选阅读 | 摘要 | {saved} |",
        ])

    def test_parse_saved_states_from_last_column(self):
        filename = "未知-Foo.md"
        states = parse_daily_saved_states(self._saved_table(filename, read="[ ]", saved="[x]", title="Foo"))
        key = article_read_key("GitHub Trending", filename)
        self.assertTrue(states[key])
        self.assertFalse(parse_daily_read_states(self._saved_table(filename, read="[ ]", saved="[x]"))[key])

    def test_parse_saved_states_missing_column_is_not_saved(self):
        filename = "未知-Foo.md"
        href = encode_markdown_path(f"./信息源/GitHub Trending/{filename}")
        markdown = "\n".join([
            "| 是否阅读 | 发布时间 | 中文标题 | 推荐等级 | 文章总结 |",
            "| --- | --- | --- | --- | --- |",
            f"| [x] | 发布时间未知 | [Foo]({href}) | 可选阅读 | 摘要 |",
        ])
        key = article_read_key("GitHub Trending", filename)
        self.assertFalse(parse_daily_saved_states(markdown)[key])
        self.assertTrue(parse_daily_read_states(markdown)[key])

    def test_rebuild_daily_summary_preserves_saved_checkbox(self):
        title = "Anthropic官方Claude Code插件目录高Star但需安全注意"
        filename = "未知-Anthropic官方Claude Code插件目录高Star但需安全注意.md"
        old_markdown = self._saved_table(filename, read="[x]", saved="[x]", title="旧标题")
        rebuilt = build_daily_summary_markdown(
            {},
            {"GitHub Trending": [self._github_item(title, "新摘要")]},
            "2026-08-26",
            read_states=parse_daily_read_states(old_markdown),
            saved_states=parse_daily_saved_states(old_markdown),
        )
        rows = [line for line in rebuilt.splitlines() if line.startswith("| [")]
        self.assertEqual(1, len(rows))
        self.assertTrue(rows[0].startswith("| [x] |"))
        self.assertTrue(rows[0].rstrip().endswith("| [x] |"))
        self.assertIn("新摘要", rows[0])
        self.assertIn("是否收藏", rebuilt)

    def test_write_saved_collection_adds_link_and_first_seen(self):
        filename = "未知-Foo.md"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "自动获取信息"
            date_dir = root / "2026-08-26"
            date_dir.mkdir(parents=True)
            (date_dir / "今日总结.md").write_text(
                self._saved_table(filename, read="[ ]", saved="[x]", title="Foo 文章"),
                encoding="utf-8",
            )
            page, count = write_saved_collection(root, report_date="2026-08-27")
            self.assertEqual(1, count)
            text = page.read_text(encoding="utf-8")
            self.assertIn("2026-08-27", text)
            self.assertIn(
                encode_markdown_path(f"../2026-08-26/信息源/GitHub Trending/{filename}"),
                text,
            )
            self.assertIn("Foo 文章", text)
            index = json.loads((root / "收藏集合" / "_收藏索引.json").read_text(encoding="utf-8"))
            record = next(iter(index["records"].values()))
            # 2026-09-03 拍板：first_seen = 首次观察到 [x] 的扫描日 - 1（近似真实勾选日）
            self.assertEqual("2026-08-26", record["first_seen"])

    def test_write_saved_collection_removes_unchecked(self):
        filename = "未知-Foo.md"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "自动获取信息"
            date_dir = root / "2026-08-26"
            date_dir.mkdir(parents=True)
            (date_dir / "今日总结.md").write_text(
                self._saved_table(filename, saved="[x]", title="Foo 文章"),
                encoding="utf-8",
            )
            write_saved_collection(root, report_date="2026-08-26")
            (date_dir / "今日总结.md").write_text(
                self._saved_table(filename, saved="[ ]", title="Foo 文章"),
                encoding="utf-8",
            )
            page, count = write_saved_collection(root, report_date="2026-08-27")
            self.assertEqual(0, count)
            text = page.read_text(encoding="utf-8")
            self.assertNotIn("Foo 文章", text)
            index = json.loads((root / "收藏集合" / "_收藏索引.json").read_text(encoding="utf-8"))
            self.assertEqual({}, index["records"])

    def test_write_saved_collection_keeps_first_seen(self):
        filename = "未知-Foo.md"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "自动获取信息"
            date_dir = root / "2026-08-26"
            date_dir.mkdir(parents=True)
            (date_dir / "今日总结.md").write_text(
                self._saved_table(filename, saved="[x]", title="Foo 文章"),
                encoding="utf-8",
            )
            write_saved_collection(root, report_date="2026-08-26")
            page, count = write_saved_collection(root, report_date="2026-08-27")
            self.assertEqual(1, count)
            record = next(iter(json.loads(
                (root / "收藏集合" / "_收藏索引.json").read_text(encoding="utf-8")
            )["records"].values()))
            # first_seen 粘住：首次写入时 = 扫描日-1（2026-08-26 → 2026-08-25），后续扫描不回跳。
            self.assertEqual("2026-08-25", record["first_seen"])
            self.assertIn("| 2026-08-25 |", page.read_text(encoding="utf-8"))

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

    # === 2026-09-03：收藏理由字段 + 手动收藏 ===

    def test_article_markdown_includes_favorite_reason_field(self):
        markdown, _, _ = build_article_markdown(
            {"title": "Test", "收藏理由": "因为正在做 Agent 项目"},
            "Feed",
            "",
        )
        self.assertIn('收藏理由: "因为正在做 Agent 项目"', markdown)

    def test_article_markdown_favorite_reason_defaults_empty(self):
        markdown, _, _ = build_article_markdown({"title": "Test"}, "Feed", "")
        self.assertIn('收藏理由: ""', markdown)

    def test_write_saved_collection_reads_reason_from_article_note(self):
        filename = "未知-Foo.md"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "自动获取信息"
            date_dir = root / "2026-08-26"
            source_dir = date_dir / "信息源" / "GitHub Trending"
            source_dir.mkdir(parents=True)
            (date_dir / "今日总结.md").write_text(
                self._saved_table(filename, saved="[x]", title="Foo 文章"),
                encoding="utf-8",
            )
            (source_dir / filename).write_text(
                '---\n原文标题: "Foo"\n来源: "GitHub Trending"\n收藏理由: "SDD 实战案例"\n---\n',
                encoding="utf-8",
            )
            page, count = write_saved_collection(root, report_date="2026-08-27")
            self.assertEqual(1, count)
            text = page.read_text(encoding="utf-8")
            self.assertIn("| 收藏时间 | 来源 | 文章 | 收藏理由 |", text)
            self.assertIn("SDD 实战案例", text)
            record = next(iter(json.loads(
                (root / "收藏集合" / "_收藏索引.json").read_text(encoding="utf-8")
            )["records"].values()))
            self.assertEqual("SDD 实战案例", record.get("reason"))

    def test_write_saved_collection_reason_falls_back_dash(self):
        filename = "未知-Foo.md"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "自动获取信息"
            date_dir = root / "2026-08-26"
            date_dir.mkdir(parents=True)
            (date_dir / "今日总结.md").write_text(
                self._saved_table(filename, saved="[x]", title="Foo 文章"),
                encoding="utf-8",
            )
            page, count = write_saved_collection(root, report_date="2026-08-27")
            text = page.read_text(encoding="utf-8")
            self.assertIn("—", text)

    def test_write_saved_collection_preserves_manual_records(self):
        filename = "未知-Foo.md"
        manual = {
            "manual/2026-08-26/abc12345": {
                "id": "manual/2026-08-26/abc12345",
                "title": "外部文章",
                "source": "juejin.cn",
                "url": "https://juejin.cn/post/1",
                "first_seen": "2026-08-26",
                "reason": "手动收藏",
                "kind": "manual",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "自动获取信息"
            date_dir = root / "2026-08-26"
            date_dir.mkdir(parents=True)
            (date_dir / "今日总结.md").write_text(
                self._saved_table(filename, saved="[ ]", title="Foo 文章"),
                encoding="utf-8",
            )
            # 第一次：只有 manual 记录
            page, count = write_saved_collection(root, report_date="2026-08-26", manual_records=manual)
            self.assertEqual(1, count)
            self.assertIn("https://juejin.cn/post/1", page.read_text(encoding="utf-8"))
            # 第二次重建（如每日扫描）：manual 记录必须保留，即使表格没有勾选。
            page, count = write_saved_collection(root, report_date="2026-08-27")
            self.assertEqual(1, count)
            text = page.read_text(encoding="utf-8")
            self.assertIn("外部文章", text)
            self.assertIn("https://juejin.cn/post/1", text)
            records = json.loads(
                (root / "收藏集合" / "_收藏索引.json").read_text(encoding="utf-8")
            )["records"]
            self.assertIn("manual/2026-08-26/abc12345", records)

    def test_manual_record_renders_external_link_not_vault_path(self):
        manual = {
            "manual/2026-08-26/abc12345": {
                "id": "manual/2026-08-26/abc12345",
                "title": "外部文章",
                "source": "juejin.cn",
                "url": "https://juejin.cn/post/1",
                "first_seen": "2026-08-26",
                "reason": "",
                "kind": "manual",
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "自动获取信息"
            page, count = write_saved_collection(root, report_date="2026-08-26", manual_records=manual)
            text = page.read_text(encoding="utf-8")
            self.assertIn("[外部文章](https://juejin.cn/post/1)", text)
            # 外链不应被编码成 vault 相对路径
            self.assertNotIn("../2026-08-26", text)

    def test_manual_source_from_url(self):
        from scripts.push_to_obsidian import _manual_source_from_url
        self.assertEqual("juejin.cn", _manual_source_from_url("https://www.juejin.cn/post/123"))
        self.assertEqual("bilibili.com", _manual_source_from_url("https://www.bilibili.com/video/BV1xx"))
        self.assertEqual("手动收藏", _manual_source_from_url("not-a-url"))

    def test_fetch_page_title_and_desc(self):
        from scripts.push_to_obsidian import _fetch_page_title_and_desc
        html_text = (
            "<html><head><title> 测试标题  </title>"
            '<meta name="description" content="这是描述">'
            "</head><body></body></html>"
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            class FakeResp:
                def read(self, *args):
                    return html_text.encode("utf-8")
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    return False
            mock_urlopen.return_value = FakeResp()
            title, desc = _fetch_page_title_and_desc("https://example.com")
        self.assertEqual("测试标题", title)
        self.assertEqual("这是描述", desc)

    def test_fetch_page_title_and_desc_network_failure(self):
        from scripts.push_to_obsidian import _fetch_page_title_and_desc
        with patch("urllib.request.urlopen", side_effect=OSError("网络失败")):
            title, desc = _fetch_page_title_and_desc("https://example.com")
        self.assertEqual(("", ""), (title, desc))

    def test_add_manual_urls_saved_direct_collect(self):
        from scripts.push_to_obsidian import add_manual_urls
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "自动获取信息").mkdir(parents=True)
            with patch("scripts.push_to_obsidian._fetch_page_title_and_desc",
                       return_value=("测试外部文章", "简介内容")), \
                 patch("scripts.push_to_obsidian.summarize_daily",
                       side_effect=AssertionError("saved 模式不应调用 summarize_daily（零 LLM）")):
                add_manual_urls(
                    ["https://juejin.cn/post/1"],
                    str(vault),
                    note="因为正在做 Agent",
                    saved=True,
                )
            root = vault / "自动获取信息"
            date_dir = root / "2026-09-03"
            # 笔记生成 + 收藏理由写入 frontmatter
            note_files = list((date_dir / "信息源" / "juejin.cn").glob("*.md"))
            self.assertEqual(1, len(note_files))
            note_text = note_files[0].read_text(encoding="utf-8")
            self.assertIn('收藏理由: "因为正在做 Agent"', note_text)
            # 今日总结重建包含该文章
            summary = (date_dir / "今日总结.md").read_text(encoding="utf-8")
            self.assertIn("测试外部文章", summary)
            # 收藏集合有 manual 记录
            records = json.loads(
                (root / "收藏集合" / "_收藏索引.json").read_text(encoding="utf-8")
            )["records"]
            manual = [r for r in records.values() if r.get("kind") == "manual"]
            self.assertEqual(1, len(manual))
            self.assertEqual("因为正在做 Agent", manual[0].get("reason"))
            self.assertEqual("juejin.cn", manual[0].get("source"))
            self.assertEqual("2026-09-03", manual[0].get("first_seen"))

    def test_add_manual_urls_ai_kept_and_rejected(self):
        from scripts.push_to_obsidian import add_manual_urls
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "自动获取信息").mkdir(parents=True)
            with patch("scripts.push_to_obsidian._fetch_page_title_and_desc",
                       return_value=("值得看的文章", "简介")), \
                 patch("scripts.push_to_obsidian.select_for_ai_fetch",
                       return_value=[{"ai_selected": True, "selection_reason": "与主题相关"}]), \
                 patch("scripts.push_to_obsidian.summarize_daily", return_value={}):
                add_manual_urls(["https://example.com/a"], str(vault), saved=False)
            date_dir = vault / "自动获取信息" / "2026-09-03"
            note_files = list((date_dir / "信息源" / "example.com").glob("*.md"))
            self.assertEqual(1, len(note_files))
            # 情况 1 不直接进收藏集合（等用户勾选）
            records = json.loads(
                (vault / "自动获取信息" / "收藏集合" / "_收藏索引.json").read_text(encoding="utf-8")
            )["records"]
            self.assertEqual({}, records)
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            (vault / "自动获取信息").mkdir(parents=True)
            with patch("scripts.push_to_obsidian._fetch_page_title_and_desc",
                       return_value=("不值得看的文章", "简介")), \
                 patch("scripts.push_to_obsidian.select_for_ai_fetch",
                       return_value=[{"ai_selected": False, "selection_reason": "与主题无关"}]), \
                 patch("scripts.push_to_obsidian.summarize_daily", return_value={}):
                add_manual_urls(["https://example.com/b"], str(vault), saved=False)
            date_dir = vault / "自动获取信息" / "2026-09-03"
            note_files = list((date_dir / "信息源" / "example.com").glob("*.md"))
            self.assertEqual(0, len(note_files))


if __name__ == "__main__":
    unittest.main()
