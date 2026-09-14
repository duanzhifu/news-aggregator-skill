# -*- coding: utf-8 -*-
"""video_to_article.py 去重层单测：URL 规范化 / simhash / 语义命中 / 索引读写 / 精确指纹键。"""
import os
import tempfile
import unittest

try:
    from video_to_article import (
        _normalize_url, _fingerprint_key, _load_index, _save_index,
        _find_exact_duplicate, _simhash, _hamming, _find_semantic_duplicates,
        _unique_fname, INDEX_PATH,
    )
except ModuleNotFoundError:  # PYTHONPATH 未含 scripts/ 时走包路径
    from scripts.video_to_article import (
        _normalize_url, _fingerprint_key, _load_index, _save_index,
        _find_exact_duplicate, _simhash, _hamming, _find_semantic_duplicates,
        _unique_fname, INDEX_PATH,
    )


class TestNormalizeUrl(unittest.TestCase):
    def test_strips_utm_and_anchor(self):
        self.assertEqual(
            _normalize_url("https://example.com/v/BV1xx?utm_source=wechat&p=2&t=1#abc"),
            "https://example.com/v/BV1xx?p=2&t=1",
        )

    def test_query_sorted(self):
        self.assertEqual(
            _normalize_url("https://example.com/v?a=1&c=3&b=2"),
            "https://example.com/v?a=1&b=2&c=3",
        )

    def test_case_and_trailing_slash(self):
        self.assertEqual(_normalize_url("HTTPS://Example.COM/path/"), "https://example.com/path")


class TestFingerprintKey(unittest.TestCase):
    def test_url_key(self):
        self.assertEqual(
            _fingerprint_key("https://www.bilibili.com/video/BV1xx?utm_source=x"),
            "url:https://www.bilibili.com/video/BV1xx",
        )


class TestSimhash(unittest.TestCase):
    def test_same_text_same_hash(self):
        self.assertEqual(_simhash("A" * 50), _simhash("A" * 50))

    def test_different_text_far(self):
        self.assertGreater(_hamming(_simhash("量子计算与人工智能的前沿"), _simhash("今天的天气非常好适合去公园散步")),
                           _SIMHASH_THRESHOLD_LOCAL())

    def test_near_duplicate_close(self):
        a = "本课程介绍 Harness 企业级应用实战中的上下文工程与记忆管理机制"
        b = "本课程介绍 Harness 企业级应用实战里的上下文工程与记忆管理机制"
        self.assertLessEqual(_hamming(_simhash(a), _simhash(b)), _SIMHASH_THRESHOLD_LOCAL())

    def test_empty_text(self):
        self.assertEqual(_simhash(""), 0)

    def test_short_text(self):
        self.assertEqual(_simhash("哈"), _simhash("哈"))


def _SIMHASH_THRESHOLD_LOCAL():
    try:
        from video_to_article import _SIMHASH_THRESHOLD
    except ModuleNotFoundError:
        from scripts.video_to_article import _SIMHASH_THRESHOLD
    return _SIMHASH_THRESHOLD


class TestIndexRoundtrip(unittest.TestCase):
    def setUp(self):
        self._orig = INDEX_PATH
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)

    def tearDown(self):
        import video_to_article as m
        m.INDEX_PATH = self._orig
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_save_load_roundtrip(self):
        import video_to_article as m
        m.INDEX_PATH = self.tmp.name
        rec = {"records": {"url:https://x": {"topic": "t", "simhash": "0" * 16}}}
        _save_index(rec)
        self.assertEqual(_load_index(), rec)

    def test_load_missing_file(self):
        import video_to_article as m
        m.INDEX_PATH = self.tmp.name + "-nope"
        self.assertEqual(_load_index(), {})


class TestFindExactDuplicate(unittest.TestCase):
    def setUp(self):
        self._orig = INDEX_PATH
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)

    def tearDown(self):
        import video_to_article as m
        m.INDEX_PATH = self._orig
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_hit_returns_record(self):
        import video_to_article as m
        m.INDEX_PATH = self.tmp.name
        rec = {"kind": "url", "topic": "t", "output_md": "out.md", "processed_at": "2026-09-02", "simhash": "0" * 16}
        _save_index({"records": {"url:https://example.com/v": rec}})
        got = _find_exact_duplicate("https://example.com/v/?utm_source=x", "t", self.tmp.name, "2026-09-02")
        self.assertIsNotNone(got)
        self.assertEqual(got["output_md"], "out.md")

    def test_miss_returns_none(self):
        import video_to_article as m
        m.INDEX_PATH = self.tmp.name
        _save_index({"records": {}})
        self.assertIsNone(_find_exact_duplicate("https://example.com/other", "t", self.tmp.name, "2026-09-02"))


class TestUniqueFname(unittest.TestCase):
    """_unique_fname：同专题同标题碰撞时自动加 -2/-3 后缀，防静默覆盖。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_conflict_returns_base(self):
        self.assertEqual(_unique_fname(self.tmp, "2026-09-14 - 视频标题"), "2026-09-14 - 视频标题.md")

    def test_conflict_appends_suffix(self):
        for name in ("2026-09-14 - 视频标题.md", "2026-09-14 - 视频标题-2.md"):
            open(os.path.join(self.tmp, name), "w").close()
        self.assertEqual(_unique_fname(self.tmp, "2026-09-14 - 视频标题"), "2026-09-14 - 视频标题-3.md")


class TestFindSemanticDuplicates(unittest.TestCase):
    def _rec(self, topic, simhash_hex, title="t"):
        return {"topic": topic, "simhash": simhash_hex, "title": title, "output_md": "x.md"}

    def test_same_topic_hit(self):
        h = f"{_simhash('这是一段关于机器学习模型的视频转录文本'):016x}"
        index = {"records": {"k1": self._rec("Ai课程", h)}}
        hits = _find_semantic_duplicates(index, "Ai课程", _simhash("这是一段关于机器学习模型的视频转录文本"))
        self.assertEqual(len(hits), 1)
        self.assertLessEqual(hits[0][0], _SIMHASH_THRESHOLD_LOCAL())

    def test_other_topic_miss(self):
        h = f"{_simhash('这是一段关于机器学习模型的视频转录文本'):016x}"
        index = {"records": {"k1": self._rec("另一专题", h)}}
        hits = _find_semantic_duplicates(index, "Ai课程", _simhash("这是一段关于机器学习模型的视频转录文本"))
        self.assertEqual(hits, [])

    def test_missing_simhash_skipped(self):
        index = {"records": {"k1": self._rec("Ai课程", None)}}
        hits = _find_semantic_duplicates(index, "Ai课程", _simhash("任意文本"))
        self.assertEqual(hits, [])

    def test_exclude_self_key(self):
        """force 重跑场景：索引残留自身旧记录（同 key），exclude_key 排除后不自指命中。

        同时验证：其他记录（同 key 不同指纹键、但 simhash 相同）仍正常命中。
        """
        text = "这是一段关于机器学习模型的视频转录文本"
        h = f"{_simhash(text):016x}"
        index = {"records": {
            "k_self": self._rec("Ai课程", h, title="自己"),
            "k_twin": self._rec("Ai课程", h, title="双生兄弟"),
        }}
        hits = _find_semantic_duplicates(index, "Ai课程", _simhash(text), exclude_key="k_self")
        titles = [r["title"] for _, r in hits]
        self.assertNotIn("自己", titles)
        self.assertIn("双生兄弟", titles)


if __name__ == "__main__":
    unittest.main()