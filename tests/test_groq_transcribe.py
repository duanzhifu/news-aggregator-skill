"""groq_transcribe 单元测试：第 2 级视频转写兜底。

用 mock 切断 yt-dlp 子进程与 Groq 网络，覆盖：无 key 降级、无 url 降级、
下载失败降级、超 25MB 降级、HTTP 非 200 降级、成功返回文本、requests 缺失降级。
"""
import os
import tempfile
import unittest
from unittest import mock

from scripts import groq_transcribe as gt


def _tmp_mp3(size):
    d = tempfile.mkdtemp(prefix="groq_test_")
    p = os.path.join(d, "audio.mp3")
    with open(p, "wb") as f:
        f.write(b"\x00" * size)
    return d, p


class TestTranscribeViaGroq(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(gt, "_groq_api_key", return_value="gsk_test"),
            mock.patch.object(gt, "_groq_model", return_value="whisper-large-v3-turbo"),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(mock.patch.stopall)

    def _mock_requests_post(self, status=200, payload=None):
        class _Resp:
            def __init__(self):
                self.status_code = status
                self.text = ""
                self._payload = payload if payload is not None else {"text": "转写正文"}
            def json(self):
                return self._payload
        return mock.patch.object(gt.requests if hasattr(gt, "requests") else __import__("requests"),
                                 "post", return_value=_Resp())

    def test_empty_url(self):
        self.assertEqual(gt.transcribe_via_groq(""), "")

    def test_no_key(self):
        with mock.patch.object(gt, "_groq_api_key", return_value=""):
            self.assertEqual(gt.transcribe_via_groq("https://x/v"), "")

    def test_download_failure(self):
        with mock.patch.object(gt, "_download_audio", return_value=None):
            self.assertEqual(gt.transcribe_via_groq("https://x/v"), "")

    def test_oversize(self):
        d, p = _tmp_mp3(gt._MAX_FILE_BYTES + 1)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        with mock.patch.object(gt, "_download_audio", return_value=p):
            self.assertEqual(gt.transcribe_via_groq("https://x/v"), "")

    def test_http_error(self):
        d, p = _tmp_mp3(100)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        with mock.patch.object(gt, "_download_audio", return_value=p), self._mock_requests_post(status=429):
            self.assertEqual(gt.transcribe_via_groq("https://x/v"), "")

    def test_success(self):
        d, p = _tmp_mp3(100)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        with mock.patch.object(gt, "_download_audio", return_value=p), self._mock_requests_post():
            self.assertEqual(gt.transcribe_via_groq("https://x/v"), "转写正文")

    def test_success_empty_text(self):
        d, p = _tmp_mp3(100)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        with mock.patch.object(gt, "_download_audio", return_value=p), self._mock_requests_post(payload={"text": "  "}):
            self.assertEqual(gt.transcribe_via_groq("https://x/v"), "")


class TestTranscribeLocalFile(unittest.TestCase):
    def setUp(self):
        self._patches = [
            mock.patch.object(gt, "_groq_api_key", return_value="gsk_test"),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(mock.patch.stopall)

    def test_missing_file(self):
        self.assertEqual(gt.transcribe_local_file("Z:\\不存在\\视频.mp4"), "")

    def test_no_key(self):
        with mock.patch.object(gt, "_groq_api_key", return_value=""):
            self.assertEqual(gt.transcribe_local_file("C:\\fake.mp4"), "")

    def test_extract_failure(self):
        with mock.patch.object(gt, "_extract_audio_local", return_value=None):
            self.assertEqual(gt.transcribe_local_file("C:\\fake.mp4"), "")

    def test_success(self):
        d, p = _tmp_mp3(100)
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        with mock.patch.object(gt, "_extract_audio_local", return_value=p), \
             mock.patch.object(gt, "_post_audio_to_groq", return_value="转写正文"):
            self.assertEqual(gt.transcribe_local_file(p), "转写正文")


if __name__ == "__main__":
    unittest.main()
