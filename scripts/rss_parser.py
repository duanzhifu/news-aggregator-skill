
import sys
import requests
from bs4 import BeautifulSoup
import re
import time

def clean_text(text):
    if not text: return ""
    text = text.strip()
    # html.parser leaves CDATA markers intact; strip them
    text = re.sub(r'^\s*<!\[CDATA\[|\]\]>\s*$', '', text).strip()
    return text

def parse_rss_content(content, source_name, limit=5):
    """
    Parses RSS/Atom content string (XML or HTML) and returns items.
    """
    try:
        # Use html.parser which is built-in and lenient 
        soup = BeautifulSoup(content, 'html.parser')
        
        items = []
        entries = soup.find_all(['item', 'entry'])
        
        for entry in entries:
            # --- Title ---
            title_tag = entry.find('title')
            if not title_tag: continue
            title = clean_text(title_tag.get_text())
            
            # --- Link ---
            link = ""
            link_tag = entry.find('link')
            if link_tag:
                if link_tag.has_attr('href'):
                    link = link_tag['href']
                elif link_tag.get_text(strip=True):
                    link = link_tag.get_text(strip=True)
                if not link:
                    # 仅当相邻节点是纯文本（无标签名）时才取，避免把下一个兄弟元素 HTML 污染进 url
                    sibling = link_tag.next_sibling
                    if sibling is not None and getattr(sibling, 'name', None) is None:
                        link = str(sibling).strip()
            
            if not link:
                guid = entry.find('guid')
                if guid and guid.get_text(strip=True).startswith('http'):
                    link = guid.get_text(strip=True)

            # --- Time ---
            pub = entry.find(['pubdate', 'published', 'updated', 'dc:date'])
            time_str = clean_text(pub.get_text()) if pub else ""
            
            # --- Summary / Content ---
            content_encoded = entry.find('content:encoded')
            description = entry.find('description')
            summary = entry.find('summary')
            content_el = entry.find('content')
            
            raw_summary = ""
            if content_encoded: raw_summary = content_encoded.get_text()
            elif description: raw_summary = description.get_text()
            elif summary: raw_summary = summary.get_text()
            elif content_el: raw_summary = content_el.get_text()
            
            soup_desc = BeautifulSoup(raw_summary, 'html.parser')
            _summary_text = soup_desc.get_text(separator=' ', strip=True)
            clean_summary = (_summary_text[:300] + "...") if len(_summary_text) > 300 else _summary_text
            
            # --- Heat / Engagement Metrics ---
            # 尝试多种字段名（不同平台使用的 RSS 扩展不一样）
            metrics = {}
            
            # 评论数（slash:comments 是 RSS 2.0 标准）
            comments = entry.find('slash:comments')
            if comments:
                metrics['comments'] = clean_text(comments.get_text())
            
            # 通用命名空间
            for tag_name in [
                'likes', 'like', 'thumbsup', 'thumbs', 'upvotes', 'upvote',
                'votes', 'score', 'points', 'stars', 'bookmarks', 'favorites',
                'views', 'hits', 'downloads', 'reactions', 'shares', 'reads',
                'replies', 'responses', 'claps', 'hearts', 'saves',
            ]:
                el = entry.find(tag_name)
                if el:
                    metrics[tag_name] = clean_text(el.get_text())
            
            # 第三方命名空间（Medium 用 claps、YouTube 也有 likes 等）
            for prefix in ['a10:', 'media:', 'yt:', 'thread:', 'itunes:', 'dc:']:
                for tag_name in ['likes', 'views', 'comments', 'shares', 'rating']:
                    el = entry.find(f'{prefix}{tag_name}')
                    if el:
                        metrics[tag_name] = clean_text(el.get_text())
            
            # 拼接 heat 字符串（保持向后兼容）
            heat_parts = []
            if metrics.get('points'):
                heat_parts.append(f"{metrics['points']} points")
            elif metrics.get('score'):
                heat_parts.append(f"{metrics['score']} score")
            if metrics.get('comments'):
                heat_parts.append(f"{metrics['comments']} comments")
            elif metrics.get('replies'):
                heat_parts.append(f"{metrics['replies']} replies")
            heat = ', '.join(heat_parts) if heat_parts else ""
            
            item = {
                "source": source_name,
                "title": title,
                "url": link,
                "time": time_str,
                "heat": heat,
                "summary": clean_summary
            }
            # 附加所有指标（None / 0 不会进字典）
            for k, v in metrics.items():
                if v:
                    item[f'metric_{k}'] = v
            
            items.append(item)
            if len(items) >= limit: break
            
        return items
    except Exception as e:
        print(f"Content Parse failed: {e}", file=sys.stderr)
        return []

def fetch_rss_feed(url, source_name, limit=5):
    """
    Robust RSS/Atom fetcher using BeautifulSoup.
    Handles various feed formats (RSS 2.0, Atom, etc.)
    """
    # User-Agent is critical
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    }

    last_error = None
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or 'utf-8'
            return parse_rss_content(response.content, source_name, limit)
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(1 + attempt)

    print(f"RSS Fetch failed for {url}: {last_error}", file=sys.stderr)
    return []
