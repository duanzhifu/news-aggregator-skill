import sys
import json
from pathlib import Path

skill_root = Path(__file__).resolve().parent.parent
if str(skill_root) not in sys.path:
    sys.path.insert(0, str(skill_root))

from scripts.fetch_bing_search import fetch_search_results_bing
from scripts.domain_mapper import clean_and_map_url, is_site_homepage

def fetch_dynamic_search_news(topics: list, limit_per_topic: int = 3) -> list:
    all_items = []
    seen_urls = set()

    for topic in topics:
        if not topic.strip():
            continue
        print(f"[DynamicSearch] 正在全网检索主题: '{topic}'")
        try:
            results = fetch_search_results_bing(topic, limit=limit_per_topic)
            for r in results:
                bing_url = r.get("url")
                if not bing_url:
                    continue
                
                real_url, source_key, source_cn = clean_and_map_url(bing_url)
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
                    "topic_matched": topic
                }
                all_items.append(item)
        except Exception as e:
            print(f"[DynamicSearch Error] 主题 '{topic}' 检索失败: {e}", file=sys.stderr)

    return all_items

if __name__ == "__main__":
    test_topics = ["Agent", "大模型"]
    items = fetch_dynamic_search_news(test_topics, limit_per_topic=2)
    print(json.dumps(items, ensure_ascii=False, indent=2))
