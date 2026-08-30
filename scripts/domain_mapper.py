import sys
import re
import base64
from urllib.parse import urlparse, parse_qsl

def clean_and_map_url(bing_url: str) -> tuple:
    real_url = bing_url
    if "/ck/a?!" in bing_url:
        try:
            parsed_url = urlparse(bing_url)
            query_params = dict(parse_qsl(parsed_url.query))
            u_val = query_params.get("u", "")
            if u_val.startswith("a1"):
                encoded_str = u_val[2:]
                padding = 4 - (len(encoded_str) % 4)
                if padding < 4:
                    encoded_str += "=" * padding
                decoded_bytes = base64.b64decode(encoded_str.encode("utf-8"))
                real_url = decoded_bytes.decode("utf-8", errors="ignore")
        except Exception:
            pass

    parsed = urlparse(real_url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]

    domain_to_source = {
        "github.com": ("github", "GitHub 趋势榜"),
        "juejin.cn": ("juejin", "掘金热榜"),
        "dev.to": ("devto", "Dev.to"),
        "news.ycombinator.com": ("hackernews", "黑客新闻"),
        "zhihu.com": ("zhihu", "知乎"),
        "zhuanlan.zhihu.com": ("zhihu", "知乎专栏"),
        "sspai.com": ("sspai", "少数派"),
        "36kr.com": ("36kr", "36 氪"),
        "medium.com": ("medium", "Medium"),
        "runoob.com": ("runoob", "菜鸟教程"),
    }

    if domain in domain_to_source:
        source_key, source_cn = domain_to_source[domain]
    else:
        source_key = re.sub(r'[^a-zA-Z0-9_]', '_', domain)
        source_cn = domain

    return real_url, source_key, source_cn


_HOMEPAGE_NAMES = frozenset({'', '/', '/index', '/index.html', '/index.htm', '/home', '/home.html'})


def is_site_homepage(url: str) -> bool:
    """True for a site root / homepage, not an article or docs path."""
    parsed = urlparse(str(url or '').strip())
    if not parsed.netloc:
        return False
    path = (parsed.path or '/').rstrip('/') or '/'
    name = path.lower()
    if name in _HOMEPAGE_NAMES:
        return True
    # "/index" already covered; keep "/zh-cn" style locale homepages out of 4.3 for now.
    return False

if __name__ == "__main__":
    test_url = "https://www.bing.com/ck/a?!&&p=6feed642c9f9f1468469a9a8e91942c7d3c6db4355a7cf28fdf46b9f7dd9aa84JmltdHM9MTc4NzUyOTYwMA&ptn=3&ver=2&hsh=4&fclid=3fb911e4-a4d4-6c0e-3955-065ba5d16da0&u=a1aHR0cHM6Ly93d3cucnVub29iLmNvbS9haS1hZ2VudC9haS1hZ2VudC10dXRvcmlhbC5odG1s&ntb=1"
    real, key, cn = clean_and_map_url(test_url)
    print(f"Real URL: {real}")
    print(f"Source Key: {key}")
    print(f"Source CN: {cn}")
