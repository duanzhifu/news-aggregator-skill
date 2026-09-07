import os
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

skill_root = Path(__file__).resolve().parent.parent
if str(skill_root) not in sys.path:
    sys.path.insert(0, str(skill_root))

from scripts.fetch_bing_search import fetch_search_results_bing
from scripts.domain_mapper import clean_and_map_url, is_site_homepage


ANYSEARCH_API_URL = os.environ.get("ANYSEARCH_API_BASE_URL", "https://api.anysearch.com/v1/search")


def _load_anysearch_api_key():
    """读 skill 根 .env 的 ANYSEARCH_API_KEY（优先环境变量，其次 .env 文件）。"""
    key = os.environ.get("ANYSEARCH_API_KEY", "").strip()
    if key:
        return key
    env_path = skill_root / ".env"
    if env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line.startswith("ANYSEARCH_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass
    return ""


def fetch_search_results_anysearch(keyword, limit=5, api_key=None):
    """调 AnySearch /v1/search，返回与 fetch_search_results_bing 同结构的 results 列表。

    AnySearch 是境外 API，走系统代理（同 llm_client 的 urllib 默认行为）。
    任何失败（无 key / HTTP 错误 / 网络 / 空结果）返回 []，由上层决定是否回退 Bing。
    """
    api_key = api_key or _load_anysearch_api_key()
    max_results = max(1, min(int(limit), 10))
    payload = {
        "query": keyword,
        "zone": "cn",
        "language": "zh-CN",
        "max_results": max_results,
    }
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "X-Anysearch-Client": "skill/3.1.1",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ANYSEARCH_API_URL, data=data, headers=headers, method="POST")
    print(f"[AnySearch] 正在检索关键词: '{keyword}'")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[AnySearch Error] '{keyword}': HTTP {e.code} {detail}", file=sys.stderr)
        return []
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[AnySearch Error] '{keyword}' 连接失败: {e}", file=sys.stderr)
        return []
    if body.get("code", 0) != 0:
        print(f"[AnySearch Error] '{keyword}': {body.get('message')}", file=sys.stderr)
        return []
    data = body.get("data") or {}
    results = []
    for r in data.get("results") or []:
        title = r.get("title") or ""
        url = r.get("url") or ""
        desc = r.get("content") or r.get("snippet") or ""
        if title and url:
            results.append({
                "title": title,
                "url": url,
                "summary": desc,
                "time": r.get("time") or "未知",
                "source": "anysearch",
            })
    return results


def fetch_dynamic_search_news(topics: list, limit_per_topic: int = 3, engine: str = "anysearch") -> list:
    """动态搜索主入口。engine='anysearch' 走 AnySearch（默认），'bing' 走 Bing 登录态。"""
    all_items = []
    seen_urls = set()

    for topic in topics:
        if not topic.strip():
            continue
        print(f"[DynamicSearch] 正在全网检索主题: '{topic}' (engine={engine})")
        try:
            if engine == "bing":
                results = fetch_search_results_bing(topic, limit=limit_per_topic)
            else:
                results = fetch_search_results_anysearch(topic, limit=limit_per_topic)
                if not results:
                    print(f"[DynamicSearch] AnySearch 对 '{topic}' 无结果，回退 Bing 登录态")
                    results = fetch_search_results_bing(topic, limit=limit_per_topic)
            for r in results:
                raw_url = r.get("url")
                if not raw_url:
                    continue

                real_url, source_key, source_cn = clean_and_map_url(raw_url)
                if real_url in seen_urls:
                    continue
                if is_site_homepage(real_url):
                    print(f"[DynamicSearch] 跳过官网首页: {real_url}")
                    continue
                seen_urls.add(real_url)

                item = {
                    "title": r.get("title"),
                    "url": real_url,
                    "summary": r.get("summary"),
                    "time": r.get("time"),
                    "source": source_key,
                    "original_source_name": source_cn,
                    "topic_matched": topic,
                }
                all_items.append(item)
        except Exception as e:
            print(f"[DynamicSearch Error] 主题 '{topic}' 检索失败: {e}", file=sys.stderr)

    return all_items


if __name__ == "__main__":
    test_topics = ["大模型", "SDD"]
    engine = sys.argv[1] if len(sys.argv) > 1 else "anysearch"
    items = fetch_dynamic_search_news(test_topics, limit_per_topic=2, engine=engine)
    print(json.dumps(items, ensure_ascii=False, indent=2))
