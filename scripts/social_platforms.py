"""Adapters for public Douyin, Bilibili, Weibo and WeChat content discovery."""
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "social_sources.json"
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
    except (OSError, json.JSONDecodeError) as error:
        print(f"Social config unavailable: {error}", file=sys.stderr)
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
        "wechat": "wechat",
        "wechatofficialaccount": "wechat",
        "微信公众号": "wechat",
        "公众号": "wechat",
        "weibosearch": "weibo",
        "微博关键词": "weibo",
        "微博": "weibo",
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
    if platform == "wechat":
        return host == "mp.weixin.qq.com" and (path == "/s" or path.startswith("/s/"))
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

    if key in {"wechat", "bilibili", "douyin"} and not allowed_social_url(key, url):
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

    fields = ("title", "summary", "description", "author", "content")
    text = " ".join(str((item.get(field) or row.get(field) or "")) for field in fields).casefold()
    commercial_action = ("购买", "付费", "报名", "优惠", "咨询", "推广", "广告", "purchase", "paid", "buy", "sale")
    direct_sales_signals = (
        "付费课程", "付费课", "购买课程", "课程购买", "立即购买",
        "报名课程", "报名训练营", "卖课", "私教", "加微信",
        "加v", "加 vx", "课程优惠", "训练营优惠", "付费专栏",
        "推广合作", "商业推广", "广告推广", "软文推广",
    )
    actual_sales_actions = ("购买", "付费", "报名", "价格", "加微信", "广告", "推广", "立即购买")
    actual_product_types = ("课程", "教程", "训练营", "会员", "专栏", "服务")
    if any(action in text for action in actual_sales_actions) and any(
        kind in text for kind in actual_product_types
    ):
        return "明确推广或销售内容"
    if any(signal in text for signal in direct_sales_signals) and any(action in text for action in commercial_action):
        return "明确推广或销售内容"

    commercial_action = ("购买", "付费", "报名", "优惠", "咨询", "推广", "广告")
    product_type = ("课程", "教程", "训练营", "专栏", "会员", "产品", "服务")
    if any(action in text for action in commercial_action) and any(kind in text for kind in product_type):
        return "疑似商业推广内容"
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
        if source_key(source) in {"wechat", "bilibili", "douyin"} and not allowed_social_url(source, url):
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
        if keyword_matches(item, configured_keywords(keyword)):
            items.append(item)
            seen_urls.add(url)
    return items[:limit]


def browser_search(platform, query, limit):
    config = load_config()
    template = config.get("search_urls", {}).get(platform)
    if not template:
        return []
    url = template.format(query=quote(query, safe=""))
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


def fetch_weibo_hot(limit=5, keyword=None):
    """Keep the existing lightweight hot-search API as the official-first path."""
    try:
        response = requests.get(
            "https://weibo.com/ajax/side/hotSearch",
            headers={**HEADERS, "Referer": "https://weibo.com/"},
            timeout=10,
        )
        response.raise_for_status()
        rows = []
        for entry in response.json().get("data", {}).get("realtime", []):
            title = entry.get("note") or entry.get("word") or ""
            if not title:
                continue
            rows.append({
                "title": title,
                "url": f"https://s.weibo.com/weibo?q={quote(title)}&Refer=top",
                "heat": str(entry.get("num", "")),
                "time": "Real-time",
                "fetch_method": "official_api",
            })
        return normalize_rows(rows, "Weibo Hot Search", keyword, limit)
    except Exception as error:
        print(f"Weibo hot search fetch error: {error}", file=sys.stderr)
        return []


def fetch_social(platform, source, limit=5, keyword=None):
    keywords = configured_keywords(keyword)
    if not keywords:
        return []
    rows = []
    per_query = max(2, min(limit, 5))
    max_queries = max(1, int(os.environ.get("SOCIAL_MAX_QUERIES", "5")))
    for value in keywords[:max_queries]:
        api_rows = configured_api_search(platform, source, value, per_query)
        rows.extend(api_rows or browser_search(platform, value, per_query))
        if len(rows) >= limit * 2:
            break
    return normalize_rows(rows, source, keyword, limit)


def fetch_douyin(limit=5, keyword=None):
    return fetch_social("douyin", "Douyin", limit, keyword)


def fetch_bilibili(limit=5, keyword=None):
    return fetch_social("bilibili", "Bilibili", limit, keyword)


def fetch_weibo_search(limit=5, keyword=None):
    return fetch_social("weibo", "Weibo Search", limit, keyword)


def fetch_wechat(limit=5, keyword=None):
    return fetch_social("wechat", "WeChat Official Account", limit, keyword)
