import unittest

from scripts.scoring_rules import attach_engagement_scores, attach_source_keys


class ScoringRuleTests(unittest.TestCase):
    def test_attach_source_keys_preserves_input_and_adds_key(self):
        original = {"source": "GitHub Trending", "title": "repo"}
        enriched = attach_source_keys([original])
        self.assertNotIn("source_key", original)
        self.assertEqual("github", enriched[0]["source_key"])

    def test_engagement_is_normalized_within_each_source(self):
        results = attach_engagement_scores([
            {"source": "Dev.to", "title": "A", "like": 10},
            {"source": "Dev.to", "title": "B", "like": 100},
        ])
        self.assertEqual([0, 100], [item["engagement_score"] for item in results])


if __name__ == "__main__":
    unittest.main()
