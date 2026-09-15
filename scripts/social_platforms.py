"""Adapters for public Douyin and Bilibili content discovery."""
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "social_sources.json"
# 搜索页 URL 模板内置默认（原 config/social_sources.json 的 search_urls 迁移至此；
# 优先读配置文件，缺省时用此默认。仅 bilibili 为当前活跃链路，douyin 停用中不内置。）
_DEFAULT_SEARCH_URLS = {
    "bilibili": "https://search.bilibili.com/all?keyword={query}",
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
}
LAST_FILTER_STATS = {}


def _record_filter(source, reason):
    key = source_key(source)
    stats = LAST_FILTER_STATS.setdefault(key, {})
    stats[reason] = stats.get(reason, 0) + 1


def consume_filter_stats():
    snapshot = {source: dict(values) for source, values in LAST_FILTER_STATS.items()}
    LAST_FILTER_STATS.clear()
    return snapshot


def load_config():
    path = Path(os.environ.get("NEWS_AGGREGATOR_SOCIAL_CONFIG", DEFAULT_CONFIG))
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        # 配置文件缺失或无配置时静默降级为空 dict（config/social_sources.json 已迁入代码，
        # search_urls 由 social_platforms._DEFAULT_SEARCH_URLS 兜底）
        return {"keywords": {}, "accounts": {}, "search_urls": {}}


def configured_keywords(keyword=None):
    if keyword:
        return [part.strip() for part in keyword.split(",") if part.strip()]
    config = load_config()
    result = []
    for values in config.get("keywords", {}).values():
        for value in values:
            if value not in result:
                result.append(value)
    return result


def keyword_matches(item, keywords):
    if not keywords:
        return True
    haystack = " ".join(str(item.get(key, "")) for key in ("title", "summary", "author"))
    return any(value.casefold() in haystack.casefold() for value in keywords)


def source_key(source):
    value = str(source or "").casefold().replace(" ", "")
    return {
        "bilibili": "bilibili",
        "douyin": "douyin",
        "抖音": "douyin",
    }.get(value, value)


def allowed_social_url(platform, url):
    """Keep only the content URL type promised by each social source."""
    try:
        parsed = urlsplit(str(url).strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    platform = source_key(platform)
    if platform == "bilibili":
        return host in {"bilibili.com", "www.bilibili.com", "m.bilibili.com"} and path.startswith("/video/")
    if platform == "douyin":
        return host == "www.douyin.com" and path.startswith("/video/")
    return True


def disallowed_reason(source, row=None, item=None):
    """Return a stable rejection reason for paid, promotional or off-source items."""
    row = row or {}
    item = item or {}
    key = source_key(source)
    url = str(item.get("url") or row.get("url") or row.get("link") or "").strip()
    url_lower = url.casefold()

    if key in {"bilibili", "douyin"} and not allowed_social_url(key, url):
        return "不符合来源链接白名单"
    if "baike.baidu.com" in url_lower or "百度百科" in url_lower:
        return "百度百科或百科导流内容"
    if key == "bilibili" and any(marker in url_lower for marker in ("/cheese/", "paywall", "/purchase", "/vip/")):
        return "B站付费或会员页面"

    paid_flags = ("is_paid", "paid", "chargeable", "paywall")
    if any(row.get(flag) is True for flag in paid_flags):
        return "平台标记为付费内容"
    price = row.get("price")
    if price not in (None, "", 0, "0", "0.0", "0.00", 0.0):
        return "内容带有购买价格"
    return ""


def is_disallowed_item(source, row=None, item=None):
    return bool(disallowed_reason(source, row, item))


def normalize_rows(rows, source, keyword, limit):
    items = []
    seen_urls = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("name") or "").strip()
        url = str(row.get("url") or row.get("link") or "").strip()
        if not title or not url or not url.startswith("http"):
            continue
        if source_key(source) in {"bilibili", "douyin"} and not allowed_social_url(source, url):
            _record_filter(source, "url_not_allowed")
            continue
        if url in seen_urls:
            _record_filter(source, "duplicate_url")
            continue
        item = {
            "source": source,
            "title": title,
            "url": url,
            "time": (
                row.get("time")
                or row.get("published")
                or row.get("published_at")
                or row.get("created_at")
                or row.get("timestamp")
                or row.get("date")
                or row.get("updated_at")
                or ""
            ),
            "summary": row.get("summary") or row.get("description") or "",
            "heat": row.get("heat") or "",
            "author": row.get("author") or row.get("account") or "",
            "platform_id": row.get("platform_id") or row.get("id") or "",
            "keyword": keyword or "",
            "fetch_method": row.get("fetch_method") or "public_page",
        }
        for key in (
            "view", "views", "like", "likes", "heart", "hearts", "claps",
            "comment", "comments", "reply", "replies", "collect", "favorite",
            "favorites", "bookmark", "bookmarks", "share", "shares", "repost", "reposts",
        ):
            if row.get(key) is not None:
                item[key] = row[key]
        if is_disallowed_item(source, row, item):
            _record_filter(source, disallowed_reason(source, row, item))
            continue
        if source_key(source) == "bilibili" or keyword_matches(item, configured_keywords(keyword)):
            items.append(item)
            seen_urls.add(url)
    return items[:limit]


def browser_page(platform, url, limit):
    script = Path(__file__).with_name("fetch_social_browser.py")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            [sys.executable, str(script), url, "--platform", platform, "--limit", str(limit)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
        )
        if result.returncode != 0:
            detail = result.stderr or result.stdout or "unknown browser error"
            print(f"{platform} browser fetch failed: {detail}", file=sys.stderr)
            return []
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        payload = json.loads(result.stdout or "[]")
        if isinstance(payload, dict) and payload.get("error"):
            print(f"{platform} browser fetch error: {payload['error']}", file=sys.stderr)
            return []
        rows = payload if isinstance(payload, list) else []
        if not rows:
            _record_filter(platform, "browser_no_allowed_results")
        return rows
    except Exception as error:
        print(f"{platform} browser fetch exception: {error}", file=sys.stderr)
        return []


def browser_search(platform, query, limit):
    config = load_config()
    template = (config.get("search_urls", {}) or {}).get(platform)
    if not template:
        template = _DEFAULT_SEARCH_URLS.get(platform)
    if not template:
        return []
    return browser_page(platform, template.format(query=quote(query, safe="")), limit)


def configured_api_search(platform, source, query, limit):
    """Use an explicitly configured platform API before browser discovery."""
    endpoint = os.environ.get(f"SOCIAL_API_URL_{platform.upper()}", "").strip()
    if not endpoint:
        return []
    url = endpoint.format(query=quote(query, safe=""))
    token = os.environ.get(f"SOCIAL_API_TOKEN_{platform.upper()}", "").strip()
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
        rows = payload if isinstance(payload, list) else payload.get("data", payload.get("items", []))
        if isinstance(rows, dict):
            rows = rows.get("list", rows.get("results", []))
        if not isinstance(rows, list):
            return []
        for row in rows:
            if isinstance(row, dict):
                row["fetch_method"] = "official_api"
        return normalize_rows(rows, source, query, limit)
    except Exception as error:
        print(f"{platform} configured API failed, using browser fallback: {error}", file=sys.stderr)
        return []


_BILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/all/v2"
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text):
    """B站标题关键词高亮标签剥离（<em class="keyword">X</em> → X）。"""
    return html.unescape(_HTML_TAG_RE.sub("", text)).strip()


def _bilibili_api_search(query, limit):
    """B站官方搜索 API 直连（无签名/无登录）。失败或异常返回 []，由上层回退浏览器。

    /x/web-interface/search/all/v2 无签名可调（2026-09 实测 code 0，42 条 video）；
    标题带 <em class="keyword"> 高亮需剥离；pubdate 为 Unix 秒需转 ISO。
    """
    if not query:
        return []
    url = f"{_BILI_SEARCH_API}?keyword={quote(str(query), safe='')}&page=1"
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as error:
        print(f"bilibili API search failed: {error}", file=sys.stderr)
        return []
    if payload.get("code") != 0:
        print(f"bilibili API search rejected: code={payload.get('code')}", file=sys.stderr)
        return []
    rows = []
    for group in payload.get("data", {}).get("result") or []:
        if group.get("result_type") != "video":
            continue
        for item in (group.get("data") or [])[:limit]:
            bvid = str(item.get("bvid") or "").strip()
            title = _strip_html(str(item.get("title") or ""))
            if not bvid or not title:
                continue
            pubdate = item.get("pubdate")
            time_text = ""
            if isinstance(pubdate, (int, float)) and pubdate > 0:
                time_text = datetime.fromtimestamp(pubdate).astimezone().isoformat(timespec="seconds")
            play = item.get("play")
            rows.append({
                "title": title,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "time": time_text,
                "summary": str(item.get("description") or ""),
                "heat": str(play) if isinstance(play, (int, float)) else "",
                "author": str(item.get("author") or ""),
                "platform_id": bvid,
                "fetch_method": "bilibili_api",
                "view": play,
                "like": item.get("like"),
                "comment": item.get("review"),
                "favorites": item.get("favorites"),
            })
        break
    return rows


def fetch_social(platform, source, limit=5, keyword=None):
    if not keyword and source_key(platform) == "bilibili":
        env_topics = os.environ.get("NEWS_AGGREGATOR_TOPICS", "").strip()
        if env_topics:
            keyword = env_topics
    keywords = configured_keywords(keyword)
    if not keywords:
        return []
    rows = []
    per_query = max(2, min(limit, 5))
    max_queries = max(1, int(os.environ.get("SOCIAL_MAX_QUERIES", "15")))
    for value in keywords[:max_queries]:
        api_rows = configured_api_search(platform, source, value, per_query)
        if api_rows:
            rows.extend(api_rows)
            continue
        if source_key(platform) == "bilibili":
            # API 直连优先（快一个量级、元数据完整）；失败回退浏览器抓页
            rows.extend(_bilibili_api_search(value, per_query) or browser_search(platform, value, per_query))
        else:
            rows.extend(browser_search(platform, value, per_query))
    return normalize_rows(rows, source, keyword, limit)


def fetch_douyin(limit=5, keyword=None):
    return fetch_social("douyin", "Douyin", limit, keyword)


def fetch_bilibili(limit=5, keyword=None):
    return fetch_social("bilibili", "Bilibili", limit, keyword)
