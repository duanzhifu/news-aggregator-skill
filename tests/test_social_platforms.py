import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts import social_platforms
from scripts import fetch_social_browser
from scripts import setup_social_login


class SocialPlatformTests(unittest.TestCase):
    def test_login_platform_selection(self):
        self.assertEqual(
            ["douyin", "bilibili", "bing"],
            setup_social_login.selected_platforms("all"),
        )

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

    def test_douyin_search_url_normalizes_modal_id(self):
        url = "https://www.douyin.com/search/%E5%89%8D%E7%AB%AF?modal_id=7647330309549100340"
        self.assertEqual(
            "https://www.douyin.com/video/7647330309549100340",
            fetch_social_browser.normalize_url("douyin", url),
        )

    def test_douyin_search_url_requires_modal_id(self):
        url = "https://www.douyin.com/search/frontend"
        self.assertFalse(fetch_social_browser.allowed_url("douyin", url))
        self.assertEqual(url, fetch_social_browser.normalize_url("douyin", url))

    def test_douyin_video_id_attribute_becomes_detail_url(self):
        class Anchor:
            def inner_text(self):
                return "AI 工程实践视频"

            def get_attribute(self, name):
                return {"data-aweme-id": "7647330309549100340"}.get(name)

            def locator(self, selector):
                raise RuntimeError("context is not needed for this test")

        class Anchors:
            def all(self):
                return [Anchor()]

        class Page:
            def locator(self, selector):
                return Anchors()

        result = fetch_social_browser.extract_items(
            Page(), "https://www.douyin.com/search/AI", "douyin", 5
        )
        self.assertEqual("https://www.douyin.com/video/7647330309549100340", result[0]["url"])

    def test_douyin_card_class_extracts_child_video_link(self):
        class Link:
            def inner_text(self):
                return ""

            def get_attribute(self, name):
                return "/video/7647330309549100340"

        class Card:
            def inner_text(self):
                return "AI 工程实践视频"

            def get_attribute(self, name):
                return None

            def locator(self, selector):
                return type("Links", (), {"all": lambda self: [Link()]})()

        class Cards:
            def all(self):
                return [Card()]

        class Page:
            def locator(self, selector):
                self.selector = selector
                return Cards()

        result = fetch_social_browser.extract_items(
            Page(), "https://www.douyin.com/search/AI", "douyin", 5
        )
        self.assertEqual("https://www.douyin.com/video/7647330309549100340", result[0]["url"])

    def test_douyin_direct_video_url_is_normalized(self):
        url = "https://www.douyin.com/video/1234567890123456789?enter_from=search"
        self.assertEqual(
            "https://www.douyin.com/video/1234567890123456789",
            fetch_social_browser.normalize_url("douyin", url),
        )

    def test_douyin_normalization_is_platform_specific(self):
        url = "https://www.douyin.com/search/frontend?modal_id=123"
        self.assertEqual(url, fetch_social_browser.normalize_url("bilibili", url))

    def test_douyin_search_results_dedupe_by_normalized_video_url(self):
        class Anchor:
            def __init__(self, title, href):
                self.title = title
                self.href = href

            def inner_text(self):
                return self.title

            def get_attribute(self, name):
                return self.href if name == "href" else None

            def locator(self, selector):
                raise RuntimeError("context is not needed for this test")

        class Anchors:
            def __init__(self, anchors):
                self.anchors = anchors

            def all(self):
                return self.anchors

        class Page:
            def locator(self, selector):
                return Anchors([
                    Anchor("前端技巧视频", "https://www.douyin.com/search/前端?modal_id=123"),
                    Anchor("frontend tips", "https://www.douyin.com/search/frontend?modal_id=123"),
                ])

        result = fetch_social_browser.extract_items(
            Page(), "https://www.douyin.com/search/前端", "douyin", 5
        )
        self.assertEqual(1, len(result))
        self.assertEqual("https://www.douyin.com/video/123", result[0]["url"])
        self.assertIn("original_url", result[0])

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

    def test_bilibili_keeps_course_sales_for_downstream_llm(self):
        # 抓取层不再按标题字面词硬删课程销售内容，保留交给下游 LLM 准入层语义判断。
        rows = [{
            "title": "报名购买 AI 训练营课程",
            "url": "https://www.bilibili.com/video/BV123",
        }]
        result = social_platforms.normalize_rows(rows, "Bilibili", "AI", 5)
        self.assertEqual(1, len(result))
        self.assertEqual("报名购买 AI 训练营课程", result[0]["title"])

    def test_bilibili_skips_keyword_literal_match(self):
        # bilibili 用 topics 搜索后不再按完整短语做字面子串过滤（B 站搜索已保证相关性，语义交下游 LLM）。
        rows = [{
            "title": "MiniMax H3 最强动态",
            "url": "https://www.bilibili.com/video/BV999",
        }]
        result = social_platforms.normalize_rows(rows, "Bilibili", "大模型最新动态", 5)
        self.assertEqual(1, len(result))
        self.assertEqual("MiniMax H3 最强动态", result[0]["title"])

    def test_douyin_still_keyword_matches(self):
        # douyin 保持原行为：标题不含关键词仍按字面过滤。
        rows = [{
            "title": "与关键词无关的视频",
            "url": "https://www.douyin.com/video/12345678",
        }]
        result = social_platforms.normalize_rows(rows, "Douyin", "AI", 5)
        self.assertEqual([], result)

    def test_bilibili_keeps_free_technical_video(self):
        rows = [{
            "title": "AI 工程实践公开分享",
            "url": "https://www.bilibili.com/video/BV456",
        }]
        result = social_platforms.normalize_rows(rows, "Bilibili", "AI", 5)
        self.assertEqual("AI 工程实践公开分享", result[0]["title"])

    def test_browser_search_uses_bounded_subprocess(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps([{"title": "AI", "url": "https://example.test/a"}]), "")
        with patch.object(social_platforms.subprocess, "run", return_value=completed) as run:
            result = social_platforms.browser_search("bilibili", "AI", 3)
        self.assertEqual("AI", result[0]["title"])
        self.assertIn("--limit", run.call_args.args[0])

    def test_social_search_uses_all_15_default_keywords(self):
        keywords = [f"keyword-{index}" for index in range(15)]
        with patch.object(social_platforms, "configured_keywords", return_value=keywords), \
                patch.object(social_platforms, "configured_api_search", return_value=[]), \
                patch.object(
                    social_platforms,
                    "browser_search",
                    side_effect=lambda platform, query, limit: [{
                        "title": query,
                        "url": f"https://example.test/{query}",
                    }],
                ) as browser:
            social_platforms.fetch_social("douyin", "Douyin", 1)
        self.assertEqual(keywords, [call.args[1] for call in browser.call_args_list])

    def test_bilibili_uses_env_topics_as_keywords(self):
        topics = "大模型最新动态,AI Agent 框架与多智能体协作"
        with patch.dict(os.environ, {"NEWS_AGGREGATOR_TOPICS": topics}), \
                patch.object(social_platforms, "configured_keywords", return_value=["大模型最新动态"]) as kw, \
                patch.object(social_platforms, "configured_api_search", return_value=[]), \
                patch.object(social_platforms, "browser_search", return_value=[]):
            social_platforms.fetch_social("bilibili", "Bilibili", 5)
        self.assertEqual(topics, kw.call_args.args[0])

    def test_bilibili_without_env_topics_falls_back_to_default(self):
        with patch.dict(os.environ, {"NEWS_AGGREGATOR_TOPICS": ""}), \
                patch.object(social_platforms, "configured_keywords", return_value=["前端"]) as kw, \
                patch.object(social_platforms, "configured_api_search", return_value=[]), \
                patch.object(social_platforms, "browser_search", return_value=[]):
            social_platforms.fetch_social("bilibili", "Bilibili", 5)
        self.assertIsNone(kw.call_args.args[0])

    def test_social_search_respects_explicit_query_limit(self):
        keywords = [f"keyword-{index}" for index in range(15)]
        with patch.dict(os.environ, {"SOCIAL_MAX_QUERIES": "5"}), \
                patch.object(social_platforms, "configured_keywords", return_value=keywords), \
                patch.object(social_platforms, "configured_api_search", return_value=[]), \
                patch.object(social_platforms, "browser_search", return_value=[]) as browser:
            social_platforms.fetch_social("douyin", "Douyin", 1)
        self.assertEqual(5, browser.call_count)

    def test_bilibili_detail_title_accepts_real_title_and_rejects_metrics(self):
        self.assertTrue(fetch_social_browser._is_valid_bilibili_title(
            "【Vue3极简2025版教程】2个半小时快速学会Vue，效率最高，用时最短！"
        ))
        self.assertFalse(fetch_social_browser._is_valid_bilibili_title("259 0 15:12:11"))


class BilibiliPubtimeTests(unittest.TestCase):
    def test_bvid_extracted_from_video_url(self):
        self.assertEqual(
            "BV1KHtH61Efm",
            fetch_social_browser._bvid_from_url("https://www.bilibili.com/video/BV1KHtH61Efm/"),
        )
        self.assertEqual(
            "BV1KHtH61Efm",
            fetch_social_browser._bvid_from_url(
                "https://www.bilibili.com/video/BV1KHtH61Efm?vd_source=abc#reply123"
            ),
        )

    def test_bvid_rejects_non_bilibili_host(self):
        self.assertEqual(
            "", fetch_social_browser._bvid_from_url("https://www.douyin.com/video/12345678")
        )
        self.assertEqual("", fetch_social_browser._bvid_from_url("https://example.com/video/BV123"))

    def test_bvid_returns_empty_without_video_path(self):
        self.assertEqual("", fetch_social_browser._bvid_from_url("https://www.bilibili.com/cheese/play/ep123"))

    def test_pubtime_converts_unix_timestamp_to_local_datetime(self):
        payload = json.dumps({"code": 0, "data": {"pubdate": 1788089887}}).encode("utf-8")
        with patch.object(
            fetch_social_browser.urllib.request, "urlopen", return_value=_FakeResp(payload)
        ):
            result = fetch_social_browser._bilibili_pubtime(
                "https://www.bilibili.com/video/BV1KHtH61Efm/"
            )
        # pubdate=1788089887 在 UTC+8 下应为 2026-08-30 19:38:07
        self.assertTrue(
            result.endswith(" 19:38:07"),
            f"expected UTC+8 local time, got {result!r}",
        )
        self.assertTrue(result.startswith("2026-08-30 "), result)

    def test_pubtime_returns_empty_on_error_code(self):
        payload = json.dumps({"code": -400, "message": "请求错误"}).encode("utf-8")
        with patch.object(
            fetch_social_browser.urllib.request, "urlopen", return_value=_FakeResp(payload)
        ):
            self.assertEqual(
                "", fetch_social_browser._bilibili_pubtime("https://www.bilibili.com/video/BV1KHtH61Efm/")
            )

    def test_pubtime_returns_empty_on_network_failure(self):
        with patch.object(
            fetch_social_browser.urllib.request, "urlopen", side_effect=OSError("boom")
        ):
            self.assertEqual(
                "", fetch_social_browser._bilibili_pubtime("https://www.bilibili.com/video/BV1KHtH61Efm/")
            )

    def test_pubtime_returns_empty_without_bvid(self):
        self.assertEqual("", fetch_social_browser._bilibili_pubtime("https://www.bilibili.com/cheese/play/ep123"))


class BiliApiSearchTests(unittest.TestCase):
    """_bilibili_api_search：B站官方搜索 API 直连解析（2026-09-15 发现层改造）。"""

    _VIDEO_ITEM = {
        "type": "video",
        "bvid": "BV1SzYk6HE4B",
        "title": '<em class="keyword">DeepSeek</em> Harness 紧急漏洞',
        "author": "网络小白_Uncle城",
        "play": 80271,
        "like": 11537,
        "review": 243,
        "favorites": 6251,
        "pubdate": 1789378717,
        "description": "漏洞复现演示",
        "arcurl": "http://www.bilibili.com/video/av117268679757898",
    }

    def _payload(self, *groups):
        return {"code": 0, "data": {"result": list(groups)}}

    def _video_group(self, items=None):
        return {"result_type": "video", "data": items or [self._VIDEO_ITEM]}

    def _mock_ok(self, payload):
        fake = type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: payload})()
        return patch.object(social_platforms.requests, "get", return_value=fake)

    def test_strip_html_removes_keyword_highlight(self):
        self.assertEqual(
            "DeepSeek Harness 紧急漏洞",
            social_platforms._strip_html('<em class="keyword">DeepSeek</em> Harness 紧急漏洞'),
        )
        self.assertEqual("A & B", social_platforms._strip_html("A &amp; B"))

    def test_api_search_maps_fields(self):
        with self._mock_ok(self._payload(self._video_group())):
            rows = social_platforms._bilibili_api_search("deepseek harness", 5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["title"], "DeepSeek Harness 紧急漏洞")
        self.assertEqual(row["url"], "https://www.bilibili.com/video/BV1SzYk6HE4B")
        self.assertEqual(row["author"], "网络小白_Uncle城")
        self.assertEqual(row["heat"], "80271")
        self.assertEqual(row["platform_id"], "BV1SzYk6HE4B")
        self.assertEqual(row["fetch_method"], "bilibili_api")
        self.assertEqual(row["view"], 80271)
        self.assertEqual(row["comment"], 243)
        self.assertIn("T", row["time"])

    def test_api_search_time_parses_as_iso(self):
        with self._mock_ok(self._payload(self._video_group())):
            rows = social_platforms._bilibili_api_search("x", 1)
        parsed = datetime.fromisoformat(rows[0]["time"])
        self.assertEqual(parsed.year, 2026)

    def test_api_search_url_passes_allowed_social_url(self):
        with self._mock_ok(self._payload(self._video_group())):
            rows = social_platforms._bilibili_api_search("x", 1)
        self.assertTrue(social_platforms.allowed_social_url("bilibili", rows[0]["url"]))

    def test_api_search_skips_non_video_groups(self):
        payload = self._payload(
            {"result_type": "bili_user", "data": [{"title": "UP主", "url": "https://space.bilibili.com/1"}]},
            self._video_group(),
        )
        with self._mock_ok(payload):
            rows = social_platforms._bilibili_api_search("x", 5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["platform_id"], "BV1SzYk6HE4B")

    def test_api_search_respects_limit(self):
        items = [dict(self._VIDEO_ITEM, bvid=f"BV1{a:02d}xxxx", title=f"title{a}") for a in range(5)]
        with self._mock_ok(self._payload(self._video_group(items))):
            rows = social_platforms._bilibili_api_search("x", 2)
        self.assertEqual(len(rows), 2)

    def test_api_search_returns_empty_when_code_not_zero(self):
        with self._mock_ok({"code": -412, "data": {}}):
            self.assertEqual([], social_platforms._bilibili_api_search("x", 5))

    def test_api_search_returns_empty_on_request_error(self):
        with patch.object(social_platforms.requests, "get", side_effect=OSError("network")):
            self.assertEqual([], social_platforms._bilibili_api_search("x", 5))

    def test_fetch_social_falls_back_to_browser_when_api_empty(self):
        api_rows, browser_rows = [], [{"title": "b", "url": "https://www.bilibili.com/video/BV1xxx"}]
        with patch.object(social_platforms, "_bilibili_api_search", return_value=api_rows) as mock_api, \
                patch.object(social_platforms, "browser_search", return_value=browser_rows) as mock_browser:
            social_platforms.fetch_social("bilibili", "Bilibili", limit=5, keyword="deepseek harness")
            mock_api.assert_called_once()
            mock_browser.assert_called_once()

    def test_fetch_social_uses_api_before_browser(self):
        api_rows = [dict(self._VIDEO_ITEM, url="https://www.bilibili.com/video/BV1SzYk6HE4B")]
        with patch.object(social_platforms, "_bilibili_api_search", return_value=api_rows) as mock_api, \
                patch.object(social_platforms, "browser_search") as mock_browser:
            social_platforms.fetch_social("bilibili", "Bilibili", limit=5, keyword="deepseek harness")
            mock_api.assert_called_once()
            mock_browser.assert_not_called()


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


if __name__ == "__main__":
    unittest.main()
