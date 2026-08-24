import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.obsidian_template import build_obsidian_note

class ObsidianTemplateTests(unittest.TestCase):
    def test_build_video_note(self):
        item = {"title": "Bilibili Tech Talk", "url": "https://www.bilibili.com/video/BV123", "source": "Bilibili", "summary": "Video summary"}
        note = build_obsidian_note(item, (85, "⭐ 推荐阅读", "recommend"), transcript="Test transcript content")
        self.assertIn("视频多媒体转写审阅", note)
        self.assertIn("Test transcript content", note)

if __name__ == '__main__':
    unittest.main()
