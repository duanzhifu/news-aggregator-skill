import unittest
from datetime import datetime, timedelta, timezone
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

    def test_time_parser_handles_recent_chinese_values_and_rejects_generic_hot_label(self):
        recent = fetch_news.parse_item_datetime({"time": "30分钟前"})
        self.assertIsNotNone(recent)
        self.assertLess(datetime.now(timezone.utc) - recent, timedelta(hours=1))
        self.assertIsNone(fetch_news.parse_item_datetime({"time": "hot"}))

    def test_ordinary_course_title_is_not_promotion(self):
        rows = [{
            "title": "AI 课程介绍：从原理到实践",
            "url": "https://www.bilibili.com/video/BV789",
        }]
        result = social_platforms.normalize_rows(rows, "Bilibili", "AI", 5)
        self.assertEqual(1, len(result))

    def test_explicit_paid_course_is_filtered(self):
        rows = [{
            "title": "购买 AI 课程，立即报名",
            "url": "https://www.bilibili.com/video/BV790",
        }]
        self.assertEqual([], social_platforms.normalize_rows(rows, "Bilibili", "AI", 5))

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
