import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scripts.video_transcribe import transcribe_audio_to_text

class VideoTranscribeTests(unittest.TestCase):
    def test_transcribe_empty(self):
        res = transcribe_audio_to_text("")
        self.assertIn("未找到", res)

if __name__ == '__main__':
    unittest.main()
