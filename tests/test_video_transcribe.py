"""4.8 视频源转录：video_transcribe 单元测试。

覆盖：视频站识别、bvid 提取、bilibili 字幕全链、无登录/无字幕/网络失败降级、
youtube 暂退化、逐条 content 拼接。用 mock 切断真实网络/浏览器。
"""
import time
import unittest
from unittest import mock

from scripts import video_transcribe as vt


class TestIsVideoSite(unittest.TestCase):
    def test_bilibili_true(self):
        self.assertTrue(vt.is_video_site("https://www.bilibili.com/video/BV1w6ZEYrEsb"))

    def test_youtube_true(self):
        self.assertTrue(vt.is_video_site("https://www.youtube.com/watch?v=tklAv8hcG9s"))
        self.assertTrue(vt.is_video_site("https://youtu.be/tklAv8hcG9s"))

    def test_normal_false(self):
        self.assertFalse(vt.is_video_site("https://juejin.cn/post/123"))
        self.assertFalse(vt.is_video_site("https://github.com/foo/bar"))

    def test_empty_false(self):
        self.assertFalse(vt.is_video_site(""))
        self.assertFalse(vt.is_video_site(None))


class TestExtractBvid(unittest.TestCase):
    def test_video_path(self):
        self.assertEqual(vt._extract_bvid("https://www.bilibili.com/video/BV1w6ZEYrEsb"), "BV1w6ZEYrEsb")

    def test_query_param(self):
        self.assertEqual(vt._extract_bvid("https://www.bilibili.com/video/av1?bvid=BV1twZ4YzEmv"), "BV1twZ4YzEmv")

    def test_wrong_host(self):
        self.assertEqual(vt._extract_bvid("https://example.com/video/BV1234"), "")


class TestFetchVideoTranscript(unittest.TestCase):
    """顶层入口：用 mock 把 _fetch_bilibili_transcript / cookie / 网络切断。"""

    @mock.patch("scripts.video_transcribe._fetch_bilibili_transcript", return_value="字幕全文")
    def test_bilibili_calls_transcript(self, m_transcript):
        url = "https://www.bilibili.com/video/BV1w6ZEYrEsb"
        self.assertEqual(vt.fetch_video_transcript(url), "字幕全文")
        m_transcript.assert_called_once_with("BV1w6ZEYrEsb")

    def test_youtube_degraded(self):
        # youtube 暂退化 → 返回空串（不管 cookie / mock）
        self.assertEqual(vt.fetch_video_transcript("https://www.youtube.com/watch?v=x"), "")

    def test_empty_url(self):
        self.assertEqual(vt.fetch_video_transcript(""), "")

    def test_non_video(self):
        self.assertEqual(vt.fetch_video_transcript("https://juejin.cn/post/1"), "")


class TestBilibiliTranscript(unittest.TestCase):
    """bilibili 字幕全链：cookie → view → wbi/v2 → 字幕 body → 拼接。"""

    VIEW = {"code": 0, "data": {"cid": 12345}}
    SUB_LIST = {"code": 0, "data": {"subtitle": {"subtitles": [
        {"lan": "ai-en", "lan_doc": "English", "subtitle_url": "//aisubtitle.hdslb.com/sub1.json"},
        {"lan": "ai-zh", "lan_doc": "中文", "subtitle_url": "//aisubtitle.hdslb.com/sub2.json"},
    ]}}}
    SUB_BODY = {"body": [
        {"from": 0, "to": 1, "content": "第一句。 "},
        {"from": 1, "to": 2, "content": "第二句。"},
        {"from": 2, "to": 3, "content": ""},
        {"from": 3, "to": 4, "content": None},
    ]}

    def _patch_cookie_and_api(self):
        return (
            mock.patch.object(vt, "_get_bilibili_cookie", return_value="SESSDATA=x; bili_jct=y"),
            mock.patch.object(vt, "_api_get", side_effect=[self.VIEW, self.SUB_LIST, self.SUB_BODY]),
        )

    def test_full_chain_prefers_zh(self):
        ck, api = self._patch_cookie_and_api()
        with ck, api:
            text = vt._fetch_bilibili_transcript("BV1w6ZEYrEsb")
        # 应优先 ai-zh（第 2 条），并跳过空/None content，拼接成两行
        self.assertEqual(text, "第一句。\n第二句。")

    def test_no_cookie_degraded(self):
        with mock.patch.object(vt, "_get_bilibili_cookie", return_value=""):
            self.assertEqual(vt._fetch_bilibili_transcript("BV1x"), "")

    def test_no_subtitles_degraded(self):
        ck, api = self._patch_cookie_and_api()
        empty_sub = {"code": 0, "data": {"subtitle": {"subtitles": []}}}
        with ck, mock.patch.object(vt, "_api_get", side_effect=[self.VIEW, empty_sub]):
            self.assertEqual(vt._fetch_bilibili_transcript("BV1x"), "")

    def test_view_error_degraded(self):
        ck, api = self._patch_cookie_and_api()
        err_view = {"code": -400, "message": "no such video"}
        with ck, mock.patch.object(vt, "_api_get", side_effect=[err_view]):
            self.assertEqual(vt._fetch_bilibili_transcript("BV1x"), "")

    def test_api_exception_degraded(self):
        ck, api = self._patch_cookie_and_api()
        with ck, mock.patch.object(vt, "_api_get", side_effect=Exception("network down")):
            self.assertEqual(vt._fetch_bilibili_transcript("BV1x"), "")


class TestFetchBilibiliCookie(unittest.TestCase):
    def test_cookie_when_not_loaded(self):
        with mock.patch.object(vt, "_load_bilibili_cookie", return_value="SESSDATA=x") as m_load:
            vt._BILI_COOKIE_HEADER = None
            vt._BILI_COOKIE_TIME = 0.0
            self.assertEqual(vt._get_bilibili_cookie(), "SESSDATA=x")
            m_load.assert_called_once()

    def test_cookie_cached_within_ttl(self):
        vt._BILI_COOKIE_HEADER = "SESSDATA=cached"
        vt._BILI_COOKIE_TIME = time.time()
        with mock.patch.object(vt, "_load_bilibili_cookie", return_value="SESSDATA=fresh") as m_load:
            self.assertEqual(vt._get_bilibili_cookie(), "SESSDATA=cached")
            m_load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
