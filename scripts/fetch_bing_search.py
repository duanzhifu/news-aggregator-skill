import sys
import json
import urllib.parse
from pathlib import Path

skill_root = Path(__file__).resolve().parent.parent
if str(skill_root) not in sys.path:
    sys.path.insert(0, str(skill_root))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("[Error] Playwright 未安装", file=sys.stderr)
    sys.exit(1)


def fetch_search_results_bing(keyword: str, limit: int = 5) -> list:
    encoded_kw = urllib.parse.quote(keyword)
    search_url = f"https://www.bing.com/search?q={encoded_kw}"
    results = []

    print(f"[BingSearch] 正在检索关键词: '{keyword}' -> URL: {search_url}")

    with sync_playwright() as p:
        browser = None
        for channel in ['msedge', 'chrome', None]:
            try:
                launch_args = {"headless": True}
                if channel:
                    launch_args["channel"] = channel
                browser = p.chromium.launch(**launch_args)
                break
            except Exception:
                continue

        if not browser:
            browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()

        try:
            page.goto(search_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_selector("li.b_algo", timeout=10000)
            page.wait_for_timeout(2500)

            items = page.locator("li.b_algo").all()
            for item in items[:limit]:
                try:
                    title_el = item.locator("h2 a").first
                    if title_el.count() == 0:
                        continue
                    title = title_el.inner_text().strip()
                    url = title_el.get_attribute("href")

                    snippet_el = item.locator(".b_caption p, .b_snippet").first
                    snippet = snippet_el.inner_text().strip() if snippet_el.count() > 0 else ""

                    time_el = item.locator(".b_factrow, .c_t, span._date").first
                    pub_time = time_el.inner_text().strip() if time_el.count() > 0 else "未知"

                    if title and url:
                        results.append({
                            "title": title,
                            "url": url,
                            "summary": snippet,
                            "time": pub_time,
                            "source": "bing_search",
                        })
                except Exception:
                    continue
        except Exception as e:
            print(f"[BingSearch Error] 抓取 Bing 失败: {e}", file=sys.stderr)
        finally:
            browser.close()

    return results


if __name__ == "__main__":
    test_keyword = sys.argv[1] if len(sys.argv) > 1 else "Agent"
    res = fetch_search_results_bing(test_keyword, limit=3)
    print(json.dumps(res, ensure_ascii=False, indent=2))
