import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.scoring_funnel import evaluate_item_score

class ScoringFunnelTests(unittest.TestCase):
    def test_recommend_item(self):
        item = {"title": "Advanced AI Architecture in Production", "summary": "Detailed deep dive into LLM inference scaling.", "content": "x" * 1500, "fetch_method": "github_repo"}
        score, label, action = evaluate_item_score(item)
        self.assertGreaterEqual(score, 80)
        self.assertEqual(action, "recommend")

    def test_reject_clickbait_item(self):
        item = {"title": "震惊！千万别错过这个颠覆行业的技术", "summary": "速看绝密内幕", "content": "short"}
        score, label, action = evaluate_item_score(item)
        self.assertLess(score, 60)
        self.assertEqual(action, "reject")

if __name__ == '__main__':
    unittest.main()
