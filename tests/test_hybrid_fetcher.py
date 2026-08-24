import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.hybrid_fetcher import fetch_dynamic_source

class HybridFetcherTests(unittest.TestCase):
    def test_fetch_unknown_source(self):
        res = fetch_dynamic_source("unknown_source", keyword="test", limit=2)
        self.assertEqual(res, [])

if __name__ == '__main__':
    unittest.main()
