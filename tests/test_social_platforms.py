import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import social_platforms
from scripts import fetch_social_browser
from scripts import setup_social_login


class SocialPlatformTests(unittest.TestCase):
    def test_login_platform_selection(self):
        self.assertEqual(
            ["douyin", "bilibili", "weibo"],
            setup_social_login.selected_platforms("all"),
        )
        self.assertEqual(["weibo"], setup_social_login.selected_platforms("weibo"))

    def test_login_browser_selection(self):
        self.assertEqual("msedge", setup_social_login.browser_channel("edge"))
        self.assertIsNone(setup_social_login.browser_channel("chromium"))

    def test_browser_channel_and_launch_options(self):
        self.assertEqual("msedge", fetch_social_browser.configured_channel({
            "NEWS_AGGREGATOR_BROWSER_CHANNEL": "msedge",
        }))
        self.assertIsNone(fetch_social_browser.configured_channel({
            "NEWS_AGGREGATOR_BROWSER_CHANNEL": "invalid",
        }))
        self.assertEqual("msedge", fetch_social_browser.launch_options("msedge")["channel"])
        self.assertNotIn("channel", fetch_social_browser.launch_options(None))

    def test_browser_profile_requires_existing_directory(self):
        missing = os.path.join(tempfile.gettempdir(), "missing-news-browser-profile")
        self.assertIsNone(fetch_social_browser.configured_profile({
            "NEWS_AGGREGATOR_BROWSER_PROFILE": missing,
        }))

    def test_browser_profile_accepts_existing_directory(self):
        with tempfile.TemporaryDirectory() as profile:
            self.assertEqual(
                os.path.abspath(profile),
                fetch_social_browser.configured_profile({
                    "NEWS_AGGREGATOR_BROWSER_PROFILE": profile,
                }),
            )

    def test_normalize_rows_keeps_matching_metadata(self):
        rows = [{"title": "React 性能优化", "url": "https://www.bilibili.com/video/BV000", "author": "frontend"}]
        items = social_platforms.normalize_rows(rows, "Bilibili", "React", 5)
        self.assertEqual("Bilibili", items[0]["source"])
        self.assertEqual("public_page", items[0]["fetch_method"])

    def test_normalize_rows_drops_non_matching_rows(self):
        rows = [{"title": "旅游攻略", "url": "https://example.test/a"}]
        self.assertEqual([], social_platforms.normalize_rows(rows, "Bilibili", "AI", 5))

    def test_bilibili_drops_paid_cheese_pages(self):
        rows = [{
            "title": "AI 系统课程",
            "url": "https://www.bilibili.com/cheese/play/ep123",
        }]
        self.assertEqual([], social_platforms.normalize_rows(rows, "Bilibili", "AI", 5))

    def test_bilibili_drops_explicit_course_sales(self):
        rows = [{
            "title": "报名购买 AI 训练营课程",
            "url": "https://www.bilibili.com/video/BV123",
        }]
        self.assertEqual([], social_platforms.normalize_rows(rows, "Bilibili", "AI", 5))

    def test_bilibili_keeps_free_technical_video(self):
        rows = [{
            "title": "AI 工程实践公开分享",
            "url": "https://www.bilibili.com/video/BV456",
        }]
        result = social_platforms.normalize_rows(rows, "Bilibili", "AI", 5)
        self.assertEqual("AI 工程实践公开分享", result[0]["title"])

    def test_wechat_keeps_only_article_urls(self):
        rows = [
            {"title": "官网前端文章", "url": "https://example.com/frontend"},
            {"title": "微信公众号前端文章", "url": "https://mp.weixin.qq.com/s/abc123"},
        ]
        result = social_platforms.normalize_rows(rows, "WeChat Official Account", "前端", 5)
        self.assertEqual(1, len(result))
        self.assertEqual("https://mp.weixin.qq.com/s/abc123", result[0]["url"])

    def test_social_promotion_is_rejected(self):
        rows = [{
            "title": "百度百科前端开发推广课程",
            "url": "https://mp.weixin.qq.com/s/promo123",
        }]
        self.assertEqual([], social_platforms.normalize_rows(rows, "WeChat Official Account", "前端", 5))

    def test_deep_content_promotion_is_rejected(self):
        item = {
            "source": "WeChat Official Account",
            "url": "https://mp.weixin.qq.com/s/article123",
            "title": "前端工程实践",
            "content": "本文为商业推广，报名训练营请加微信。",
        }
        self.assertEqual(
            "明确推广或销售内容",
            social_platforms.disallowed_reason(item["source"], item=item),
        )

    def test_browser_search_uses_bounded_subprocess(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps([{"title": "AI", "url": "https://example.test/a"}]), "")
        with patch.object(social_platforms.subprocess, "run", return_value=completed) as run:
            result = social_platforms.browser_search("bilibili", "AI", 3)
        self.assertEqual("AI", result[0]["title"])
        self.assertIn("--limit", run.call_args.args[0])

    def test_bilibili_detail_title_accepts_real_title_and_rejects_metrics(self):
        self.assertTrue(fetch_social_browser._is_valid_bilibili_title(
            "【Vue3极简2025版教程】2个半小时快速学会Vue，效率最高，用时最短！"
        ))
        self.assertFalse(fetch_social_browser._is_valid_bilibili_title("259 0 15:12:11"))

    def test_weibo_hot_normalizes_api_response(self):
        response = unittest.mock.Mock()
        response.json.return_value = {"data": {"realtime": [{"note": "AI", "num": 100}]}}
        response.raise_for_status.return_value = None
        with patch.object(social_platforms.requests, "get", return_value=response):
            result = social_platforms.fetch_weibo_hot(3, "AI")
        self.assertEqual("official_api", result[0]["fetch_method"])
        self.assertEqual("100", result[0]["heat"])

    def test_weibo_search_uses_configured_keywords_without_cli_keyword(self):
        rows = [{"title": "AI 工程实践", "url": "https://example.test/a"}]
        with patch.object(social_platforms, "configured_keywords", return_value=["AI"]), patch.object(
            social_platforms, "configured_api_search", return_value=[]
        ), patch.object(social_platforms, "browser_search", return_value=rows) as browser:
            result = social_platforms.fetch_weibo_search(1)
        self.assertEqual("Weibo Search", result[0]["source"])
        self.assertEqual("AI", browser.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
