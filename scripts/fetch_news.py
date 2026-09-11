import argparse
import json
import requests
from bs4 import BeautifulSoup
import sys
import io
import time
import re
import concurrent.futures
import os
import threading
import ipaddress
import socket
from functools import lru_cache
from urllib.parse import urljoin, urlsplit
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import subprocess

# Windows console defaults to cp936/GBK; force UTF-8 so Chinese JSON output isn't mangled.
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Headers for scraping to avoid basic bot detection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

GITHUB_METADATA_CACHE_PATH = os.path.join(os.path.dirname(__file__), '.cache', 'github_repo_metadata.json')
GITHUB_METADATA_CACHE_TTL = 6 * 60 * 60
_github_cache = {}
_github_cache_loaded = False
_github_cache_lock = threading.Lock()

from bs4 import XMLParsedAsHTMLWarning
import warnings
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

def parse_item_datetime(item):
    """Parse common feed, API and Chinese relative timestamps consistently."""
    fields = (
        'time', 'published', 'published_at', 'pub_time', 'publish_time',
        'created_at', 'created_at_i', 'timestamp', 'date', 'updated_at', 'time_ms',
    )
    value = next((item.get(field) for field in fields if item.get(field) not in (None, '')), None)
    if value is None:
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        number = float(value)
        if number > 10**12:
            number /= 1000
        if number > 10**9:
            return datetime.fromtimestamp(number, tz=timezone.utc)

    text = str(value).strip()
    lowered = text.casefold()
    if lowered in {'hot', 'recent', 'realtime', 'real-time', 'updated recently'}:
        return None
    relative = re.search(
        r'(\d+(?:\.\d+)?)\s*(minutes?|mins?|hours?|hrs?|days?|分钟前|分钟|小时|天)\s*(ago|前)?',
        lowered,
    )
    if relative:
        amount = float(relative.group(1))
        unit = relative.group(2)
        if unit.startswith(('minute', 'min')) or '分钟' in unit:
            return datetime.now(timezone.utc) - timedelta(minutes=amount)
        if unit.startswith(('hour', 'hr')) or '小时' in unit:
            return datetime.now(timezone.utc) - timedelta(hours=amount)
        return datetime.now(timezone.utc) - timedelta(days=amount)
    for marker, offset in (('今天', 0), ('昨天', 1)):
        match = re.fullmatch(rf'{marker}\s*(\d{{1,2}}):(\d{{2}})', text)
        if match:
            base = datetime.now(timezone.utc) - timedelta(days=offset)
            return base.replace(
                hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0
            )
    if text.casefold() in {'刚刚', 'just now'}:
        return datetime.now(timezone.utc)
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def filter_by_hours(items, hours=72, stats=None):
    """Keep only items with a parseable publication time in the recent window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = []
    for item in items:
        published = parse_item_datetime(item)
        source = item.get('source', 'unknown')
        if stats is not None:
            stats.setdefault(source, {'received': 0, 'time_missing': 0, 'too_old': 0, 'kept': 0})
            stats[source]['received'] += 1
        if published is None:
            if stats is not None:
                stats[source]['time_missing'] += 1
            continue
        if published >= cutoff or item.get('is_trending') is True:
            result.append(item)
            if stats is not None:
                stats[source]['kept'] += 1
        elif stats is not None:
            stats[source]['too_old'] += 1
    return result


AI_TIME_INTERPRETATION_MODE = False


def source_time_filter(items, hours):
    """Preserve raw candidates when downstream AI owns time interpretation."""
    if AI_TIME_INTERPRETATION_MODE:
        return items
    return filter_by_hours(items, hours=hours)


def filter_items(items, keyword=None):
    if not keyword:
        return items
    keywords = [k.strip() for k in keyword.split(',') if k.strip()]
    pattern = '|'.join([r'\b' + re.escape(k) + r'\b' for k in keywords])
    regex = r'(?i)(' + pattern + r')'
    return [item for item in items if re.search(regex, item['title'])]


@lru_cache(maxsize=256)
def _resolves_to_public_address(hostname):
    """Reject hosts that resolve to private or otherwise local addresses."""
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (OSError, socket.gaierror):
        return False
    if not addresses:
        return False
    for address in {entry[4][0] for entry in addresses}:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def is_safe_http_url(url):
    """Allow only public HTTP(S) URLs without embedded credentials."""
    try:
        parsed = urlsplit(str(url).strip())
        hostname = parsed.hostname
        if parsed.scheme not in {"http", "https"} or not hostname:
            return False
        if parsed.username or parsed.password:
            return False
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            return _resolves_to_public_address(hostname)
        return not (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        )
    except ValueError:
        return False


def _fetch_public_url(url, max_redirects=3, max_bytes=5 * 1024 * 1024):
    current_url = url
    for _ in range(max_redirects + 1):
        if not is_safe_http_url(current_url):
            return None
        response = None
        try:
            response = requests.get(
                current_url,
                headers=HEADERS,
                timeout=5,
                allow_redirects=False,
                stream=True,
            )
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    return None
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type and not (
                content_type.startswith("text/")
                or content_type in {"application/xhtml+xml", "application/xml"}
            ):
                return None
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                return None
            # urllib3 does not automatically decode streamed raw reads unless
            # decode_content is enabled explicitly.
            response.raw.decode_content = True
            body = response.raw.read(max_bytes + 1)
            return None if len(body) > max_bytes else body
        except (OSError, ValueError, requests.RequestException):
            return None
        finally:
            if response is not None:
                response.close()
    return None

MIN_ARTICLE_CONTENT_CHARS = 200
# Retained for callers that explicitly request a short excerpt. The default
# evidence pipeline below now keeps the complete extracted article text.
MAX_EVIDENCE_CHARS = 4000
MIN_EVIDENCE_CHARS = 300


def _is_readable_text(text, min_chars=20):
    """Reject binary payloads or irreversibly decoded text before AI processing."""
    if not isinstance(text, str) or len(text.strip()) < min_chars:
        return False
    sample = text[:4000]
    replacement_ratio = sample.count('\ufffd') / len(sample)
    control_count = sum(
        1 for char in sample
        if ord(char) < 32 and char not in {'\n', '\r', '\t'}
    )
    return replacement_ratio <= 0.01 and control_count / len(sample) <= 0.01


def _truncate_evidence(text, max_chars):
    if len(text) <= max_chars:
        return text.strip()
    cut = text.rfind('\n', 0, max_chars)
    return text[:cut if cut > 0 else max_chars].strip()


def _collapse_lines(text):
    """Collapse consecutive blank lines and strip each line."""
    cleaned = []
    previous_empty = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if not previous_empty:
                cleaned.append('')
            previous_empty = True
            continue
        previous_empty = False
        cleaned.append(line)
    return '\n'.join(cleaned).strip()


# 借鉴 Firecrawl 的 EXCLUDE_NON_MAIN_TAGS（apps/api/native/src/html.rs）：
# 按 class/id 剔除广告、cookie、社交分享、侧栏、弹窗、面包屑、导航等非正文块。
# 故意不收 .top / .bottom（太通用，易误杀正文）与 .***（语义不明）。
_BOILERPLATE_SELECTORS = (
    ".ad", ".ads", ".advert", "#ad",
    ".cookie", "#cookie",
    ".social", ".social-media", ".social-links", "#social",
    ".share", "#share",
    ".widget", "#widget",
    ".sidebar", ".side", ".aside", "#sidebar",
    ".modal", ".popup", "#modal", ".overlay",
    ".breadcrumbs", "#breadcrumbs",
    ".navigation", ".menu", ".navbar", "#nav",
    ".lang-selector", "#language-selector", ".language",
)


def _extract_article_text(html_content):
    """Extract main article text: trafilatura first, BeautifulSoup fallback, never whole-body junk."""
    # 1) 主提取：trafilatura 只留正文（去导航/广告/页脚，同 Firecrawl 的 main-content 思路）。
    #    输出 Markdown：保留标题/列表/代码块结构，便于笔记正文「## 原文正文」在 Obsidian 里正确渲染。
    #    （A/B 实测 Markdown 较现状 -13%，仅比纯文本 txt 多约 3% 结构字符，换取结构收益。）
    try:
        import trafilatura
        text = trafilatura.extract(
            html_content,
            output_format='markdown',
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if text and _is_readable_text(text):
            return _collapse_lines(text)
    except Exception:
        pass  # 依赖缺失/解析异常 → 走下方 BeautifulSoup 兜底

    # 2) 兜底：BeautifulSoup 选正文容器；一个都不命中就返回空串，
    #    交给上层 _fetch_url_evidence_with_method 触发 Playwright 重试 / 标记 insufficient，
    #    不再退回整页全文（避免导航/广告/相关推荐混进证据喂给 LLM 白烧 token）。
    soup = BeautifulSoup(html_content, 'html.parser')
    for element in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        element.decompose()
    # 借鉴 Firecrawl：按 class/id 再剔广告/侧栏/弹窗/分享/cookie 等非正文块（仅兜底路径）。
    for selector in _BOILERPLATE_SELECTORS:
        for element in soup.select(selector):
            element.decompose()
    selectors = (
        "article", "main", "[role='main']", ".article-content", ".post-content",
        ".entry-content", ".markdown-body", ".prose", ".content",
    )
    candidates = []
    for selector in selectors:
        for node in soup.select(selector):
            text = node.get_text(separator='\n', strip=True)
            if text:
                candidates.append(text)
    if not candidates:
        return ""
    return _collapse_lines(max(candidates, key=len))


def fetch_url_content(url):
    """Fetch article text over HTTP; browser rendering is handled as a fallback."""
    if not url:
        return ""
    try:
        content = _fetch_public_url(url)
        text = _extract_article_text(content) if content else ""
        return text if _is_readable_text(text) else ""
    except Exception:
        return ""


def _fetch_url_evidence_with_method(url, max_bytes=5 * 1024 * 1024, max_chars=None):
    """Return readable article evidence and the method used to obtain it."""
    if not url:
        return "", "feed_metadata"
    # 视频源优先拿字幕转录（4.8）：bilibili 走 cookie 字幕 API，youtube 暂退化。
    # 拿到 transcript 时直接当证据注入下游，避免 AI 只看标题/简介盲审。
    try:
        from scripts.video_transcribe import is_video_site as _is_video_site, fetch_video_transcript as _fetch_video_transcript
    except ModuleNotFoundError:
        from video_transcribe import is_video_site as _is_video_site, fetch_video_transcript as _fetch_video_transcript
    if _is_video_site(url):
        transcript = _fetch_video_transcript(url)
        if _is_readable_text(transcript):
            return _truncate_evidence(transcript, max_chars) if max_chars is not None else transcript.strip(), "video_transcript"
        # 字幕拿不到（无字幕/获取失败/空）→ 第 2 级 Groq whisper 兜底，所有视频站统一走此链。
        try:
            from scripts.groq_transcribe import transcribe_via_groq as _transcribe_via_groq
        except ModuleNotFoundError:
            from groq_transcribe import transcribe_via_groq as _transcribe_via_groq
        transcript = _transcribe_via_groq(url)
        if _is_readable_text(transcript):
            return _truncate_evidence(transcript, max_chars) if max_chars is not None else transcript.strip(), "groq_transcript"
    try:
        content = _fetch_public_url(url, max_bytes=max_bytes)
        text = _extract_article_text(content) if content else ""
        method = "full_dom_text" if max_chars is None else "bounded_dom_text"
    except Exception:
        text = ""

    if not _is_readable_text(text):
        text = fetch_url_content_browser(url)
        method = "full_browser_text" if max_chars is None else "bounded_browser_text"
    if not _is_readable_text(text):
        return "", "feed_metadata"
    return _truncate_evidence(text, max_chars) if max_chars is not None else text.strip(), method


def _fetch_deep_evidence(url, max_chars=None):
    """Retry short evidence with full HTTP text, then a longer browser render."""
    text = fetch_url_content(url)
    method = "full_dom_text"
    if not _is_readable_text(text, min_chars=MIN_EVIDENCE_CHARS):
        text = fetch_url_content_browser(url, wait_ms=3000)
        method = "full_browser_text"
    if not _is_readable_text(text, min_chars=MIN_EVIDENCE_CHARS):
        return "", ""
    return _truncate_evidence(text, max_chars) if max_chars is not None else text.strip(), method


def enrich_items_with_evidence(items, max_workers=8):
    """Attach complete, AI-readable article text while preserving item order."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(_fetch_url_evidence_with_method, item.get('url', '')): item
            for item in items
        }
        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            try:
                evidence, method = future.result()
            except Exception:
                evidence, method = "", "feed_metadata"
            if evidence:
                item['evidence_snapshot'] = evidence
                item['evidence_status'] = 'fetched'
                item['evidence_method'] = method
                item['evidence_length'] = len(evidence)
            else:
                item['evidence_status'] = 'unavailable'
                item['evidence_method'] = 'feed_metadata'
                item['evidence_length'] = 0

    short_items = [
        item for item in items
        if item.get('evidence_status') == 'fetched'
        and item.get('evidence_length', 0) < MIN_EVIDENCE_CHARS
    ]
    if short_items:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(short_items))) as executor:
            future_to_item = {
                executor.submit(_fetch_deep_evidence, item.get('url', '')): item
                for item in short_items
            }
            for future in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[future]
                try:
                    evidence, method = future.result()
                except Exception:
                    evidence, method = "", ""
                if evidence:
                    item['evidence_snapshot'] = evidence
                    item['evidence_status'] = 'fetched'
                    item['evidence_method'] = method
                    item['evidence_length'] = len(evidence)
                else:
                    item['evidence_status'] = 'insufficient'
    return items


def fetch_url_content_browser(url, wait_ms=1200):
    """Render JS-heavy pages when the normal HTTP response has no usable body."""
    if not url or not is_safe_http_url(url):
        return ""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)
            rendered = page.content()
            browser.close()
        return _extract_article_text(rendered)
    except Exception:
        return ""


def enrich_items_with_content(items, max_workers=10):
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {executor.submit(fetch_url_content, item['url']): item for item in items}
        for future in concurrent.futures.as_completed(future_to_item):
            item = future_to_item[future]
            try:
                content = future.result()
                if content:
                    item['content'] = content
                    item['content_fetch_status'] = 'fetched'
                    item['content_fetch_method'] = 'http'
                    item['content_length'] = len(content)
                else:
                    item['content_fetch_status'] = 'unavailable'
                    item['content_fetch_method'] = 'none'
                    item['content_length'] = 0
            except Exception:
                item['content_fetch_status'] = 'unavailable'
                item['content_fetch_method'] = 'none'
                item['content_length'] = 0

    for item in items:
        content = item.get('content', '') or ''
        if len(content) >= MIN_ARTICLE_CONTENT_CHARS:
            continue
        browser_content = fetch_url_content_browser(item.get('url', ''))
        if browser_content:
            item['content'] = browser_content
            item['content_fetch_status'] = 'fetched'
            item['content_fetch_method'] = 'browser'
            item['content_length'] = len(browser_content)
        elif content:
            item['content_fetch_status'] = 'partial'
            item['content_fetch_method'] = 'http'
            item['content_length'] = len(content)
    return items

# --- Source Fetchers ---

def fetch_hackernews(limit=5, keyword=None):
    if keyword:
        # Use Algolia API for keyword search (Much better recall for specific topics like "AI")
        try:
            # 24h window
            timestamp_24h = int(time.time() - 24 * 3600)
            
            # Query builder strategy
            raw_keywords = [k.strip() for k in keyword.split(',')]
            
            # 1. Try Complex Query with Quoted Phrases
            # "Github Copilot" needs quotes in Algolia search string if mixed with OR
            quoted_keywords = [f'"{k}"' if ' ' in k else k for k in raw_keywords]
            query_str = " OR ".join(quoted_keywords)
            
            api_url = f"http://hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=created_at_i>{timestamp_24h}&hitsPerPage={limit*2}&query={requests.utils.quote(query_str)}"
            
            data = requests.get(api_url, timeout=10).json()
            hits = data.get('hits', [])
            
            # 2. Level 2 Fallback: If 0 results, try just the first keyword (usually the most broad, e.g. "AI")
            if not hits and raw_keywords:
                simple_query = raw_keywords[0]
                api_url_simple = f"http://hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=created_at_i>{timestamp_24h}&hitsPerPage={limit*2}&query={requests.utils.quote(simple_query)}"
                data = requests.get(api_url_simple, timeout=10).json()
                hits = data.get('hits', [])

            items = []
            for hit in hits:
                items.append({
                    "source": "Hacker News",
                    "title": hit.get('title'),
                    "url": hit.get('url') or f"https://news.ycombinator.com/item?id={hit['objectID']}",
                    "hn_url": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                    "heat": f"{hit.get('points', 0)} points",
                    "time": hit.get('created_at') or hit.get('created_at_i') or ""
                })
            
            # Only return if we actually found something. 
            # If we found nothing after all attempts, we might want to fall back to scraping frontpage 
            # but frontpage is unlikely to have keyword matches if deep search failed. 
            # However, returning [] is better than hallucinating.
            return items[:limit]
            
        except Exception as e:
            print(f"HN Algolia failed: {e}", file=sys.stderr)
            # Fallback to scraping logic below if API completely errors out (e.g. network/timeout)
            pass

    # Fallback / Default: Scrape Front Page
    base_url = "https://news.ycombinator.com"
    news_items = []
    page = 1
    max_pages = 5
    
    while len(news_items) < limit and page <= max_pages:
        url = f"{base_url}/news?p={page}"
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            if response.status_code != 200: break
        except: break

        soup = BeautifulSoup(response.text, 'html.parser')
        rows = soup.select('.athing')
        if not rows: break
        
        page_items = []
        for row in rows:
            try:
                id_ = row.get('id')
                title_line = row.select_one('.titleline a')
                if not title_line: continue
                title = title_line.get_text()
                link = title_line.get('href')
                
                # Metadata
                score_span = soup.select_one(f'#score_{id_}')
                score = score_span.get_text() if score_span else "0 points"
                
                # Age/Time
                age_span = soup.select_one(f'.age a[href="item?id={id_}"]')
                time_str = age_span.get_text() if age_span else ""
                
                if link and link.startswith('item?id='): link = f"{base_url}/{link}"
                
                page_items.append({
                    "source": "Hacker News", 
                    "title": title, 
                    "url": link, 
                    "hn_url": f"{base_url}/item?id={id_}",
                    "heat": score,
                    "time": time_str
                })
            except: continue
        
        news_items.extend(filter_items(page_items, keyword))
        if len(news_items) >= limit: break
        page += 1
        time.sleep(0.5)

    return news_items[:limit]

def github_repo_slug(url):
    match = re.match(r'https?://github\.com/([^/]+/[^/?#]+)', str(url or '').strip())
    return match.group(1).rstrip('/') if match else ''


def _load_github_cache():
    global _github_cache_loaded, _github_cache
    with _github_cache_lock:
        if _github_cache_loaded:
            return
        try:
            with open(GITHUB_METADATA_CACHE_PATH, 'r', encoding='utf-8') as handle:
                payload = json.load(handle)
            _github_cache = payload if isinstance(payload, dict) else {}
        except (OSError, ValueError):
            _github_cache = {}
        _github_cache_loaded = True


def _cache_github_metadata(slug, data):
    with _github_cache_lock:
        _github_cache[slug] = {'fetched_at': time.time(), 'data': data}
        try:
            os.makedirs(os.path.dirname(GITHUB_METADATA_CACHE_PATH), exist_ok=True)
            with open(GITHUB_METADATA_CACHE_PATH, 'w', encoding='utf-8') as handle:
                json.dump(_github_cache, handle, ensure_ascii=False, indent=2)
        except OSError as error:
            print(f'GitHub metadata cache write failed: {error}', file=sys.stderr)


def _apply_github_metadata(item, data):
    pushed_at = data.get('pushed_at')
    if not pushed_at:
        return item
    enriched = dict(item)
    enriched.update({
        'time': pushed_at,
        'time_kind': 'repository_last_push',
        'updated_at': data.get('updated_at', ''),
        'created_at': data.get('created_at', ''),
        'language': data.get('language') or enriched.get('language', ''),
        'description': data.get('description') or enriched.get('description', ''),
        'stars': data.get('stargazers_count', ''),
    })
    return enriched


def fetch_github_repo_metadata(item):
    """Attach an accurate repository activity timestamp to a GitHub result."""
    slug = github_repo_slug(item.get('url'))
    if not slug:
        return item
    _load_github_cache()
    cached = _github_cache.get(slug)
    if isinstance(cached, dict) and time.time() - cached.get('fetched_at', 0) < GITHUB_METADATA_CACHE_TTL:
        return _apply_github_metadata(item, cached.get('data') or {})
    headers = {**HEADERS, 'Accept': 'application/vnd.github+json'}
    token = os.environ.get('GITHUB_TOKEN', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        response = requests.get(
            f'https://api.github.com/repos/{slug}', headers=headers, timeout=10
        )
        status_code = getattr(response, 'status_code', 200)
        if status_code in {403, 429}:
            response_headers = getattr(response, 'headers', {})
            remaining = response_headers.get('X-RateLimit-Remaining', '?')
            reset = response_headers.get('X-RateLimit-Reset', '?')
            print(
                f'GitHub metadata rate limited for {slug}: status={status_code}, '
                f'remaining={remaining}, reset={reset}',
                file=sys.stderr,
            )
        response.raise_for_status()
        data = response.json()
        _cache_github_metadata(slug, data)
        return _apply_github_metadata(item, data)
    except (requests.RequestException, ValueError, TypeError) as error:
        print(f'GitHub metadata fetch failed for {slug}: {error}', file=sys.stderr)
        return item


def enrich_github_items(items):
    """Fetch GitHub metadata while preserving the source ranking order."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_github_repo_metadata, item) for item in items]
        return [future.result() for future in futures]


def fetch_github(limit=5, keyword=None):
    if keyword:
         # Use GitHub Search for keywords
         query = f"{keyword.split(',')[0]} sort:updated" # Use first kw as primary
         url = f"https://github.com/search?q={requests.utils.quote(query)}&type=repositories"
         # Note: GitHub Search page is hard to scrape due to login requirements (often).
         # Fallback strat: Topics? "https://github.com/topics/{kw}?o=desc&s=updated"
         topic_url = f"https://github.com/topics/{keyword.split(',')[0].strip()}?o=desc&s=updated"
         try:
             response = requests.get(topic_url, headers=HEADERS, timeout=10)
             if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                items = []
                for article in soup.select('article.border'):
                     # Topic page structure changes often, but let's try generic selector
                     h3 = article.select_one('h3 a') 
                     # Actually standard topic page: <h3 class="f3"><a href="/user/repo">...
                     if not h3: continue
                     repo_link = h3['href'] # /user/repo
                     title = repo_link.strip('/')
                     link = "https://github.com" + repo_link
                     
                     desc = ""
                     desc_div = article.select_one('.color-fg-muted')
                     if desc_div: desc = desc_div.get_text(strip=True)
                     
                     items.append({
                        "source": "GitHub Trending", 
                        "title": f"{title} - {desc}", 
                        "url": link,
                        "heat": "Topic Match",
                        "time": ""
                     })
                if items:
                    return enrich_github_items(items[:limit])
         except: pass

    # Default Trending
    try:
        response = requests.get("https://github.com/trending", headers=HEADERS, timeout=10)
    except: return []
    
    soup = BeautifulSoup(response.text, 'html.parser')
    items = []
    for article in soup.select('article.Box-row'):
        try:
            h2 = article.select_one('h2 a')
            if not h2: continue
            title = h2.get_text(strip=True).replace('\n', '').replace(' ', '')
            link = "https://github.com" + h2['href']
            
            desc = article.select_one('p')
            desc_text = desc.get_text(strip=True) if desc else ""
            
            # Stars (Heat)
            # usually the first 'Link--muted' with a SVG star
            stars_tag = article.select_one('a[href$="/stargazers"]')
            stars = stars_tag.get_text(strip=True) if stars_tag else ""
            
            items.append({
                "source": "GitHub Trending", 
                "title": f"{title} - {desc_text}",
                "url": link,
                "heat": f"{stars} stars",
                "time": "",
                "is_trending": True,
            })
        except: continue
    return enrich_github_items(filter_items(items, keyword)[:limit])

def fetch_36kr(limit=5, keyword=None):
    try:
        response = requests.get("https://36kr.com/newsflashes", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        items = []
        for item in soup.select('.newsflash-item'):
            title = item.select_one('.item-title').get_text(strip=True)
            href = item.select_one('.item-title')['href']
            time_tag = item.select_one('.time')
            time_str = time_tag.get_text(strip=True) if time_tag else ""
            
            items.append({
                "source": "36Kr", 
                "title": title, 
                "url": f"https://36kr.com{href}" if not href.startswith('http') else href,
                "time": time_str,
                "heat": ""
            })
        return filter_items(items, keyword)[:limit]
    except: return []

def fetch_v2ex(limit=5, keyword=None):
    try:
        # Hot topics json
        data = requests.get("https://www.v2ex.com/api/topics/hot.json", headers=HEADERS, timeout=10).json()
        items = []
        for t in data:
            # V2EX API fields: created, replies (heat)
            replies = t.get('replies', 0)
            created = t.get('created', 0)
            # convert epoch to readable if possible, simpler to just leave as is or basic format
            # Let's keep it simple
            items.append({
                "source": "V2EX", 
                "title": t['title'], 
                "url": t['url'],
                "heat": f"{replies} replies",
                "time": datetime.fromtimestamp(created).isoformat() if created else ""
            })
        return filter_items(items, keyword)[:limit]
    except: return []

def fetch_tencent(limit=5, keyword=None):
    try:
        url = "https://i.news.qq.com/web_backend/v2/getTagInfo?tagId=aEWqxLtdgmQ%3D"
        data = requests.get(url, headers={"Referer": "https://news.qq.com/"}, timeout=10).json()
        items = []
        for news in data['data']['tabs'][0]['articleList']:
            items.append({
                "source": "Tencent News", 
                "title": news['title'], 
                "url": news.get('url') or news.get('link_info', {}).get('url'),
                "time": news.get('pub_time', '') or news.get('publish_time', '')
            })
        return filter_items(items, keyword)[:limit]
    except: return []

def fetch_wallstreetcn(limit=5, keyword=None):
    try:
        url = "https://api-one.wallstcn.com/apiv1/content/information-flow?channel=global-channel&accept=article&limit=30"
        data = requests.get(url, timeout=10).json()
        items = []
        for item in data['data']['items']:
            res = item.get('resource')
            if res and (res.get('title') or res.get('content_short')):
                 ts = res.get('display_time', 0)
                 time_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') if ts else ""
                 items.append({
                     "source": "Wall Street CN", 
                     "title": res.get('title') or res.get('content_short'), 
                     "url": res.get('uri'),
                     "time": time_str
                 })
        return filter_items(items, keyword)[:limit]
    except: return []

def fetch_producthunt(limit=5, keyword=None):
    try:
        # Using RSS for speed and reliability without API key
        response = requests.get("https://www.producthunt.com/feed", headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        items = []
        for entry in soup.find_all(['item', 'entry']):
            title = entry.find('title').get_text(strip=True)
            link_tag = entry.find('link')
            url = link_tag.get('href') or link_tag.get_text(strip=True) if link_tag else ""
            
            pubBox = entry.find('pubDate') or entry.find('published')
            pub = pubBox.get_text(strip=True) if pubBox else ""
            
            items.append({
                "source": "Product Hunt", 
                "title": title, 
                "url": url,
                "time": pub,
                "heat": "Top Product" # RSS implies top rank
            })
        return filter_items(items, keyword)[:limit]
    except: return []

# --- New Fetchers (RSS/API) ---

from rss_parser import fetch_rss_feed


from social_platforms import (
    fetch_bilibili,
    fetch_douyin,
    consume_filter_stats,
)

# fetch_tldr_ai removed: all known feed URLs (feed.tldr.tech/ai, tldr.tech/ai/rss) return 404.

def fetch_huggingface_papers(limit=5, keyword=None):
    items = []
    # User requested a "Good Solution" without fallback.
    # We use Playwright (which is installed) to bypass the SSL/fingerprinting connection issues.
    # Logic extracted to scripts/fetch_hf_papers_playwright.py for reusability
    
    try:
        import subprocess
        import sys
        import os
        
        # Locate the standalone script
        script_path = os.path.join(os.path.dirname(__file__), "fetch_hf_papers_playwright.py")
        
        # Run the playwright script in a subprocess
        cmd = [sys.executable, script_path, "--limit", str(limit)]
        # Increase timeout for detail fetch (10 pages * 5s = 50s + startup)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            for paper in data:
                items.append({
                    "source": "HF Papers",
                    "title": paper['title'],
                    "url": paper['url'],
                    "github": paper.get('github', ''),
                    "heat": paper.get('heat', ''),
                    "time": "", # The source does not expose an article publication timestamp.
                    "summary": paper.get('summary', '')
                })
        else:
             print(f"HF Playwright Failed: {result.stderr}", file=sys.stderr)
             
    except Exception as e:
        print(f"HF Playwright Exception: {e}", file=sys.stderr)
            
    return filter_items(items[:limit], keyword)


def fetch_latentspace_ainews(limit=5, keyword=None):
    """Fetch AINews daily roundups from Latent Space Substack RSS.
    Filters for posts with [AINews] title prefix, separating them from podcast episodes."""
    items = []
    try:
        response = requests.get("https://www.latent.space/feed", headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        for entry in soup.find_all('item'):
            title_tag = entry.find('title')
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            
            # Filter: only AINews posts (title starts with [AINews])
            if not title.startswith('[AINews]'):
                continue
            
            # Extract link from guid (Substack RSS has link text empty, guid has the URL)
            guid_tag = entry.find('guid')
            link = guid_tag.get_text(strip=True) if guid_tag else ""
            
            # Fallback: try link tag
            if not link:
                link_tag = entry.find('link')
                if link_tag:
                    link = link_tag.get_text(strip=True) or (link_tag.get('href') or '')
            
            # Publication date
            pub_tag = entry.find('pubdate') or entry.find('published')
            pub_date = pub_tag.get_text(strip=True) if pub_tag else ""
            # Simplify date if possible
            try:
                dt = parsedate_to_datetime(pub_date)
                pub_date = dt.strftime('%Y-%m-%d')
            except Exception:
                pass
            
            # Content snippet from description
            desc_tag = entry.find('description')
            content = ""
            if desc_tag:
                desc_html = desc_tag.get_text(strip=True)
                desc_soup = BeautifulSoup(desc_html, 'html.parser')
                content = desc_soup.get_text(separator=' ', strip=True)[:2000]
            
            items.append({
                "source": "Latent Space AINews",
                "title": title,
                "url": link,
                "time": pub_date,
                "heat": "Daily Roundup",
                "content": content
            })
    except Exception as e:
        print(f"Latent Space AINews fetch error: {e}", file=sys.stderr)
    
    return filter_items(items[:limit], keyword)


# --- Extended Sources (v2): Lobsters / Dev.to / arXiv / Papers with Code / 少数派 / 即刻 / User OPML ---

def fetch_lobsters(limit=5, keyword=None):
    """Lobsters hottest stories via official JSON API."""
    items = []
    try:
        data = requests.get("https://lobste.rs/hottest.json", headers=HEADERS, timeout=10).json()
        for story in data:
            tags = ",".join(story.get('tags', []))
            items.append({
                "source": "Lobsters",
                "title": story.get('title', ''),
                "url": story.get('url') or story.get('short_id_url') or story.get('comments_url', ''),
                "comments_url": story.get('comments_url', ''),
                "heat": f"{story.get('score', 0)} points",
                "time": story.get('created_at', '')[:10] if story.get('created_at') else '',
                "tags": tags,
            })
    except Exception as e:
        print(f"Lobsters fetch error: {e}", file=sys.stderr)
    return filter_items(items[:limit], keyword)


def fetch_devto(limit=5, keyword=None):
    """Dev.to top articles of the past 24h via official JSON API."""
    items = []
    try:
        url = "https://dev.to/api/articles?top=1&per_page=30"
        data = requests.get(url, headers=HEADERS, timeout=10).json()
        for art in data:
            tag_list = art.get('tag_list', [])
            tags = ",".join(tag_list) if isinstance(tag_list, list) else str(tag_list)
            items.append({
                "source": "Dev.to",
                "title": art.get('title', ''),
                "url": art.get('url', ''),
                "heat": f"{art.get('positive_reactions_count', 0)} reactions",
                "like": art.get('positive_reactions_count', 0),
                "comment": art.get('comments_count', 0),
                "view": art.get('page_views_count', 0),
                "time": (art.get('published_at') or '')[:10],
                "summary": art.get('description', ''),
                "tags": tags,
            })
    except Exception as e:
        print(f"Dev.to fetch error: {e}", file=sys.stderr)
    return filter_items(items[:limit], keyword)


def fetch_devto_react(limit=5, keyword=None):
    """Dev.to React 专区 RSS - 抓取带 react 标签的文章"""
    return filter_items(fetch_rss_feed("https://dev.to/feed/tag/react", "Dev.to React", limit * 2)[:limit], keyword)


def fetch_react_blog(limit=5, keyword=None):
    """React 官方博客 RSS"""
    return filter_items(fetch_rss_feed("https://react.dev/rss.xml", "React Blog", limit * 2)[:limit], keyword)


def fetch_openai_blog(limit=5, keyword=None):
    """OpenAI 官方新闻博客 RSS"""
    return filter_items(fetch_rss_feed("https://openai.com/news/rss.xml", "OpenAI Blog", limit * 2)[:limit], keyword)


def fetch_anthropic_blog(limit=5, keyword=None):
    """Anthropic 官方新闻博客 RSS（第三方镜像）"""
    return filter_items(fetch_rss_feed(
        "https://raw.githubusercontent.com/taobojlen/anthropic-rss-feed/main/anthropic_news_rss.xml",
        "Anthropic Blog", limit * 2)[:limit], keyword)


def fetch_juejin_page_time(url):
    """Read the article's canonical publication time from its rendered HTML."""
    if not url or not is_safe_http_url(url):
        return ""
    try:
        response = requests.get(url, headers=HEADERS, timeout=8)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        time_tag = soup.select_one('time.time[datetime], time[datetime]')
        if not time_tag:
            return ""
        value = time_tag.get('datetime', '').strip()
        return value if parse_item_datetime({'time': value}) else ""
    except (OSError, ValueError, requests.RequestException):
        return ""


def enrich_juejin_page_times(items):
    """Fill missing ranking timestamps from article pages with bounded concurrency."""
    missing = [item for item in items if not item.get('time') and item.get('url')]
    if not missing:
        return items
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_juejin_page_time, item['url']): item for item in missing}
        for future in concurrent.futures.as_completed(futures):
            item = futures[future]
            try:
                page_time = future.result()
            except Exception:
                page_time = ""
            if page_time:
                item['time'] = page_time
                item['time_source'] = 'article_page'
            else:
                item['time_source'] = 'missing'
    return items


def fetch_juejin(limit=5, keyword=None):
    """掘金热榜文章 via 官方 API"""
    items = []
    try:
        url = "https://api.juejin.cn/content_api/v1/content/article_rank"
        params = {"category_id": "1", "type": "hot", "limit": limit * 2}
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        for art in data.get("data", []):
            content = art.get("content", {})
            counter = art.get("content_counter", {})
            author = art.get("author", {})
            api_time = content.get('ctime') or content.get('mtime') or ""
            items.append({
                "source": "掘金热榜",
                "title": content.get("title", ""),
                "url": f"https://juejin.cn/post/{content.get('content_id', '')}",
                "author": author.get("name", ""),
                "hot_rank": counter.get('hot_rank', 0),
                "view": counter.get('view', 0),
                "like": counter.get('like', 0),
                "comment": counter.get('comment', 0),
                "collect": counter.get('collect', 0),
                "share": counter.get('share', 0),
                "heat": counter.get('hot_rank', 0),  # 主指标用 hot_rank
                "time": api_time if api_time else "",
                "time_source": 'api' if api_time else 'missing',
            })
    except Exception as e:
        print(f"Juejin fetch error: {e}", file=sys.stderr)
    return filter_items(enrich_juejin_page_times(items[:limit]), keyword)


def fetch_sspai(limit=5, keyword=None):
    """少数派 latest articles via RSS."""
    return filter_items(fetch_rss_feed("https://sspai.com/feed", "少数派", limit * 2)[:limit], keyword)


# NOTE: Papers with Code (paperswithcode.com) was acquired/merged by Hugging Face
# and now 302-redirects to huggingface.co/papers/trending. Duplicates `huggingface` source.
# Removed from v2 sources. Use --source huggingface for trending papers.


def fetch_arxiv(limit=5, keyword=None, categories=None):
    """arXiv latest submissions in given CS categories via official Atom API.
    arXiv 接口偶尔较慢，最多重试 3 次（timeout=45s，退避 2s/4s）。"""
    cats = categories or ['cs.AI', 'cs.CL', 'cs.LG']
    cat_query = '+OR+'.join(f'cat:{c}' for c in cats)
    url = (
        f"http://export.arxiv.org/api/query"
        f"?search_query={cat_query}"
        f"&sortBy=submittedDate&sortOrder=descending"
        f"&max_results={limit * 2}"
    )
    items = []
    from rss_parser import parse_rss_content
    for attempt in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=45)
            response.encoding = response.apparent_encoding or 'utf-8'
            items = parse_rss_content(response.content, "arXiv", limit * 2)
            if items:
                break
        except Exception as e:
            print(f"arXiv attempt {attempt + 1}/3 error: {e}", file=sys.stderr)
        if attempt < 2:
            time.sleep(2 * (attempt + 1))  # 2s, 4s
    return filter_items(items[:limit], keyword)


def fetch_infoq_cn(limit=5, keyword=None):
    """InfoQ 中文站最新文章 via RSS。"""
    return filter_items(fetch_rss_feed("https://www.infoq.cn/feed.xml", "InfoQ 中文", limit * 2)[:limit], keyword)


def fetch_aihot(limit=15, keyword=None):
    """AIHOT (aihot.virxact.com) AI 精选聚合，跨源中文编辑稿，日更 ~50 条。
    默认拉最近 24h 内容（日更源，50 条/天，取最多 limit 条）。"""
    raw = fetch_rss_feed("https://aihot.virxact.com/rss", "AIHOT", max(limit * 4, 50))
    items = source_time_filter(raw, hours=24)
    return filter_items(items[:limit], keyword)


def fetch_tldr_ai(limit=3, keyword=None):
    """TLDR AI 英文每日 AI 摘要，5-10 主题/期。
    默认拉最近 48h（日刊时间戳为午夜 UTC，48h 确保任意时段都能拿到最新 1-2 期）。"""
    raw = fetch_rss_feed("https://tldr.tech/api/rss/ai", "TLDR AI", limit * 4)
    items = source_time_filter(raw, hours=48)
    return filter_items(items[:limit], keyword)


def fetch_import_ai(limit=2, keyword=None):
    """Import AI by Jack Clark（前 OpenAI/Anthropic 联创）周更深度评论。
    默认拉最近 7 天（周刊，1 条 = 1 期 = 1 周），通常返回最新 1 期。"""
    raw = fetch_rss_feed("https://importai.substack.com/feed", "Import AI", limit * 4)
    items = source_time_filter(raw, hours=168)  # 7 days
    return filter_items(items[:limit], keyword)


INTERNATIONAL_NEWS_SOURCES = [
    ("BBC Top News", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC Chinese", "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"),
    ("The Guardian World", "https://www.theguardian.com/world/rss"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("France 24", "http://www.france24.com/en/rss"),
]

REUTERS_GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?"
    "q=site%3Areuters.com%20when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"
)

INTERNATIONAL_NEWS_MAX_AGE_HOURS = 24


def fetch_recent_rss_feed(url, source_name, limit=10, keyword=None, hours=INTERNATIONAL_NEWS_MAX_AGE_HOURS):
    """Fetch an RSS feed and keep only items from the recent time window."""
    raw = fetch_rss_feed(url, source_name, max(limit * 4, 30))
    items = source_time_filter(raw, hours=hours)
    return filter_items(items, keyword)[:limit]


def create_recent_rss_fetcher(url, name, hours=INTERNATIONAL_NEWS_MAX_AGE_HOURS):
    def fetcher(limit=5, keyword=None):
        return fetch_recent_rss_feed(url, name, limit, keyword, hours)
    return fetcher


def fetch_reuters(limit=10, keyword=None):
    """Reuters public fallback via Google News RSS.
    Reuters.com no longer exposes a reliable unauthenticated public RSS feed."""
    items = fetch_rss_feed(
        REUTERS_GOOGLE_NEWS_RSS,
        "Reuters (Google News fallback)",
        max(limit * 4, 30),
    )
    items = source_time_filter(items, hours=INTERNATIONAL_NEWS_MAX_AGE_HOURS)
    for item in items:
        title = item.get('title', '')
        if title.endswith(' - Reuters'):
            item['title'] = title[:-10]
        item['fallback'] = "Google News RSS search for site:reuters.com"
    return filter_items(items, keyword)[:limit]


def fetch_international(limit=15, keyword=None):
    """Aggregate official international RSS feeds plus Reuters fallback."""
    per_source = max(2, min(5, limit // 3))
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        tasks = [
            (name, executor.submit(fetch_recent_rss_feed, url, name, per_source, keyword))
            for name, url in INTERNATIONAL_NEWS_SOURCES
        ]
        tasks.append(("Reuters", executor.submit(fetch_reuters, per_source, keyword)))

        item_groups = []
        for name, future in tasks:
            try:
                items = future.result()
                if items:
                    item_groups.append(items)
            except Exception as e:
                print(f"International fetch error for {name}: {e}", file=sys.stderr)

    merged = []
    max_group_len = max((len(group) for group in item_groups), default=0)
    for index in range(max_group_len):
        for group in item_groups:
            if index < len(group):
                merged.append(group[index])
            if len(merged) >= limit:
                return merged
    return merged


def fetch_user_feeds(limit=5, keyword=None):
    """Fetch user-defined RSS feeds from an OPML file.
    Looks at ~/.config/news-aggregator/user_sources.opml first,
    then <skill_root>/user_sources.opml."""
    try:
        # Add scripts/ to path for direct import
        sys.path.insert(0, os.path.dirname(__file__))
        from fetch_user_feeds import find_opml_file, parse_opml, fetch_all_feeds
        opml_path = find_opml_file()
        if not opml_path:
            print("No OPML configured. See user_sources.opml.example", file=sys.stderr)
            return []
        feeds = parse_opml(opml_path)
        if not feeds:
            return []
        items = fetch_all_feeds(feeds, limit_per_feed=3)
        return filter_items(items[:limit], keyword)
    except Exception as e:
        print(f"User feeds error: {e}", file=sys.stderr)
        return []


# --- Source Definitions (Global for Access) ---

AI_NEWSLETTER_SOURCES = [
    # Bens Bites is protected by Cloudflare -> Use Playwright
    ("Ben's Bites", "https://www.bensbites.com/feed"), 
    ("Interconnects", "https://www.interconnects.ai/feed"),  # Fixed: needs www.
    ("One Useful Thing", "https://www.oneusefulthing.org/feed"), 
    # Removed: The Rundown (beehiiv feed 404), The Neuron (403 Forbidden)
    ("ChinAI", "https://chinai.substack.com/feed"),
    ("Memia", "https://memia.substack.com/feed"),
    ("AI to ROI", "https://ai2roi.substack.com/feed"),
    ("KDnuggets", "https://www.kdnuggets.com/feed"),
]

# ... (rest of sources)

def fetch_rss_with_playwright(url, source_name, limit=5):
    """Fallback fetcher using Playwright to bypass Cloudflare"""
    try:
        # Special handling for Ben's Bites which uses custom Homepage Scraper
        if "Ben's Bites" in source_name:
             script_path = os.path.join(os.path.dirname(__file__), "fetch_bensbites.py")
             # No arguments needed, script hardcodes URL
             cmd = [sys.executable, script_path]
             
             
             result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
             
             if result.returncode == 0:
                 try:
                    data = json.loads(result.stdout)
                    if not data: raise ValueError("Empty JSON")
                    return data
                 except Exception:
                    # Fallback for Ben's Bites if parsing fails
                    return [{
                        "source": "Ben's Bites",
                        "title": "Ben's Bites (Visit Site)",
                        "url": "https://bensbites.beehiiv.com/",
                "time": "",
                        "summary": "Auto-fetch failed. Please verify on site.",
                    }]
             else:
                 return [{
                        "source": "Ben's Bites",
                        "title": "Ben's Bites (Check Site)",
                        "url": "https://bensbites.beehiiv.com/",
                "time": "",
                        "summary": "Fetch process failed.",
                    }]

        # Use the generic Playwright script for all other protected feeds.
        script_path = os.path.join(
            os.path.dirname(__file__), "fetch_generic_playwright.py"
        )
        cmd = [sys.executable, script_path, url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)

        if result.returncode == 0:
            from rss_parser import parse_rss_content
            # Result stdout should be the HTML/XML content
            return parse_rss_content(result.stdout, source_name, limit)
        else:
            print(f"Playwright fetch failed for {source_name}: {result.stderr}", file=sys.stderr)
            return []
    except Exception as e:
        print(f"Playwright exception for {source_name}: {e}", file=sys.stderr)
        return []


PODCAST_SOURCES = [
    ("Lex Fridman", "https://lexfridman.com/feed/podcast"),
    # Removed: Cognitive Rev (megaphone.fm feed 404)
    ("80000 Hours", "https://feeds.transistor.fm/80-000-hours-podcast"),
    ("Latent Space", "https://latent.space/feed"),
]

ESSAY_SOURCES = [
    ("Wait But Why", "https://waitbutwhy.com/feed"),
    ("James Clear", "https://jamesclear.com/feed"),
    ("Farnam Street", "https://fs.blog/feed"),
    ("Paul Graham", "http://www.aaronsw.com/2002/feeds/pgessays.rss"), 
    ("Scott Young", "https://www.scotthyoung.com/blog/feed/"),
    ("Dan Koe", "https://thedankoe.com/feed/"),
]

def fetch_ai_newsletters(limit=5, keyword=None):
    """Aggregate Fetcher for AI Newsletters"""
    all_items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_rss_feed, url, name, 3): name for name, url in AI_NEWSLETTER_SOURCES}
        for future in concurrent.futures.as_completed(futures):
            all_items.extend(future.result())
    return filter_items(all_items, keyword)[:limit]

def fetch_podcasts(limit=5, keyword=None):
    all_items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_rss_feed, url, name, 3): name for name, url in PODCAST_SOURCES}
        for future in concurrent.futures.as_completed(futures):
            all_items.extend(future.result())
    return filter_items(all_items, keyword)[:limit]

def fetch_essays(limit=5, keyword=None):
    all_items = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_rss_feed, url, name, 3): name for name, url in ESSAY_SOURCES}
        for future in concurrent.futures.as_completed(futures):
            all_items.extend(future.result())
    return filter_items(all_items, keyword)[:limit]

def create_single_rss_fetcher(url, name):
    def fetcher(limit=5, keyword=None):
        return filter_items(fetch_rss_feed(url, name, limit), keyword)[:limit]
    return fetcher


def save_report(data, source_name, out_dir):
    """
    Saves JSON and generates a simple Markdown report.
    """
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
        
    # Sanitize source name for filename
    safe_name = "".join([c if c.isalnum() else "_" for c in source_name]).lower()
    timestamp = datetime.now().strftime("%H%M")
    
    # 1. Save JSON
    json_path = os.path.join(out_dir, f"{safe_name}_{timestamp}.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        
    return json_path

def main():
    parser = argparse.ArgumentParser()
    sources_map = {
        'hackernews': fetch_hackernews, 'github': fetch_github,
        'douyin': fetch_douyin,
        'bilibili': fetch_bilibili,
        'youtube_tech': create_recent_rss_fetcher("https://www.youtube.com/feeds/videos.xml?channel_id=UCXuqSBlHAE6Xw-yeJA0Tunw", "YouTube 科技频道"),
        '36kr': fetch_36kr, 'v2ex': fetch_v2ex, 'tencent': fetch_tencent,
        'wallstreetcn': fetch_wallstreetcn, 'producthunt': fetch_producthunt,
        # Aggregates
        'huggingface': fetch_huggingface_papers,
        'ai_newsletters': fetch_ai_newsletters, 'podcasts': fetch_podcasts,
        'essays': fetch_essays,
        # Standalone AI Sources
        'latentspace_ainews': fetch_latentspace_ainews,
        # Extended (v2): tech community / academic / Chinese deep-content / user OPML
        'lobsters': fetch_lobsters,
        'devto': fetch_devto,
        'devto_react': fetch_devto_react,
        'react_blog': fetch_react_blog,
        'openai': fetch_openai_blog,
        'anthropic': fetch_anthropic_blog,
        'juejin': fetch_juejin,
        'sspai': fetch_sspai,
        'infoq_cn': fetch_infoq_cn,
        'arxiv': fetch_arxiv,
        # Curated AI aggregators (v3): one feed = many original sources, pre-curated
        'aihot': fetch_aihot,
        'tldr_ai': fetch_tldr_ai,
        'import_ai': fetch_import_ai,
        # International news (official RSS where available; Reuters uses a marked fallback)
        'international': fetch_international,
        'bbc_top': create_recent_rss_fetcher("https://feeds.bbci.co.uk/news/rss.xml", "BBC Top News"),
        'bbc_world': create_recent_rss_fetcher("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC World"),
        'bbc_chinese': create_recent_rss_fetcher("https://feeds.bbci.co.uk/zhongwen/simp/rss.xml", "BBC Chinese"),
        'guardian_world': create_recent_rss_fetcher("https://www.theguardian.com/world/rss", "The Guardian World"),
        'aljazeera': create_recent_rss_fetcher("https://www.aljazeera.com/xml/rss/all.xml", "Al Jazeera"),
        'france24': create_recent_rss_fetcher("http://www.france24.com/en/rss", "France 24"),
        'reuters': fetch_reuters,
        'user': fetch_user_feeds,
    }

    # Dynamic Registration of Sub-sources
    # AI Newsletters
    for name, url in AI_NEWSLETTER_SOURCES:
        key = name.lower().replace(' ', '').replace("'", "")
        # Check if this source needs Playwright
        if "Ben's Bites" in name:
             sources_map[key] = lambda limit=10, k=None, u=url, n=name: filter_items(fetch_rss_with_playwright(u, n, limit), k)[:limit]
        else:
             sources_map[key] = create_single_rss_fetcher(url, name)
        
    # Podcasts
    for name, url in PODCAST_SOURCES:
        key = name.lower().replace(' ', '')
        sources_map[key] = create_single_rss_fetcher(url, name)

    # Essays
    for name, url in ESSAY_SOURCES:
        key = name.lower().replace(' ', '')
        sources_map[key] = create_single_rss_fetcher(url, name)
    
    parser.add_argument('--source', default='all', help='Source(s) to fetch from (comma-separated). Now supports sub-sources like "chinai", "paulgraham"')
    parser.add_argument('--limit', type=int, default=10, help='Limit per source. Default 10')
    parser.add_argument('--keyword', help='Comma-sep keyword filter')
    parser.add_argument('--deep', action='store_true', help='Download article content for detailed summarization')
    parser.add_argument('--hours', type=int, default=72, help='Only keep items published within the last N hours (default: 72)')
    parser.add_argument('--skip-time-filter', action='store_true',
                        help='Preserve raw time candidates for downstream AI interpretation')
    parser.add_argument('--save', action='store_true', help='Save output to reports directory (JSON + MD)')
    parser.add_argument('--no-save', action='store_true', dest='no_save', help='Skip saving JSON files to disk (only output to stdout)')
    parser.add_argument('--outdir', help='Custom output directory for saved reports')
    parser.add_argument('--list-sources', action='store_true', help='List all available source keys')
    parser.add_argument('--stats-file', help='Write per-source fetch and time-filter statistics as JSON')
    
    args = parser.parse_args()

    global AI_TIME_INTERPRETATION_MODE
    AI_TIME_INTERPRETATION_MODE = args.skip_time_filter

    if args.list_sources:
        print(f"{'Source Key':<20}")
        print("-" * 40)
        for key in sorted(sources_map.keys()):
            print(f"{key:<20}")
        return
    
    to_run = []
    if args.source == 'all':
        to_run = [(key, func) for key, func in sources_map.items()]
    else:
        requested_sources = [s.strip() for s in args.source.split(',')]
        for s in requested_sources:
            if s in sources_map:
                to_run.append((s, sources_map[s]))
            
    results = []
    
    source_stats = {}

    def run_fetchers(fetchers, limit, kw):
        res = []
        for source_key, func in fetchers:
            entry = source_stats.setdefault(source_key, {'requested': 0, 'returned': 0, 'errors': []})
            entry['requested'] += limit
            try:
                fetched = func(limit, kw)
                entry['returned'] += len(fetched)
                for item in fetched:
                    time_source = item.get('time_source')
                    if time_source:
                        sources = entry.setdefault('time_sources', {})
                        sources[time_source] = sources.get(time_source, 0) + 1
                social_filtered = consume_filter_stats()
                if social_filtered:
                    entry['filtered'] = social_filtered
                res.extend(fetched)
            except Exception as error:
                entry['errors'].append(str(error)[:300])
                print(f"Fetcher {getattr(func, '__name__', repr(func))} failed: {error}", file=sys.stderr)
        return res

    # Primary Fetch
    results = run_fetchers(to_run, args.limit, args.keyword)
    time_stats = {}
    if not args.skip_time_filter:
        results = filter_by_hours(results, hours=args.hours, stats=time_stats)
        
    # Smart Fill Logic (Only if keyword is used and results are sparse)
    MIN_ITEMS = 5
    if args.keyword and len(results) < MIN_ITEMS:
        sys.stderr.write(f"Smart Fill triggered: Found {len(results)} items, filling gaps...\n")
        
        # Secondary Fetch (Broad, no keyword)
        # We fetch enough to potentially fill the gap, limit=MIN_ITEMS is a safe bet for each source
        fill_limit = MIN_ITEMS 
        fill_results = run_fetchers(to_run, limit=fill_limit, kw=None)
        if not args.skip_time_filter:
            fill_results = filter_by_hours(fill_results, hours=args.hours, stats=time_stats)
        
        # Deduplicate and Append
        existing_urls = {item.get('url') for item in results}
        existing_titles = {item.get('title') for item in results}
        
        for item in fill_results:
            if len(results) >= MIN_ITEMS:
                break
                
            u = item.get('url')
            t = item.get('title')
            
            if u not in existing_urls and t not in existing_titles:
                # Mark as smart fill
                item['smart_fill'] = True
                
                # Add warning to time field as per SKILL.md
                if 'time' in item:
                    item['time'] = f"⚠️ {item['time']}"
                
                results.append(item)
                existing_urls.add(u)
                existing_titles.add(t)

    # Apply the window again after smart fill so no fallback can reintroduce stale items.
    if not args.skip_time_filter:
        results = filter_by_hours(results, hours=args.hours, stats=time_stats)

    if args.stats_file:
        try:
            with open(args.stats_file, 'w', encoding='utf-8') as handle:
                json.dump({
                    'source': args.source,
                    'hours': args.hours,
                    'time_filter_skipped': args.skip_time_filter,
                    'fetchers': source_stats,
                    'time_filter': time_stats,
                    'final_count': len(results),
                }, handle, ensure_ascii=False, indent=2)
        except OSError as error:
            print(f"Stats file write failed: {error}", file=sys.stderr)

    if args.deep and results:
        sys.stderr.write(f"Deep fetching content for {len(results)} items...\n")
        results = enrich_items_with_content(results)
        
    print(json.dumps(results, indent=2, ensure_ascii=False))
    
    # Save Report if requested or if running a single source (implicit convenience)
    # Skip saving when --no-save is set (agent reads from stdout)
    if not getattr(args, 'no_save', False) and (args.save or args.source != 'all'):
        if args.outdir:
            out_dir = args.outdir
        else:
            today = datetime.now().strftime('%Y-%m-%d')
            out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'reports', today)
            
        md_file = save_report(results, args.source, out_dir)
        sys.stderr.write(f"\n[Saved] Raw Data: {md_file} (Agent to process)\n")

if __name__ == "__main__":
    main()
