import sys
import json
import subprocess
import os

def fetch_dynamic_source(source_name: str, keyword: str = None, limit: int = 5) -> list:
    """
    混合捞鱼策略：针对前沿硬核源或反爬严格的动态网站（如 B站搜索、特定 Substack 或动态博客），
    自动调用底层 Playwright 脚本（如 fetch_social_browser.py 或 fetch_bensbites.py）进行动态抓取。
    """
    results = []
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    if source_name.lower() in ("bilibili", "bili", "douyin"):
        script_path = os.path.join(skill_root, "scripts", "fetch_social_browser.py")
        search_kw = keyword.split(',')[0].strip() if keyword else "人工智能"
        target_url = f"https://search.bilibili.com/all?keyword={search_kw}" if source_name.lower() != "douyin" else f"https://www.douyin.com/search/{search_kw}"
        
        cmd = ["py", script_path, target_url, "--limit", str(limit)]
        try:
            print(f"[混合捞鱼] 正在通过 Playwright 动态抓取 {source_name} (关键词: {search_kw})...", file=sys.stderr)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=skill_root)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, list):
                    for item in data:
                        results.append({
                            "source": f"Dynamic-{source_name.capitalize()}",
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "time": item.get("time", "Recent"),
                            "summary": item.get("summary", "动态浏览器采集内容"),
                            "fetch_method": "agent_browser_dynamic"
                        })
        except Exception as e:
            print(f"[Warning] 动态捞鱼 {source_name} 抓取异常: {e}", file=sys.stderr)
            
    elif source_name.lower() == "bensbites":
        script_path = os.path.join(skill_root, "scripts", "fetch_bensbites.py")
        cmd = ["py", script_path]
        try:
            print(f"[混合捞鱼] 正在通过 Playwright 动态抓取 Ben's Bites...", file=sys.stderr)
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=skill_root)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                if isinstance(data, list):
                    for item in data:
                        results.append({
                            "source": "Ben's Bites",
                            "title": item.get("title"),
                            "url": item.get("url"),
                            "time": item.get("time", "Recent"),
                            "summary": item.get("summary", "AI News & Tools"),
                            "fetch_method": "agent_browser_dynamic"
                        })
        except Exception as e:
            print(f"[Warning] Ben's Bites 动态捞鱼异常: {e}", file=sys.stderr)
            
    return results[:limit]
