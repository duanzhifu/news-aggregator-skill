import unittest

from scripts.push_to_obsidian import DEFAULT_SOURCE_KEYS


class DefaultSourceTests(unittest.TestCase):
    def test_default_daily_sources_include_social_sources(self):
        self.assertEqual(
            (
                "juejin", "devto", "github", "openai",
                "douyin", "bilibili", "weibo_search", "wechat",
            ),
            DEFAULT_SOURCE_KEYS,
        )


if __name__ == "__main__":
    unittest.main()
