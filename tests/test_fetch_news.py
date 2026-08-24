import gzip
import subprocess
import sys
import types
import unittest
import zlib
from io import BytesIO
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

# fetch_news.py is also executed directly from scripts/, where its sibling imports
# are available as top-level modules.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from scripts import fetch_news


class DecodingRaw:
    def __init__(self, payload, encoding):
        self.payload = payload
        self.encoding = encoding
        self.decode_content = False

    def read(self, size=-1):
        payload = self.payload
        if self.decode_content:
            if self.encoding == "gzip":
                payload = gzip.decompress(payload)
            elif self.encoding == "deflate":
                payload = zlib.decompress(payload)
        return payload[:size]


class PlaywrightRssFallbackTests(unittest.TestCase):
    def test_evidence_fetch_can_use_an_explicit_excerpt_and_extracts_dom_text(self):
        html = b"<html><body><article><h1>Title</h1><p>Useful evidence paragraph.</p></article></body></html>"
        with patch.object(fetch_news, "_fetch_public_url", return_value=html) as request:
            evidence = fetch_news.fetch_url_evidence("https://example.test/a", max_chars=30)
        self.assertLessEqual(len(evidence), 30)
        self.assertIn("Title", evidence)
        self.assertEqual(5 * 1024 * 1024, request.call_args.kwargs["max_bytes"])

    def test_streamed_response_decodes_compressed_content(self):
        html = b"<html><body>decoded text</body></html>"
        payloads = {
            "gzip": gzip.compress(html),
            "deflate": zlib.compress(html),
        }
        for encoding, payload in payloads.items():
            with self.subTest(encoding=encoding):
                raw = DecodingRaw(payload, encoding)
                response = types.SimpleNamespace(
                    status_code=200,
                    headers={
                        "Content-Type": "text/html; charset=utf-8",
                        "Content-Encoding": encoding,
                    },
                    raw=raw,
                    raise_for_status=lambda: None,
                    close=lambda: None,
                )
                with patch.object(fetch_news, "is_safe_http_url", return_value=True), patch.object(
                    fetch_news.requests, "get", return_value=response
                ):
                    body = fetch_news._fetch_public_url("https://example.test/a")
                self.assertEqual(html, body)
                self.assertTrue(raw.decode_content)

    def test_public_fetch_rejects_binary_content_type(self):
        raw = BytesIO(b"\x00\x01\x02")
        raw.decode_content = False
        response = types.SimpleNamespace(
            status_code=200,
            headers={"Content-Type": "application/octet-stream"},
            raw=raw,
            raise_for_status=lambda: None,
            close=lambda: None,
        )
        with patch.object(fetch_news, "is_safe_http_url", return_value=True), patch.object(
            fetch_news.requests, "get", return_value=response
        ):
            body = fetch_news._fetch_public_url("https://example.test/archive")
        self.assertIsNone(body)

    def test_unreadable_http_snapshot_uses_browser_fallback(self):
        binary_html = b"<html><body>\x00\x01\x02\xef\xbf\xbd\xef\xbf\xbd</body></html>"
        browser_text = "Readable browser evidence with enough detail for downstream processing."
        with patch.object(fetch_news, "_fetch_public_url", return_value=binary_html), patch.object(
            fetch_news, "fetch_url_content_browser", return_value=browser_text
        ):
            evidence, method = fetch_news._fetch_url_evidence_with_method("https://example.test/a")
        self.assertEqual(browser_text, evidence)
        self.assertEqual("full_browser_text", method)

    def test_unreadable_snapshot_is_not_sent_downstream(self):
        self.assertFalse(fetch_news._is_readable_text("\x00\x01\ufffd\ufffd" * 20))

    def test_fetch_url_content_extracts_readable_http_body(self):
        html = b"<article><p>" + b"Detailed evidence. " * 20 + b"</p></article>"
        with patch.object(fetch_news, "_fetch_public_url", return_value=html):
            text = fetch_news.fetch_url_content("https://example.test/a")
        self.assertIn("Detailed evidence.", text)

    def test_sufficient_snapshot_skips_deep_retry(self):
        items = [{"url": "https://example.test/a"}]
        evidence = "Detailed evidence. " * 20
        with patch.object(
            fetch_news, "_fetch_url_evidence_with_method", return_value=(evidence, "bounded_dom_text")
        ), patch.object(fetch_news, "_fetch_deep_evidence") as deep_fetch:
            fetch_news.enrich_items_with_evidence(items, max_workers=1)
        self.assertFalse(deep_fetch.called)
        self.assertEqual("fetched", items[0]["evidence_status"])

    def test_short_snapshot_is_replaced_by_deep_http_evidence(self):
        items = [{"url": "https://example.test/a"}]
        deep_evidence = "Full article evidence. " * 20
        with patch.object(
            fetch_news, "_fetch_url_evidence_with_method", return_value=("Short introduction.", "bounded_browser_text")
        ), patch.object(
            fetch_news, "_fetch_deep_evidence", return_value=(deep_evidence, "full_dom_text")
        ):
            fetch_news.enrich_items_with_evidence(items, max_workers=1)
        self.assertEqual(deep_evidence, items[0]["evidence_snapshot"])
        self.assertEqual("full_dom_text", items[0]["evidence_method"])
        self.assertEqual("fetched", items[0]["evidence_status"])

    def test_short_snapshot_uses_longer_browser_retry(self):
        browser_evidence = "Rendered article evidence. " * 20
        with patch.object(fetch_news, "fetch_url_content", return_value="Short introduction."), patch.object(
            fetch_news, "fetch_url_content_browser", return_value=browser_evidence
        ) as browser_fetch:
            evidence, method = fetch_news._fetch_deep_evidence("https://example.test/a")
        self.assertEqual(browser_evidence.strip(), evidence)
        self.assertEqual("full_browser_text", method)
        self.assertEqual(3000, browser_fetch.call_args.kwargs["wait_ms"])

    def test_short_snapshot_is_marked_insufficient_when_deep_retry_fails(self):
        items = [{"url": "https://example.test/a"}]
        with patch.object(
            fetch_news, "_fetch_url_evidence_with_method", return_value=("Short introduction.", "bounded_browser_text")
        ), patch.object(fetch_news, "_fetch_deep_evidence", return_value=("", "")):
            fetch_news.enrich_items_with_evidence(items, max_workers=1)
        self.assertEqual("insufficient", items[0]["evidence_status"])
        self.assertEqual("Short introduction.", items[0]["evidence_snapshot"])

    def test_source_time_filter_can_defer_to_ai(self):
        old_mode = fetch_news.AI_TIME_INTERPRETATION_MODE
        try:
            fetch_news.AI_TIME_INTERPRETATION_MODE = True
            items = [{"title": "unknown time"}]
            self.assertEqual(items, fetch_news.source_time_filter(items, 24))
        finally:
            fetch_news.AI_TIME_INTERPRETATION_MODE = old_mode

    def test_filter_by_hours_keeps_recent_and_drops_old_items(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        old = datetime.now(timezone.utc) - timedelta(hours=25)
        result = fetch_news.filter_by_hours([
            {"title": "recent", "time": recent.isoformat()},
            {"title": "old", "time": old.isoformat()},
        ], hours=24)
        self.assertEqual(["recent"], [item["title"] for item in result])

    def test_filter_by_hours_drops_missing_time(self):
        self.assertEqual([], fetch_news.filter_by_hours([{"title": "unknown"}]))

    def test_filter_by_hours_parses_unix_timestamp_and_relative_time(self):
        recent_timestamp = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
        result = fetch_news.filter_by_hours([
            {"title": "unix", "created_at_i": recent_timestamp},
            {"title": "relative", "time": "30 minutes ago"},
        ])
        self.assertEqual({"unix", "relative"}, {item["title"] for item in result})

    def test_trending_item_survives_old_push_time(self):
        old = datetime.now(timezone.utc) - timedelta(days=3)
        result = fetch_news.filter_by_hours([{
            "title": "hot repo",
            "time": old.isoformat(),
            "is_trending": True,
        }])
        self.assertEqual(["hot repo"], [item["title"] for item in result])

    def test_github_metadata_uses_pushed_at(self):
        response = types.SimpleNamespace(
            json=lambda: {
                "pushed_at": "2026-08-03T10:00:00Z",
                "updated_at": "2026-08-03T11:00:00Z",
                "created_at": "2025-01-01T00:00:00Z",
                "stargazers_count": 42,
                "language": "Python",
                "description": "A repository",
            },
            raise_for_status=lambda: None,
        )
        item = {"title": "owner/repo", "url": "https://github.com/owner/repo", "is_trending": True}
        with patch.object(fetch_news.requests, "get", return_value=response):
            result = fetch_news.fetch_github_repo_metadata(item)
        self.assertEqual("2026-08-03T10:00:00Z", result["time"])
        self.assertEqual("repository_last_push", result["time_kind"])
        self.assertEqual(42, result["stars"])

    def test_generic_feed_runs_fetcher_and_parses_stdout(self):
        parsed_items = [{"title": "Parsed item"}]
        parser = types.ModuleType("rss_parser")
        parser.parse_rss_content = lambda content, source, limit: parsed_items
        completed = subprocess.CompletedProcess([], 0, "<rss />", "")

        with patch.object(fetch_news.subprocess, "run", return_value=completed) as run, patch.dict(
            sys.modules, {"rss_parser": parser}
        ):
            result = fetch_news.fetch_rss_with_playwright("https://example.test/feed", "Protected Feed", 3)

        self.assertEqual(parsed_items, result)
        self.assertEqual(
            [
                sys.executable,
                str(fetch_news.os.path.join(fetch_news.os.path.dirname(fetch_news.__file__), "fetch_generic_playwright.py")),
                "https://example.test/feed",
            ],
            run.call_args.args[0],
        )

    def test_generic_feed_returns_empty_list_when_fetcher_fails(self):
        completed = subprocess.CompletedProcess([], 1, "", "browser failed")

        with patch.object(fetch_news.subprocess, "run", return_value=completed):
            result = fetch_news.fetch_rss_with_playwright("https://example.test/feed", "Protected Feed")

        self.assertEqual([], result)


if __name__ == "__main__":
    unittest.main()
