"""Fetch public social search pages with a bounded Playwright session."""
import argparse
import json
import os
import re
import sys
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright


TIME_PATTERN = re.compile(
    r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|\d+\s*(?:minutes?|mins?|hours?|hrs?|days?|分钟|分钟前|小时|天)\s*(?:ago|前)?"
    r"|(?:今天|昨天)\s*\d{1,2}:\d{2}|刚刚|today|recent)",
    re.IGNORECASE,
)
METRIC_PATTERNS = {
    "view": re.compile(r"([\d,.]+\s*[万kKmM]?)\s*(?:播放|浏览|views?|观看)", re.IGNORECASE),
    "like": re.compile(r"([\d,.]+\s*[万kKmM]?)\s*(?:点赞|喜欢|likes?)", re.IGNORECASE),
    "comment": re.compile(r"([\d,.]+\s*[万kKmM]?)\s*(?:评论|comments?)", re.IGNORECASE),
    "share": re.compile(r"([\d,.]+\s*[万kKmM]?)\s*(?:转发|分享|shares?)", re.IGNORECASE),
    "collect": re.compile(r"([\d,.]+\s*[万kKmM]?)\s*(?:收藏|保存|favorites?|saves?)", re.IGNORECASE),
}
STATS_ONLY_TITLE = re.compile(
    r"^\s*[\d,.]+\s*[万kKmM]?\s+\d[\d,.]*\s+\d{1,2}:\d{2}(?::\d{2})?\s*$"
)


def configured_profile(environ=None):
    environ = os.environ if environ is None else environ
    value = environ.get("NEWS_AGGREGATOR_BROWSER_PROFILE", "").strip()
    if not value:
        return None
    profile = os.path.abspath(os.path.expanduser(value))
    if not os.path.isdir(profile):
        print(
            f"Browser profile does not exist; using a temporary session: {profile}",
            file=sys.stderr,
        )
        return None
    return profile


def configured_channel(environ=None):
    environ = os.environ if environ is None else environ
    value = environ.get("NEWS_AGGREGATOR_BROWSER_CHANNEL", "").strip().casefold()
    if not value:
        return None
    if value == "msedge":
        return value
    print(
        f"Unsupported browser channel '{value}'; using Playwright Chromium.",
        file=sys.stderr,
    )
    return None


def launch_options(channel, headless=True):
    options = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if channel:
        options["channel"] = channel
    return options


def allowed_url(platform, url):
    from urllib.parse import urlsplit

    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    if platform == "wechat":
        return host == "mp.weixin.qq.com" and (path == "/s" or path.startswith("/s/"))
    if platform == "bilibili":
        return host in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"} and path.startswith("/video/")
    if platform == "douyin":
        return host == "www.douyin.com" and path.startswith("/video/")
    return True


def _safe_inner_text(locator):
    try:
        return re.sub(r"\s+", " ", locator.inner_text()).strip()
    except Exception:
        return ""


def _context_for_anchor(anchor):
    """Read the nearest result container, which carries metadata omitted by the link."""
    try:
        container = anchor.locator("xpath=ancestor::*[self::article or self::li][1]")
        if container.count() == 0:
            container = anchor.locator("xpath=..").first
        return container
    except Exception:
        return anchor


def _extract_context(anchor, title):
    container = _context_for_anchor(anchor)
    context_text = _safe_inner_text(container)
    metadata = {}
    time_match = TIME_PATTERN.search(context_text)
    if time_match:
        metadata["time"] = time_match.group(0)

    for key, pattern in METRIC_PATTERNS.items():
        match = pattern.search(context_text)
        if match:
            metadata[key] = match.group(1).replace(" ", "")

    try:
        for candidate in container.locator("a").all()[:8]:
            href = candidate.get_attribute("href") or ""
            candidate_text = _safe_inner_text(candidate)
            if (
                candidate_text
                and candidate_text != title
                and len(candidate_text) <= 80
                and re.search(r"/(?:space|user|author|profile)(?:/|$)", href, re.IGNORECASE)
            ):
                metadata["author"] = candidate_text
                break
    except Exception:
        pass

    summary = context_text.replace(title, "", 1).strip(" -|·\n")
    if summary and summary != metadata.get("time", ""):
        metadata["summary"] = summary[:500]
    return metadata


def extract_items(page, base_url, platform, limit):
    rows = []
    seen = set()
    for anchor in page.locator("a").all()[: max(limit * 8, 40)]:
        try:
            text = re.sub(r"\s+", " ", anchor.inner_text()).strip()
            href = anchor.get_attribute("href") or ""
            if not text or len(text) < 4 or not href:
                continue
            url = urljoin(base_url, href)
            if not url.startswith("http") or url in seen:
                continue
            if not allowed_url(platform, url):
                continue
            if len(text) > 240:
                text = text[:237] + "..."
            seen.add(url)
            row = {"title": text, "url": url, "fetch_method": "browser_context"}
            row.update(_extract_context(anchor, text))
            rows.append(row)
            if len(rows) >= limit:
                break
        except Exception:
            continue
    return rows


def _is_valid_bilibili_title(title):
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    return bool(value) and len(value) >= 4 and not STATS_ONLY_TITLE.fullmatch(value)


def enrich_bilibili_titles(page, rows):
    """Replace search-card metric text with titles from each video detail page."""
    for row in rows:
        url = row.get("url", "")
        if not allowed_url("bilibili", url):
            continue
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
            title_node = page.locator("h1.video-title").first
            title_node.wait_for(state="visible", timeout=5000)
            title = (
                title_node.get_attribute("data-title")
                or title_node.get_attribute("title")
                or _safe_inner_text(title_node)
            )
            if _is_valid_bilibili_title(title):
                row["title"] = re.sub(r"\s+", " ", title).strip()
                row["title_source"] = "bilibili_detail"
        except Exception:
            continue
        if not _is_valid_bilibili_title(row.get("title", "")):
            row["title"] = ""
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--platform", default="", choices=("", "bilibili", "douyin", "wechat", "weibo"))
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    profile = configured_profile()
    channel = configured_channel()

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            if profile:
                print(f"Using persistent browser profile: {profile}", file=sys.stderr)
                context = playwright.chromium.launch_persistent_context(
                    profile, **launch_options(channel)
                )
            else:
                browser = playwright.chromium.launch(**launch_options(channel))
                context = browser.new_context()
            page = context.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
            rows = extract_items(page, args.url, args.platform, args.limit)
            if args.platform == "bilibili":
                rows = enrich_bilibili_titles(page, rows)
            print(json.dumps(rows, ensure_ascii=False))
        except Exception as error:
            print(json.dumps({"error": str(error)[:300]}, ensure_ascii=False))
            return 1
        finally:
            if context:
                context.close()
            if browser:
                browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
