"""视频源正文提取（4.8：字幕一级）。

统一入口：fetch_video_transcript(url)

优先级（第 1 级）：
  1. bilibili：用浏览器 Profile 的登录 cookie（Playwright 复用 D:\\news-aggregator-browser-profile）
     调 /x/web-interface/view 拿 cid → /x/player/wbi/v2 拿 AI 字幕列表 → 抓 ai-zh 字幕 → 拼全文。
  2. youtube_tech：暂退化——RSS 描述 + 视频页 DOM 简介（见 fetch_news._fetch_url_evidence_with_method）。

为什么只做"字幕"一级、不做语音转录：
  - bilibili / youtube 技术类视频 80%+ 有官方/AI 字幕，走 API 拿现成字幕比语音识别又快又准。
  - 语音转录（whisper / yt-dlp 音频）是第 2 级兜底，成本高、需额外 key/模型，本次不落地。

失败兜底：任何一步失败（无 cookie / 无字幕 / 网络 / 解析）→ 返回空字符串，
由下游 fetch_news 回退到"视频页 DOM 抓简介"，绝不中断整个拉取管线。
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

# bilibili Web 常量
_BILI_PROFILE = r"D:\news-aggregator-browser-profile"
_BILI_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_BILI_COOKIE_HEADER = None
_BILI_COOKIE_TIME = 0.0
_BILI_COOKIE_TTL = 30 * 60  # 30 分钟内复用同一 cookie，之后重读（登录态可能过期/刷新）
_BILI_COOKIE_LOCK = None  # 延迟初始化，避免跨平台 pickle 问题

# 视频站 host（仅 bilibili 走字幕；youtube 暂退化）
_VIDEO_HOSTS = {
    "bilibili.com", "www.bilibili.com", "m.bilibili.com",
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
}


def is_video_site(url: str) -> bool:
    """判断 URL 是否为常见视频站（bilibili / youtube）。"""
    if not url:
        return False
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return False
    return host in _VIDEO_HOSTS


def _extract_bvid(url: str) -> str:
    """从 bilibili 视频 URL 提取 bvid，无则返回空串。

    仅对 bilibili 域名生效（防御：避免误把其它站 /video/ 当 bvid）。
    """
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        if not host.endswith("bilibili.com"):
            return ""
        parsed = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parsed.query)
        bvid = q.get("bvid", [None])[0]
        if bvid:
            return bvid
        m = re.search(r"/video/(BV\w+)", parsed.path)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _get_cookie_lock():
    global _BILI_COOKIE_LOCK
    if _BILI_COOKIE_LOCK is None:
        import threading
        _BILI_COOKIE_LOCK = threading.Lock()
    return _BILI_COOKIE_LOCK


def _load_bilibili_cookie() -> str:
    """用 Playwright 复用浏览器 Profile 读 bilibili 登录 cookie，返回 Cookie 头字符串。

    读不到/未登录 → 返回空串（调用方据此降级到 DOM 抓简介）。
    这是走登录态拿字幕的唯一入口——匿名访问字幕接口返回空列表（实测）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ""
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                _BILI_PROFILE,
                channel="msedge",
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                cookies = ctx.cookies(["https://www.bilibili.com", "https://api.bilibili.com"])
            finally:
                ctx.close()
    except Exception as e:
        print(f"[视频转录] 读取 B 站登录态失败，降级：{e}", file=sys.stderr)
        return ""
    bili = {c["name"]: c["value"] for c in cookies
            if c["name"] in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5")}
    if "SESSDATA" not in bili:
        return ""
    return "; ".join(f"{k}={v}" for k, v in bili.items())


def _get_bilibili_cookie() -> str:
    """带 TTL 缓存的 cookie 获取。同一进程内 30 分钟复用，避免每篇重复启动 Playwright。"""
    global _BILI_COOKIE_HEADER, _BILI_COOKIE_TIME
    now = time.time()
    with _get_cookie_lock():
        if _BILI_COOKIE_HEADER and (now - _BILI_COOKIE_TIME) < _BILI_COOKIE_TTL:
            return _BILI_COOKIE_HEADER
        header = _load_bilibili_cookie()
        if header:
            _BILI_COOKIE_HEADER = header
            _BILI_COOKIE_TIME = now
        else:
            # 加载失败：清掉旧值，避免复用过期缓存
            _BILI_COOKIE_HEADER = None
            _BILI_COOKIE_TIME = 0.0
        return header


def _api_get(url: str, cookie_header: str, referer: str):
    """GET 一个 bilibili JSON 接口。"""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _BILI_UA, "Cookie": cookie_header, "Referer": referer},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_bilibili_transcript(bvid: str) -> str:
    """拿一条 bilibili 视频的中文 AI 字幕全文。

    流程：view API(拿 cid) → wbi/v2 拿字幕列表 → ai-zh 字幕 URL → 字幕 JSON body → 拼文本。
    """
    cookie = _get_bilibili_cookie()
    if not cookie:
        return ""
    try:
        view = _api_get(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            cookie, f"https://www.bilibili.com/video/{bvid}",
        )
        if view.get("code") != 0:
            return ""
        cid = view["data"]["cid"]
        sub = _api_get(
            f"https://api.bilibili.com/x/player/wbi/v2?bvid={bvid}&cid={cid}",
            cookie, f"https://www.bilibili.com/video/{bvid}",
        )
    except Exception as e:
        print(f"[视频转录] B 站 API 失败({bvid})：{e}", file=sys.stderr)
        return ""

    subs = (sub.get("data") or {}).get("subtitle", {}).get("subtitles", [])
    if not subs:
        return ""
    # 优先中文 AI 字幕，否则取第一条
    target = next((s for s in subs if s.get("lan") == "ai-zh"), subs[0])
    sub_url = target.get("subtitle_url", "")
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url
    if not sub_url:
        return ""
    try:
        body = _api_get(sub_url, cookie, f"https://www.bilibili.com/video/{bvid}")
    except Exception as e:
        print(f"[视频转录] B 站字幕内容获取失败({bvid})：{e}", file=sys.stderr)
        return ""

    parts = []
    for x in body.get("body", []):
        c = x.get("content")
        if not c:
            continue
        c = str(c).strip()
        if c:
            parts.append(c)
    return "\n".join(parts)


def fetch_video_transcript(url: str, source_hint: str = None) -> str:
    """统一入口：从视频 URL 提取 transcript 文本。

    仅 bilibili 走字幕 API；youtube 暂退化（返回空串，走 DOM 抓简介兜底）。
    失败/无字幕/不支持 → 返回空串（绝不抛异常）。
    """
    if not url:
        return ""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.endswith("bilibili.com"):
        bvid = _extract_bvid(url)
        if not bvid:
            return ""
        return _fetch_bilibili_transcript(bvid)
    # youtube.com / youtu.be：暂不接（数据中心 IP 被反爬），退化到 DOM 简介
    return ""
