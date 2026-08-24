import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.keyword_expander import expand_keyword

class KeywordExpanderTests(unittest.TestCase):
    def test_expand_empty(self):
        self.assertEqual(expand_keyword(""), [])
        self.assertEqual(expand_keyword(None), [])

    def test_expand_basic(self):
        # 即使 LLM 离线或异常，也要能正常回退返回基础词
        res = expand_keyword("前端开发")
        self.assertIn("前端开发", res)

if __name__ == '__main__':
    unittest.main()
