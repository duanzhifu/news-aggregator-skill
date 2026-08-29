import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import fetch_news, push_to_obsidian
from scripts import social_platforms


class HardeningTests(unittest.TestCase):
    def test_content_fetch_rejects_local_addresses_and_credentials(self):
        self.assertFalse(fetch_news.is_safe_http_url("http://127.0.0.1:8080/admin"))
        self.assertFalse(fetch_news.is_safe_http_url("http://localhost/admin"))
        self.assertFalse(fetch_news.is_safe_http_url("https://user:pass@example.com/"))
        self.assertTrue(fetch_news.is_safe_http_url("https://8.8.8.8/"))

    def test_dedupe_prefers_canonical_url_over_title(self):
        items = [
            {"source": "Feed", "title": "First title", "url": "https://example.com/post#comments"},
            {"source": "Feed", "title": "Updated title", "url": "https://EXAMPLE.com/post"},
            {"source": "Feed", "title": "First title", "url": "https://example.com/other"},
        ]
        result = push_to_obsidian.dedupe_items(items)
        self.assertEqual(2, len(result))
        self.assertEqual("First title", result[0]["title"])
        self.assertEqual("First title", result[1]["title"])

    def test_existing_articles_match_by_url_even_when_title_differs(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = Path(tmp) / "2026-08-26" / "信息源" / "OpenAI Blog" / "old.md"
            article.parent.mkdir(parents=True)
            article.write_text(
                "\n".join([
                    "---",
                    '原文标题: "OpenAI homepage dump"',
                    '来源: "OpenAI Blog"',
                    '链接: "https://openai.com/index/jalapeno-first-results"',
                    "---",
                    "",
                    "## 总结",
                    "",
                    "旧摘要",
                ]),
                encoding="utf-8",
            )
            existing = push_to_obsidian.load_existing_articles(Path(tmp))
            translated = {
                "title": "OpenAI 发布定制推理芯片 Jalapeño 实测结果",
                "source": "openai",
                "url": "https://openai.com/index/jalapeno-first-results?utm_source=bing",
            }
            self.assertTrue(push_to_obsidian.article_already_exists(translated, existing, "OpenAI Blog"))
            other = {
                "title": "OpenAI homepage dump",
                "source": "openai",
                "url": "https://openai.com/index/gpt-4",
            }
            self.assertFalse(push_to_obsidian.article_already_exists(other, existing, "OpenAI Blog"))

    def test_existing_articles_fall_back_to_title_when_url_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            article = Path(tmp) / "2026-08-26" / "信息源" / "掘金热榜" / "old.md"
            article.parent.mkdir(parents=True)
            article.write_text(
                "\n".join([
                    "---",
                    '原文标题: "无链接旧文"',
                    '来源: "掘金热榜"',
                    "---",
                    "",
                ]),
                encoding="utf-8",
            )
            existing = push_to_obsidian.load_existing_articles(Path(tmp))
            self.assertTrue(push_to_obsidian.article_already_exists(
                {"title": "无链接旧文", "source": "juejin", "url": ""},
                existing,
                "掘金热榜",
            ))
            self.assertFalse(push_to_obsidian.article_already_exists(
                {"title": "另一篇无链接", "source": "juejin", "url": ""},
                existing,
                "掘金热榜",
            ))

    def test_site_homepage_is_rejected_but_article_path_is_kept(self):
        from scripts.domain_mapper import is_site_homepage
        self.assertTrue(is_site_homepage("https://openai.com/"))
        self.assertTrue(is_site_homepage("https://www.openai.com"))
        self.assertTrue(is_site_homepage("https://openai.com/index.html"))
        self.assertFalse(is_site_homepage("https://openai.com/index/jalapeno-first-results"))
        self.assertFalse(is_site_homepage("https://python.langchain.ac.cn/docs/tutorials/"))

    def test_time_parser_handles_recent_chinese_values_and_rejects_generic_hot_label(self):
        recent = fetch_news.parse_item_datetime({"time": "30分钟前"})
        self.assertIsNotNone(recent)
        self.assertLess(datetime.now(timezone.utc) - recent, timedelta(hours=1))
        self.assertIsNone(fetch_news.parse_item_datetime({"time": "hot"}))

    def test_course_intro_title_is_kept(self):
        rows = [{
            "title": "AI 课程介绍：从原理到实践",
            "url": "https://www.bilibili.com/video/BV789",
        }]
        result = social_platforms.normalize_rows(rows, "Bilibili", "AI", 5)
        self.assertEqual(1, len(result))

    def test_explicit_paid_course_is_kept_for_downstream_llm(self):
        # 抓取层不再按标题字面词硬删付费课程内容，保留交给下游 LLM 准入层语义判断。
        rows = [{
            "title": "购买 AI 课程，立即报名",
            "url": "https://www.bilibili.com/video/BV790",
        }]
        result = social_platforms.normalize_rows(rows, "Bilibili", "AI", 5)
        self.assertEqual(1, len(result))
        self.assertEqual("购买 AI 课程，立即报名", result[0]["title"])

    def test_juejin_page_datetime_is_used(self):
        response = SimpleNamespace(
            text='<time class="time" datetime="2026-07-28T02:18:34.000Z">2026-07-28</time>',
            raise_for_status=lambda: None,
        )
        with patch.object(fetch_news, "is_safe_http_url", return_value=True), patch.object(
            fetch_news.requests, "get", return_value=response
        ):
            result = fetch_news.fetch_juejin_page_time("https://juejin.cn/post/123")
        self.assertEqual("2026-07-28T02:18:34.000Z", result)

if __name__ == "__main__":
    unittest.main()
