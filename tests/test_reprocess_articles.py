import tempfile
import unittest
from pathlib import Path

from scripts.reprocess_articles import (
    clean_daily_summary_cells,
    clean_summaries,
    clean_summary_document,
)


ARTICLE = '''---
链接: "https://example.com/article"
---

## 总结

有效摘要。了解更多：https://example.com/details

## 文章正文

正文链接 https://example.com/body 应保持不变。

## 相关链接

- [今日总结](../../今日总结.md)
'''


class ReprocessSummaryTests(unittest.TestCase):
    def test_article_cleanup_is_scoped_to_summary_and_related_links(self):
        updated, changed = clean_summary_document(ARTICLE)
        self.assertTrue(changed)
        self.assertIn('链接: "https://example.com/article"', updated)
        self.assertIn('正文链接 https://example.com/body 应保持不变。', updated)
        self.assertIn('有效摘要。', updated)
        self.assertNotIn('了解更多', updated)
        self.assertNotIn('## 相关链接', updated)

    def test_daily_cleanup_changes_only_summary_cell(self):
        daily = (
            '| 发布时间 | 中文标题 | 文章总结 |\n'
            '| --- | --- | --- |\n'
            '| 2026-07-30 | [标题](./article.md) | 摘要。阅读全文：https://example.com |\n'
        )
        updated, changed = clean_daily_summary_cells(daily)
        self.assertTrue(changed)
        self.assertIn('[标题](./article.md)', updated)
        self.assertIn('| 摘要。 |', updated)
        self.assertNotIn('https://example.com', updated)

    def test_all_history_migration_only_writes_changed_documents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / '自动获取信息'
            date_dir = root / '2026-07-30'
            article_dir = date_dir / '信息源' / '测试源'
            article_dir.mkdir(parents=True)
            article_path = article_dir / 'article.md'
            article_path.write_text(ARTICLE, encoding='utf-8')
            daily_path = date_dir / '今日总结.md'
            daily_path.write_text(
                '| 发布时间 | 中文标题 | 文章总结 |\n'
                '| --- | --- | --- |\n'
                '| now | [标题](./article.md) | 摘要。阅读原文：https://example.com |\n',
                encoding='utf-8',
            )

            self.assertEqual((1, 1), clean_summaries(root))
            self.assertNotIn('## 相关链接', article_path.read_text(encoding='utf-8'))
            self.assertNotIn('https://example.com |', daily_path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
