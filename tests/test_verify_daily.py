import tempfile
import unittest
from pathlib import Path

from scripts.verify_daily import verify_daily


EN_ARTICLE = '''---
原文标题: "A Test English Title"
中文标题: "一篇测试中文标题"
来源: "Dev.to"
来源key: "devto"
分类: "程序员社区"
链接: "https://example.com/a"
热度: 7
发布时间: "2026-09-11 10:00"
抓取日期: "2026-09-11"
翻译状态: "success"
推荐等级: "可选阅读"
标签: ["新闻"]
---

## 总结

这是一段干净的测试总结，不含多余链接。

## 文章正文

正文内容。
'''

ZH_ARTICLE = '''---
原文标题: "中文源文章"
中文标题: "中文源文章"
来源: "掘金热榜"
来源key: "juejin"
分类: "国内技术社区"
链接: "https://example.com/b"
热度: 99
发布时间: "2026-09-11 11:00"
抓取日期: "2026-09-11"
标签: ["新闻"]
---

## 总结

中文源的测试总结，无需翻译。

## 文章正文

正文内容。
'''

BAD_TRANSLATION_ARTICLE = '''---
原文标题: "Another English Title"
来源: "GitHub Trending"
来源key: "github"
分类: "开源项目"
链接: "https://example.com/c"
热度: 10
发布时间: "2026-09-11 12:00"
抓取日期: "2026-09-11"
标签: ["新闻"]
---

## 总结

GitHub 上的测试文章。
'''

NOISE_ARTICLE = '''---
原文标题: "A Test English Title"
中文标题: "一篇测试中文标题"
来源: "Dev.to"
来源key: "devto"
分类: "程序员社区"
链接: "https://example.com/a"
热度: 7
发布时间: "2026-09-11 10:00"
抓取日期: "2026-09-11"
翻译状态: "success"
推荐等级: "可选阅读"
标签: ["新闻"]
---

## 总结

这是一段干净总结。了解更多：https://example.com/details

## 文章正文

正文内容。
'''


def make_daily(article_rows):
    header = "| 是否阅读 | 发布时间 | 中文标题 | 推荐等级 | 文章总结 | 是否收藏 |"
    sep = "| --- | --- | --- | --- | --- | --- |"
    lines = [header, sep] + article_rows
    return '\n'.join(lines) + '\n'


class VerifyDailyTests(unittest.TestCase):
    def _vault(self, articles, daily_text):
        """在临时目录构造 vault/自动获取信息/<date>/ 结构，返回 date_dir。"""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        date_dir = Path(temp.name) / '自动获取信息' / '2026-09-11'
        sources = date_dir / '信息源'
        for rel, content in articles:
            article_path = sources / rel
            article_path.parent.mkdir(parents=True, exist_ok=True)
            article_path.write_text(content, encoding='utf-8')
        (date_dir / '今日总结.md').write_text(daily_text, encoding='utf-8')
        return date_dir

    def test_valid_day_passes(self):
        rows = [
            "| [ ] | 2026-09-11 10:00 | [一篇测试中文标题](./信息源/Dev.to/1.md) | 可选阅读 | 这是一段干净的测试总结，不含多余链接。 | [ ] |",
            "| [ ] | 2026-09-11 11:00 | [中文源文章](./信息源/掘金热榜/2.md) | 可选阅读 | 中文源的测试总结，无需翻译。 | [ ] |",
        ]
        date_dir = self._vault(
            [
                ('Dev.to/1.md', EN_ARTICLE),
                ('掘金热榜/2.md', ZH_ARTICLE),
            ],
            make_daily(rows),
        )
        result = verify_daily(date_dir)
        self.assertEqual(2, result['files'])
        self.assertEqual(1, result['translated'])
        self.assertEqual(1, result['untouched_chinese'])
        self.assertEqual(2, result['links'])
        self.assertEqual([], result['missing_links'])
        self.assertEqual([], result['invalid_metadata'])
        self.assertEqual([], result['invalid_summary_noise'])

    def test_expected_counts_mismatch_raises(self):
        rows = [
            "| [ ] | 2026-09-11 10:00 | [一篇测试中文标题](./信息源/Dev.to/1.md) | 可选阅读 | 这是一段干净的测试总结，不含多余链接。 | [ ] |",
        ]
        date_dir = self._vault([('Dev.to/1.md', EN_ARTICLE)], make_daily(rows))
        with self.assertRaises(AssertionError):
            verify_daily(date_dir, expected_counts={'Dev.to': 5})

    def test_missing_link_raises(self):
        # 今日总结中的链接指向不存在的文件 → missing_links 非空
        rows = [
            "| [ ] | 2026-09-11 10:00 | [一篇测试中文标题](./信息源/Dev.to/不存在.md) | 可选阅读 | 这是一段干净的测试总结，不含多余链接。 | [ ] |",
        ]
        date_dir = self._vault([('Dev.to/1.md', EN_ARTICLE)], make_daily(rows))
        with self.assertRaises(AssertionError) as ctx:
            verify_daily(date_dir)
        self.assertNotEqual([], ctx.exception.args[0]['missing_links'])

    def test_untranslated_english_article_raises(self):
        rows = [
            "| [ ] | 2026-09-11 12:00 | [Another English Title](./信息源/GitHub Trending/2.md) | 可选阅读 | GitHub 上的测试文章。 | [ ] |",
        ]
        date_dir = self._vault([('GitHub Trending/2.md', BAD_TRANSLATION_ARTICLE)], make_daily(rows))
        with self.assertRaises(AssertionError) as ctx:
            verify_daily(date_dir)
        self.assertNotEqual([], ctx.exception.args[0]['invalid_metadata'])

    def test_malformed_table_row_raises(self):
        # 第二行竖线数不一致（缺列）→ malformed_rows 非空
        rows = [
            "| [ ] | 2026-09-11 10:00 | [标题A](./信息源/Dev.to/1.md) | 可选阅读 | 总结A。 | [ ] |",
            "| [ ] | 2026-09-11 10:00 | [标题B](./信息源/Dev.to/2.md) | 可选阅读 | 总结B。 |",
        ]
        date_dir = self._vault(
            [
                ('Dev.to/1.md', EN_ARTICLE),
                ('Dev.to/2.md', EN_ARTICLE),
            ],
            make_daily(rows),
        )
        with self.assertRaises(AssertionError) as ctx:
            verify_daily(date_dir)
        self.assertNotEqual([], ctx.exception.args[0]['malformed_rows'])

    def test_summary_noise_raises(self):
        # 文章总结含未清洗的阅读引导 → invalid_summary_noise 非空
        rows = [
            "| [ ] | 2026-09-11 10:00 | [一篇测试中文标题](./信息源/Dev.to/1.md) | 可选阅读 | 这是一段干净总结。了解更多：https://example.com/details | [ ] |",
        ]
        date_dir = self._vault([('Dev.to/1.md', NOISE_ARTICLE)], make_daily(rows))
        with self.assertRaises(AssertionError) as ctx:
            verify_daily(date_dir)
        self.assertNotEqual([], ctx.exception.args[0]['invalid_summary_noise'])


if __name__ == '__main__':
    unittest.main()