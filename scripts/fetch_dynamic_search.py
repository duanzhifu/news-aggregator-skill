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
from scripts.llm_client import call_llm, extract_json_block


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


def optimize_search_query(topic, max_variants=3):
    """LLM 消歧改写搜索词，输出 1~3 个明确变体；任何异常回退 [原词]。

    用于「搜索词优化」开关（search_query_optimization）开启时：多义词（如 trellis）
    交给 LLM 消歧，避免搜索引擎返回错误义项。失败不阻断搜索，回退原词直搜。
    """
    prompt = (
        "你是搜索引擎查询优化助手，负责为技术/AI 类内容搜索消歧。\n"
        f"搜索词：{topic!r}\n"
        "判断该词是否存在歧义（多义词/多义项）：\n"
        "- 无歧义：返回只含原词一个元素的 JSON 数组；\n"
        "- 有歧义：返回 1~3 个消歧后的搜索变体（每个变体保留原词并追加限定词，"
        "如 trellis → [\"trellis AI 编码助手\", \"trellis 3D 生成\"]）。\n"
        "只输出 JSON 数组，不要任何解释。"
    )
    try:
        raw = call_llm(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=None,
            json_mode=True,
        )
        variants = json.loads(extract_json_block(raw))
        if isinstance(variants, list):
            seen, out = set(), []
            for v in variants:
                v = str(v).strip()
                if v and v not in seen:
                    seen.add(v)
                    out.append(v)
            if out:
                return out[:max_variants]
    except Exception as e:
        print(f"[SearchQueryOptimize] 优化 '{topic}' 失败，回退原词: {e}", file=sys.stderr)
    return [topic]


def fetch_dynamic_search_news(topics: list, limit_per_topic: int = 3, engine: str = "anysearch", optimize: bool = False) -> list:
    """动态搜索主入口。engine='anysearch' 走 AnySearch（默认），'bing' 走 Bing 登录态。

    optimize=True 时，每个主题先经 LLM 消歧（1~3 个变体）再逐变体搜索、去重合并；
    多变量时每变体降为 2 条收敛成本。optimize=False（默认）原词直搜。
    """
    all_items = []
    seen_urls = set()

    for topic in topics:
        if not topic.strip():
            continue
        if optimize:
            search_terms = optimize_search_query(topic)
            if len(search_terms) > 1:
                print(f"[DynamicSearch] 主题 '{topic}' 消歧为 {len(search_terms)} 个变体: {search_terms}")
        else:
            search_terms = [topic]

        for term in search_terms:
            per_term_limit = limit_per_topic if len(search_terms) == 1 else 2
            print(f"[DynamicSearch] 正在全网检索主题: '{term}' (engine={engine})")
            try:
                if engine == "bing":
                    results = fetch_search_results_bing(term, limit=per_term_limit)
                else:
                    results = fetch_search_results_anysearch(term, limit=per_term_limit)
                    if not results:
                        print(f"[DynamicSearch] AnySearch 对 '{term}' 无结果，回退 Bing 登录态")
                        results = fetch_search_results_bing(term, limit=per_term_limit)
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
                print(f"[DynamicSearch Error] 主题 '{term}' 检索失败: {e}", file=sys.stderr)

    return all_items
