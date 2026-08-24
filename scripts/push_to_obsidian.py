"""
把新闻抓取结果推送到本地 Obsidian Vault。

用法：
  py scripts/push_to_obsidian.py --vault "D:/Obsidian/自动信息获取"

环境变量：
  OBSIDIAN_VAULT_PATH：Obsidian Vault 根目录（优先）
  LLM_MODEL：可选，覆盖默认 LLM 模型

输出结构：
  <Vault>/
    自动获取信息/
      YYYY-MM-DD/                      # 按日期组织的子目录
        今日总结.md                    # 当日批次总结（按信源分段，每段含文章总结表格+全部文章表）
        信息源/                        # 当日抓取的信息源
          <来源中文名>/
            YYYY-MM-DD-<标题>.md      # 单篇文章
      拒绝集合/
        YYYY-MM-DD.md                  # 当天全部拒绝和待复核项目
        _拒绝索引.json                 # AI 前历史去重使用的结构化索引
"""
import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

TODAY = datetime.now().strftime('%Y-%m-%d')
NOW_DT = datetime.now()
UNKNOWN_PUBLISH_DATE = '未知'
UNKNOWN_PUBLISH_DATETIME = datetime.min
DEFAULT_SOURCE_KEYS = (
    'juejin', 'devto', 'github', 'openai',
    'bilibili',
)

# === 来源配置 ===
SOURCE_NAME_CN = {
    'huggingface': '抱抱脸每日论文',
    'arxiv': '学术 arXiv',
    'aihot': 'AIHOT 中文 AI 聚合',
    'tldr_ai': 'TLDR AI',
    'import_ai': 'Import AI',
    'ai_newsletters': 'AI Newsletter 聚合',
    'openai': 'OpenAI Blog',
    'anthropic': 'Anthropic Blog',
    'latentspace_ainews': 'Latent Space AI 每日新闻',
    'hackernews': '黑客新闻',
    'lobsters': '洛博斯特斯',
    'devto': 'Dev.to',
    'devto_react': 'Dev.to React',
    'v2ex': 'V2EX',
    'github': 'GitHub 趋势榜',
    'react_blog': 'React 官方博客',
    'juejin': '掘金热榜',
    'sspai': '少数派',
    'weibo': '微博热搜',
    'weibo_search': '微博关键词',
    'douyin': '抖音',
    'bilibili': 'Bilibili',
    'youtube_tech': 'YouTube 科技频道',
    'wechat': '微信公众号',
    '36kr': '36 氪',
    'tencent': '腾讯新闻',
    'wallstreetcn': '华尔街见闻',
    'producthunt': 'Product Hunt',
}

CATEGORY_MAP = {
    'huggingface': 'ai', 'arxiv': 'ai', 'aihot': 'ai', 'tldr_ai': 'ai',
    'import_ai': 'ai', 'ai_newsletters': 'ai', 'openai': 'ai', 'anthropic': 'ai',
    'latentspace_ainews': 'ai',
    'hackernews': 'programmer', 'lobsters': 'programmer', 'devto': 'programmer',
    'devto_react': 'programmer', 'v2ex': 'programmer',
    'github': 'github',
    'douyin': 'social', 'bilibili': 'social', 'youtube_tech': 'social',
    'weibo': 'social', 'weibo_search': 'social', 'wechat': 'social',
    'react_blog': 'frontend', 'juejin': 'frontend', 'sspai': 'frontend',
    '掘金热榜': 'frontend',  # 中文 key 直接匹配
    '少数派': 'frontend',
}

CATEGORY_CN = {
    'ai': 'AI',
    'programmer': '程序员社区',
    'github': 'GitHub',
    'frontend': '前端与中文科技',
    'social': '社交平台技术内容',
    'other': '其他',
}

RECOMMENDATION_LEVEL_CN = {
    'strongly_recommended': '强烈推荐',
    'optional': '可选阅读',
    'not_recommended': '不推荐',
    'evaluation_failed': '待复核',
}


def recommendation_level_label(level):
    """Return a reader-facing Chinese label while preserving internal level keys."""
    return RECOMMENDATION_LEVEL_CN.get(level, level or '待复核')

# 不同指标的中文标签
METRIC_LABELS = {
    'heat': '热度',
    'metric_likes': '点赞',
    'metric_like': '点赞',
    'metric_thumbsup': '点赞',
    'metric_upvotes': '点赞',
    'metric_upvote': '点赞',
    'metric_score': '分数',
    'metric_points': '分数',
    'metric_votes': '投票',
    'metric_stars': '收藏',
    'metric_bookmarks': '收藏',
    'metric_favorites': '收藏',
    'metric_saves': '收藏',
    'metric_views': '浏览量',
    'metric_hits': '浏览量',
    'metric_reads': '浏览量',
    'metric_downloads': '下载',
    'metric_reactions': '互动',
    'metric_shares': '分享',
    'metric_replies': '回复',
    'metric_responses': '回复',
    'metric_comments': '评论',
    'metric_claps': '鼓掌',
    'metric_hearts': '喜欢',
}

# 每个分类下优先展示的指标（按顺序）
PREFERRED_METRICS_BY_CATEGORY = {
    'github': ['stars', 'forks', 'language'],
    'ai': ['citations', 'github_stars'],
    'programmer': ['points', 'comments'],
    'frontend': ['views', 'likes', 'comments'],
}


def get_source_cn(source_key, original_source_name=''):
    key = source_key.lower().replace(' ', '').replace("'", '')
    if key in SOURCE_NAME_CN:
        return SOURCE_NAME_CN[key]
    if original_source_name:
        return original_source_name
    return source_key


SOURCE_KEY_ALIAS = {
    'githubtrending': 'github',
    'hackernews': 'hackernews',
    'hacker news': 'hackernews',
    'reuters (google news fallback)': 'reuters',
    'dev.to': 'devto',
}


def get_category(source_key):
    key = source_key.lower().replace(' ', '').replace("'", '')
    if key in SOURCE_KEY_ALIAS:
        key = SOURCE_KEY_ALIAS[key]
    return CATEGORY_MAP.get(key, 'other')


def sanitize_filename(text, max_len=80):
    """生成安全文件名：先解码 HTML 实体与 URL 编码，再移除 Windows 非法字符。

    方案 B：同时处理 HTML 实体（如 &quot; → "）和 URL 编码（如 %20 → 空格）。
    """
    if not text:
        return 'untitled'
    text = str(text).strip()
    # 1. 解码 HTML 实体
    text = html.unescape(text)
    # 2. 解码 URL 编码
    text = unquote(text)
    # 3. 移除 Windows 文件名非法字符
    text = re.sub(r'[\\/:*?"<>|：]', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = text.strip(' .')
    if len(text) > max_len:
        text = text[:max_len]
    return text or 'untitled'


# 常见正文噪音模式（按来源/通用）
NOISE_PATTERNS = [
    # 中国网站备案号、站点标识
    r'京ICP备\d+号-\d+',
    r'沪ICP备\d+号-\d+',
    r'粤ICP备\d+号-\d+',
    r'ICP备案号[：:]\s*\S+',
    r'©\s*\d{4}\s*[\w\s]+(?:版权所有|All rights reserved)',
    # 导航按钮
    r'^\s*(?:返回|返回首页|首页|上一页|下一页|跳转到).*\s*$',
    r'^\s*(?:收起全文|展开全文|阅读全文|查看全文|点击阅读|了解更多|继续阅读)\s*$',
    r'^\s*(?:Read more|Continue reading|Back to top|Home|Next|Previous)\s*$',
    # GitHub 页面 UI 文案
    r'^\s*打开更多操作菜单\s*$',
    r'^\s*文件夹和文件\s*$',
    r'^\s*最近一次提交信息\s*$',
    r'^\s*贡献者\s*$',
    r'^\s*Commits\s*$',
    r'^\s*Pull requests\s*$',
    r'^\s*Issues\s*$',
    r'^\s*Insights\s*$',
    r'^\s*Actions\s*$',
    # 社交/互动占位
    r'^\s*(?:精选\s*\d+|点赞\s*\d+|收藏\s*\d+|评论\s*\d+|分享\s*\d+|转发\s*\d+)\s*$',
    r'^\s*(?:\d+\s*(?:赞|喜欢|收藏|评论|回复|分享|转发|浏览|阅读))\s*$',
    # 阅读原文 / via 标记（但保留 URL 本身之外的文字）
    r'🔗\s*阅读原文\s*via\s*\S+\s*(?:https?://\S+)?',
    r'\[阅读原文\]\([^)]*\)',
    r'原文地址[：:]\s*\S+',
    # 空占位段落
    r'^\s*(?:登录|注册|搜索|订阅|关注|收藏本站)\s*$',
    # GitHub 登录/会话提示
    r'^\s*你已在另一个标签页.*\s*$',
    r'^\s*你已切换账户.*\s*$',
    r'^\s*以刷新会话.*\s*$',
    r'^\s*加载时出错.*\s*$',
    r'^\s*加载时发生错误。?.*\s*$',
    r'^\s*请重新加载此页面。?\s*$',
    r'^\s*你必须登录才能.*\s*$',
    # GitHub 仓库元数据 / UI 文案
    r'^\s*最近一次提交(?:信息|日期).*\s*$',
    r'^\s*[\d,]+\s*次提交.*\s*$',
    r'^\s*贡献者\s*$',
    r'^\s*Commits\s*$',
    r'^\s*Pull requests\s*$',
    r'^\s*Issues\s*$',
    r'^\s*Insights\s*$',
    r'^\s*Actions\s*$',
    # 孤立的星标/热度数字（如 "18.9k"、"1,237"）
    r'^\s*[\d,.]+[km]?\s*$',
    # 常见目录/文件列表占位（GitHub 文件树）
    r'^\s*\.[a-z]+/\s*$',
    r'^\s*\.github\s*$',
    r'^\s*\.devcontainer\s*$',
    r'^\s*\.vscode\s*$',
    r'^\s*\.cursor\s*$',
    r'^\s*\.codex\s*$',
    r'^\s*\.claude\s*$',
    r'^\s*\.agents?\s*$',
    # 学术/arXiv 页面 UI 文案
    r'^\s*arXiv:\d+\.\d+v\d+\s*\([^)]*\)\s*$',
    r'^\s*arXiv:\d+\.\d+v\d+\s*$',
    r'^\s*\[\s*Submitted on.*\]\s*$',
    r'^\s*\[\s*提交于.*\]\s*$',
    r'^\s*(?:Computer Science|计算机科学)\s*>\s*\S.*$',
    r'^\s*(?:Skip to main content|跳转至主要内容)\s*$',
    r'^\s*(?:Search arXiv|搜索 arXiv)\s*$',
    r'^\s*(?:Press Enter to search|按 Enter 键搜索).*\s*$',
    r'^\s*(?:View PDF|查看 PDF)\s*$',
    r'^\s*(?:HTML \(experimental\)|HTML（实验性）)\s*$',
    r'^\s*(?:View a PDF of the paper titled|查看论文).*PDF.*\s*$',
    r'^\s*(?:Title:|标题[：:])\s*$',
    r'^\s*(?:Authors:|作者[：:])\s*$',
    r'^\s*(?:Abstract:|摘要[：:])\s*$',
    r'^\s*(?:Comments:|评论[：:]|Comments)\s*$',
    r'^\s*(?:Subjects:|主题[：:]|Subjects)\s*$',
    r'^\s*(?:Project Page:|Code:|项目页面[：:]|代码[：:])\s*$',
    r'^\s*this https? URL\s*$',
    # arXiv 尾部 / 元数据 / 引用 / 导航
    r'^\s*(?:项目主页|项目页面|引用方式|引用格式|全文链接|访问论文|当前浏览上下文|更改浏览分类|切换浏览分类|参考文献与引用|数据提供方|书目与引用工具|书目与引文工具|书目浏览器|关联论文)\s*[：:]?\s*$',
    r'^\s*此处为(?:\s+https?)?\s+URL\s*$',
    r'^\s*arXiv[：:]\d+\.\d+(?:v\d+)?\s*(?:\[[^\]]+\]|（[^）]+）)?\s*$',
    r'^\s*\[cs\.[A-Z]{2}\]\s*$',
    r'^\s*（cs\.[A-Z]{2}）\s*$',
    r'^\s*https://doi\.org/\S+\s*$',
    r'^\s*(?:arXiv\s+通过\s+DataCite|由\s+DataCite\s+提供的\s+arXiv\s+DOI).*$',
    r'^\s*来自：.*\[\s*$',
    r'^\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日.*UTC.*KB.*$',
    r'^\s*(?:NASA ADS|Google Scholar|Semantic Scholar|TeX 源码|查看许可证)\s*$',
    r'^\s*导出?\s*BibTeX\s*(?:格式)?(?:引用)?\s*$',
    r'^\s*加载中…+\s*$',
    r'^\s*<\s*上一篇\s*$',
    r'^\s*下一篇\s*>\s*$',
    r'^\s*\(\s*什么是[^）]+？\s*\)\s*$',
    r'^\s*cs\.[A-Z]{2}\s*$',
    r'^\s*；\s*人工智能\s*（cs\.AI）\s*$',
    r'^\s*\d{4}-\d{2}\s*$',
    # arXiv 中文翻译后的尾部变体
    r'^\s*项目页面\s*[：:]?\s*$',
    r'^\s*(?:此|this) URL\s*$',
    r'^\s*引用(?:格式|方式)\s*[：:]?\s*$',
    r'^\s*TeX 源文件\s*$',
    r'^\s*(?:正在加载|加载中)\.\.\.|(?:正在加载|加载中)…+\s*$',
    r'^\s*切换浏览(?:条件|方式|分类)\s*[：:]?\s*$',
    r'^\s*Connected Papers(?: 开关| 切换)?\s*$',
    r'^\s*查看(?:电子邮箱|电子邮件|许可协议)\s*$',
    r'^\s*（(?:或|就?)使用本版本[的:：]?.*$',
    r'^\s*（或者对于此版本引用[：:]?.*$',
    r'^\s*（对于此版本，可引用为.*$',
    r'^\s*查看由.*PDF\s*$',
    r'^\s*(?:关注|聚焦)以了解更多信息\s*$',
    r'^\s*关联论文(?: 开关| 切换)?\s*$',
    r'^\s*书目浏览器(?: 开关| 切换)?\s*$',
    r'^\s*数据提供方\s*[：:]?\s*$',
    r'^\s*此 https? URL\s*$',
    r'^\s*(?:导出\s*)?BibTeX\s*(?:格式)?\s*(?:引用)?\s*$',
    r'^\s*（什么是[^）]+(?:\s+[^）]+)?）\s*$',
    r'^\s*（什么是[^）]*$',
    r'^\s*计算与语言\s*（cs\.CL）\s*$',
    r'^\s*\[cs\.[A-Z]{2}\]\s*）?\s*$',
    r'^\s*提交者：.*\[\s*$',
    r'^\s*，用于此版本）\s*$',
    r'^\s*进一步了解\s*$',
    r'^\s*arXiv\s+经\s+DataCite\s+发布的\s+DOI.*$',
    r'^\s*（探索器是什么？）\s*$',
    r'^\s*（关联论文是什么？）\s*$',
]


def clean_summary(text):
    """Remove links and read-more prompts from summary text."""
    if not text:
        return text
    text = html.unescape(str(text)).strip()

    # Aggregator footers, including multi-word source names and optional separators.
    text = re.sub(
        r'\s*(?:🔗\s*)?阅读原文\s+via\s+.*$',
        '',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Calls to action whose only purpose is to send the reader to a link.
    cta = r'(?:查看全文|阅读全文|阅读原文|点击阅读|了解更多|继续阅读|展开全文|Read more|Continue reading)'
    text = re.sub(
        rf'\s*(?:🔗\s*)?[（(]?\s*{cta}\s*[）)]?\s*(?:[：:·•-]\s*)?(?:https?://\S+)?',
        '',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'\s*阅读我们[^。！？\r\n]*(?:https?://\S+)?',
        '',
        text,
        flags=re.IGNORECASE,
    )

    # A summary is plain prose; links already live in note properties.
    text = re.sub(r'!?\[[^\]]*\]\([^)]*\)', '', text)
    text = re.sub(r'<https?://[^>]+>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'https?://\S+', '', text, flags=re.IGNORECASE)

    # 去掉 "..." 省略号（RSS 截断标记）
    text = re.sub(r'\s*\.{2,}\s*$', '', text)
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+([，。！？；：,.!?;:])', r'\1', text)
    text = re.sub(r'\s*[：:·•-]+\s*$', '', text)
    return text.strip()


def get_article_summary(item):
    """Return the best available summary after removing feed noise."""
    for field in ('summary_zh', 'summary'):
        summary = clean_summary(item.get(field, ''))
        if summary:
            return summary

    content = item.get('content', '') or ''
    return clean_summary(content[:300])


def clean_markdown_cell(value, max_length=None):
    """Normalize untrusted text for a single Markdown table cell."""
    text = html.unescape(str(value or ''))
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('|', '/')
    if max_length and len(text) > max_length:
        text = text[:max_length - 1].rstrip() + '…'
    return text or '-'


def encode_markdown_path(path):
    """Encode a relative path while retaining path separators and dot prefixes."""
    return quote(str(path).replace('\\', '/'), safe='./-_%')


def markdown_table(headers, rows):
    """Render a table and reject malformed rows instead of emitting broken Markdown."""
    width = len(headers)
    if not width:
        raise ValueError('Markdown 表格至少需要一列')
    normalized_rows = []
    for index, row in enumerate(rows, start=1):
        if len(row) != width:
            raise ValueError(f'Markdown 表格第 {index} 行列数错误：期望 {width}，实际 {len(row)}')
        normalized_rows.append([clean_markdown_cell(cell) for cell in row])
    lines = [
        '| ' + ' | '.join(clean_markdown_cell(cell) for cell in headers) + ' |',
        '|' + '|'.join([' --- '] * width) + '|',
    ]
    lines.extend('| ' + ' | '.join(row) + ' |' for row in normalized_rows)
    return lines


def clean_body_paragraphs(paragraphs):
    """清洗正文段落：过滤掉导航、备案号、社交占位等噪音段落。

    方案 A：在 push_to_obsidian.py 中统一清洗，所有来源共享规则。
    如果清洗后正文为空，返回空列表，由 build_article_markdown 生成异常提示。
    """
    if not paragraphs:
        return []
    cleaned = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        # 命中任意噪音正则则跳过
        if any(re.search(pat, p, re.IGNORECASE) for pat in NOISE_PATTERNS):
            continue
        # 过滤过短且无实质意义的行（少于 5 个字符）
        if len(p) < 5:
            continue
        cleaned.append(p)
    return cleaned


def dedupe_items(items):
    seen = set()
    result = []
    for item in items:
        url = canonical_item_url(item.get('url', ''))
        if url:
            key = ('url', item.get('source', ''), url)
        else:
            title = re.sub(r'\s+', ' ', str(item.get('title', '')).strip()).casefold()
            key = ('title', item.get('source', ''), title)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def canonical_item_url(url):
    """Normalize stable URL parts so URL identity wins over title similarity."""
    try:
        parsed = urlsplit(str(url).strip())
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            return ''
        netloc = parsed.netloc.casefold()
        path = parsed.path.rstrip('/') or '/'
        tracking_keys = {'fbclid', 'gclid', 'msclkid', 'spm', 'ref', 'ref_src'}
        query = urlencode([
            (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith('utm_') and key.casefold() not in tracking_keys
        ])
        return urlunsplit((parsed.scheme.casefold(), netloc, path, query, ''))
    except ValueError:
        return ''


def rejection_identity(item):
    """Return a stable identity shared by rejection storage and pre-AI dedupe."""
    source_value = str(item.get('source_key') or item.get('source') or '').strip().casefold()
    source_value = source_value.replace(' ', '').replace("'", '')
    source = SOURCE_KEY_ALIAS.get(source_value, source_value)
    for source_key, source_name in SOURCE_NAME_CN.items():
        normalized_name = str(source_name).casefold().replace(' ', '').replace("'", '')
        if source == normalized_name:
            source = source_key
            break
    url = canonical_item_url(item.get('url', ''))
    if url:
        raw = f'url\n{source}\n{url}'
    else:
        title = re.sub(r'\s+', ' ', str(item.get('title', '')).strip()).casefold()
        raw = f'title\n{source}\n{title}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]


def is_definitive_rejection(item):
    return (
        item.get('recommendation_level') == 'not_recommended'
        and normalized_rejection_kind(item) == 'definitive'
    )


def normalized_rejection_kind(item):
    """Fail open for temporary evidence problems so they can be evaluated again."""
    kind = item.get('rejection_kind')
    if kind in {'definitive', 'transient'}:
        return kind
    if item.get('recommendation_level') == 'evaluation_failed':
        return 'transient'
    reason = ' '.join(str(item.get(key, '')) for key in (
        'recommendation_reason', 'selection_reason', 'evaluation_error', 'evidence_status'
    )).casefold()
    transient_markers = (
        '乱码', '无法解码', '证据不足', '快照不足', '正文缺失', '标题缺失',
        '超时', '抓取失败', '调用失败', '处理失败', 'unreadable', 'timeout', 'failed',
    )
    if item.get('evidence_quality') == 'insufficient' or any(marker in reason for marker in transient_markers):
        return 'transient'
    return 'definitive'


def rejection_stage(item):
    return '打开前拒绝' if 'ai_selected' in item and item.get('ai_selected') is False else '打开后拒绝'


def rejection_evidence_points(item):
    values = item.get('evidence_points') or []
    if isinstance(values, str):
        values = [values]
    points = [str(value).strip() for value in values if str(value).strip()]
    if not points:
        reason = item.get('recommendation_reason') or item.get('selection_reason')
        if reason:
            points = [str(reason).strip()]
    return points[:3]


def parse_publish_datetime(item):
    candidates = [
        item.get('published_at'),
        item.get('time_ms'),
        item.get('created_at_i'),
        item.get('published'),
        item.get('pub_time'),
        item.get('publish_time'),
        item.get('created_at'),
        item.get('time'),
    ]
    for val in candidates:
        if not val:
            continue
        if isinstance(val, (int, float)):
            try:
                ts = val / 1000 if val > 1e11 else val
                dt = datetime.fromtimestamp(ts)
                return dt.strftime('%Y-%m-%d'), dt
            except Exception:
                continue
        if isinstance(val, str):
            if re.fullmatch(r'\d+(?:\.\d+)?', val.strip()):
                try:
                    ts = float(val.strip())
                    ts = ts / 1000 if ts > 1e11 else ts
                    dt = datetime.fromtimestamp(ts)
                    return dt.strftime('%Y-%m-%d'), dt
                except Exception:
                    pass
            # 尝试解析相对时间 "X hours ago", "X minutes ago" 等
            rel = re.match(r'(\d+)\s*(hour|minute|day|week|month)s?\s*ago', val, re.IGNORECASE)
            if rel:
                num = int(rel.group(1))
                unit = rel.group(2).lower()
                if unit.startswith('minute'):
                    dt = NOW_DT - timedelta(minutes=num)
                elif unit.startswith('hour'):
                    dt = NOW_DT - timedelta(hours=num)
                elif unit.startswith('day'):
                    dt = NOW_DT - timedelta(days=num)
                elif unit.startswith('week'):
                    dt = NOW_DT - timedelta(weeks=num)
                elif unit.startswith('month'):
                    dt = NOW_DT - timedelta(days=num * 30)
                return dt.strftime('%Y-%m-%d'), dt
            # "Today" / "Yesterday"
            if val.lower() in {'today', 'real-time', 'hot', 'updated recently', 'updated recently.'}:
                continue
            if val.lower() == 'yesterday':
                dt = NOW_DT - timedelta(days=1)
                return dt.strftime('%Y-%m-%d'), dt
            # 尝试 RFC 2822
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(val)
                return dt.strftime('%Y-%m-%d'), dt.replace(tzinfo=None) if dt.tzinfo else dt
            except Exception:
                pass
            try:
                cleaned = val.replace('Z', '+00:00')
                dt = datetime.fromisoformat(cleaned)
                dt_naive = dt.replace(tzinfo=None) if dt.tzinfo else dt
                return dt_naive.strftime('%Y-%m-%d'), dt_naive
            except Exception:
                pass
            m = re.search(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})', val)
            if m:
                try:
                    dt = datetime(*[int(g) for g in m.groups()])
                    return dt.strftime('%Y-%m-%d'), dt
                except Exception:
                    pass
            m2 = re.search(r'(\d{4})-(\d{2})-(\d{2})', val)
            if m2:
                try:
                    dt = datetime(*[int(g) for g in m2.groups()])
                    return dt.strftime('%Y-%m-%d'), dt
                except Exception:
                    pass
    return UNKNOWN_PUBLISH_DATE, UNKNOWN_PUBLISH_DATETIME


def format_publish_datetime(pub_dt):
    """Format a publication time without inventing a fetch timestamp."""
    if pub_dt == UNKNOWN_PUBLISH_DATETIME:
        return '发布时间未知'
    return pub_dt.strftime('%Y-%m-%d %H:%M')


# 不同来源 heat 字符串的语义映射（source_key → (热度提取正则, 对应指标标签)）
HEAT_METRIC_HINTS = {
    'github': (r'([\d,.]+[km]?)\s*(?:stars?)', '星标'),
    'hackernews': (r'([\d,.]+[km]?)\s*(?:points?)', '分数'),
    'lobsters': (r'([\d,.]+[km]?)\s*(?:points?)', '分数'),
    'devto': (r'([\d,.]+[km]?)\s*(?:reactions?)', '互动'),
    'v2ex': (r'([\d,.]+[km]?)\s*(?:replies?)', '回复'),
    'juejin': (r'([\d,.]+[km]?)', '热榜排名'),
    'weibo': (r'([\d,.]+[km]?)', '热度'),
    'producthunt': (r'([\d,.]+[km]?)', '热度'),
}


def parse_heat(heat):
    """从 heat 字符串中提取数值，支持 k/m 后缀和千分位逗号。

    示例：
      "18,837 stars" → 18837
      "256 points" → 256
      "2.5k likes" → 2500
      10 → 10
    """
    if isinstance(heat, (int, float)):
        return int(heat)
    if not heat:
        return 0
    s = str(heat).lower().replace(',', '')
    # 优先取第一个数字（含 k/m 后缀）
    m = re.search(r'([\d.]+[km]?)', s)
    if not m:
        return 0
    num_str = m.group(1)
    try:
        if 'k' in num_str:
            return int(float(num_str.replace('k', '')) * 1000)
        if 'm' in num_str:
            return int(float(num_str.replace('m', '')) * 1000000)
        return int(float(num_str))
    except Exception:
        return 0


def parse_metric_value(v):
    """把 metric 值统一转为整数（支持 k/m 后缀和千分位逗号）。"""
    if v is None or v == '' or v == 0:
        return None
    s = str(v).lower().replace(',', '')
    m = re.search(r'([\d.]+[km]?)', s)
    if not m:
        return str(v).strip() or None
    num_str = m.group(1)
    try:
        if 'k' in num_str:
            return str(int(float(num_str.replace('k', '')) * 1000))
        if 'm' in num_str:
            return str(int(float(num_str.replace('m', '')) * 1000000))
        return str(int(float(num_str)))
    except Exception:
        return str(v).strip() or None


# 已知来源的指标字段映射（fetcher 直接放到 item 里的非 metric_ 字段）
KNOWN_METRIC_FIELDS = {
    'hot_rank': '热榜排名',
    'view': '浏览量',
    'like': '点赞',
    'comment': '评论',
    'collect': '收藏',
    'share': '分享',
    'points': '分数',
    'comments': '评论',
    'stars': '星标',
    'forks': '复刻',
    'downloads': '下载',
}


def extract_metrics(item):
    """从 item 中提取所有指标字段及其数值。返回 dict[label] = int_str（不含热度）。"""
    metrics = {}
    # 已知字段（非 metric_ 前缀）
    for raw_key, cn_label in KNOWN_METRIC_FIELDS.items():
        v = item.get(raw_key)
        parsed = parse_metric_value(v)
        if parsed:
            metrics[cn_label] = parsed

    # 新加的 metric_* 字段
    for k, v in item.items():
        if not k.startswith('metric_'):
            continue
        raw_key = k[len('metric_'):]
        cn_label = METRIC_LABELS.get(k, raw_key)
        parsed = parse_metric_value(v)
        if parsed:
            metrics[cn_label] = parsed

    # 方案 C：根据来源和 heat 字符串补全核心指标
    source_key = item.get('source_key', item.get('source', '')).lower().replace(' ', '').replace("'", '')
    if source_key in SOURCE_KEY_ALIAS:
        source_key = SOURCE_KEY_ALIAS[source_key]
    heat = item.get('heat', '')
    if heat and source_key in HEAT_METRIC_HINTS:
        pattern, cn_label = HEAT_METRIC_HINTS[source_key]
        m = re.search(pattern, str(heat).lower().replace(',', ''))
        if m:
            parsed = parse_metric_value(m.group(1))
            if parsed and cn_label not in metrics:
                metrics[cn_label] = parsed

    return metrics


def yaml_escape(s):
    return str(s).replace('\\', '\\\\').replace('"', '\\"')


def is_publishable(item):
    return item.get('recommendation_level', 'evaluation_failed') in {
        'strongly_recommended', 'optional'
    }


def write_json_report(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def write_recommendation_audit(items, report_date=None):
    """Append rejected items to a local audit log without article bodies."""
    rejected = [item for item in items if item.get('recommendation_level') == 'not_recommended']
    if not rejected:
        return None
    audit_dir = Path('reports') / (report_date or TODAY)
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / 'recommendation_audit.jsonl'
    with audit_path.open('w', encoding='utf-8') as handle:
        for item in rejected:
            handle.write(json.dumps({
                'title': item.get('title', ''),
                'source': item.get('source', ''),
                'url': item.get('url', ''),
                'quality_score': item.get('quality_score'),
                'engagement_metrics': item.get('engagement_metrics', {}),
                'engagement_score': item.get('engagement_score'),
                'recommendation_level': item.get('recommendation_level'),
                'recommendation_reason': item.get('recommendation_reason', ''),
                'selection_reason': item.get('selection_reason', ''),
                'rejection_kind': item.get('rejection_kind', ''),
                'evidence_quality': item.get('evidence_quality', ''),
                'evidence_points': rejection_evidence_points(item),
                'published_at': item.get('published_at', ''),
                'time_kind': item.get('time_kind', 'unknown'),
                'time_confidence': item.get('time_confidence', 'unknown'),
                'time_evidence': item.get('time_evidence', ''),
            }, ensure_ascii=False) + '\n')
    return audit_path


def _load_rejection_payload(collection_dir):
    index_path = Path(collection_dir) / '_拒绝索引.json'
    if not index_path.exists():
        return {'version': 1, 'records': {}, 'daily_records': {}}
    try:
        payload = json.loads(index_path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            raise ValueError('拒绝索引根节点不是对象')
        if not isinstance(payload.get('records'), dict):
            payload['records'] = {}
        if not isinstance(payload.get('daily_records'), dict):
            payload['daily_records'] = {}
        return payload
    except (OSError, ValueError, json.JSONDecodeError):
        return {'version': 1, 'records': {}, 'daily_records': {}}


def load_rejection_index(collection_dir):
    return _load_rejection_payload(collection_dir)['records']


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    temporary.replace(path)


def _rejection_record(item, report_date):
    reason = item.get('recommendation_reason') or item.get('selection_reason') or item.get('evaluation_error') or '未提供原因'
    return {
        'id': rejection_identity(item),
        'title': item.get('title_zh') or item.get('title', ''),
        'original_title': item.get('title', ''),
        'source': item.get('source', ''),
        'source_key': item.get('source_key', ''),
        'url': item.get('url', ''),
        'stage': rejection_stage(item),
        'rejection_kind': normalized_rejection_kind(item),
        'reason': str(reason).strip(),
        'evidence_points': rejection_evidence_points(item),
        'evidence_quality': item.get('evidence_quality', 'insufficient'),
        'published_at': item.get('published_at', ''),
        'time_kind': item.get('time_kind', 'unknown'),
        'time_confidence': item.get('time_confidence', 'unknown'),
        'time_evidence': item.get('time_evidence', ''),
        'quality_score': item.get('quality_score'),
        'last_seen': report_date,
    }


def build_rejection_daily_markdown(records, report_date):
    definitive = [record for record in records if record['rejection_kind'] == 'definitive']
    transient = [record for record in records if record['rejection_kind'] != 'definitive']
    lines = [
        '---',
        f'日期: "{report_date}"',
        f'拒绝总数: {len(records)}',
        f'确定性拒绝: {len(definitive)}',
        f'暂不推荐: {len(transient)}',
        f'标签: ["AI筛选审计", "拒绝集合", "抓取日期-{report_date}"]',
        '---', '', f'# {report_date} AI 筛选审计', '',
        '> 确定性拒绝会参与后续历史去重；暂不推荐项会在后续抓取中保留重新评估机会。', '',
    ]
    for heading, group in (('确定性拒绝', definitive), ('暂不推荐与待复核', transient)):
        lines.extend([f'## {heading}（{len(group)}）', ''])
        rows = []
        for record in group:
            title = clean_markdown_cell(record['title']) or '（无标题）'
            if record['url']:
                title = f'[{title}]({record["url"]})'
            evidence = '；'.join(record['evidence_points']) or '-'
            rows.append([
                title,
                record['source'] or '-',
                record['stage'],
                clean_markdown_cell(record['reason'], 240),
                clean_markdown_cell(evidence, 300),
                record['time_evidence'] or record['published_at'] or '未知',
            ])
        lines.extend(markdown_table(['文章', '来源', '阶段', 'AI 判断', '具体证据', '时间依据'], rows))
        lines.append('')
    return '\n'.join(lines)


def write_rejection_collection(collection_dir, items, report_date=None):
    """Write all current rejects for review and persist definitive rejects for dedupe."""
    report_date = report_date or TODAY
    collection_dir = Path(collection_dir)
    rejected = [item for item in items if item.get('recommendation_level') in {'not_recommended', 'evaluation_failed'}]
    current_records = [_rejection_record(item, report_date) for item in rejected]
    payload = _load_rejection_payload(collection_dir)
    index_records = payload['records']
    daily_by_id = {
        record['id']: record
        for record in payload['daily_records'].get(report_date, [])
        if isinstance(record, dict) and record.get('id')
    }
    for record in current_records:
        daily_by_id[record['id']] = record
        if record['rejection_kind'] != 'definitive':
            continue
        previous = index_records.get(record['id'], {})
        record['first_seen'] = previous.get('first_seen', report_date)
        index_records[record['id']] = record

    daily_records = list(daily_by_id.values())
    payload['version'] = 1
    payload['updated_at'] = datetime.now().isoformat(timespec='seconds')
    payload['records'] = index_records
    payload['daily_records'][report_date] = daily_records
    _atomic_write_text(collection_dir / '_拒绝索引.json', json.dumps(payload, ensure_ascii=False, indent=2))
    _atomic_write_text(collection_dir / f'{report_date}.md', build_rejection_daily_markdown(daily_records, report_date))
    return collection_dir / f'{report_date}.md', len(daily_records), len(index_records)


def write_evaluation_failure_audit(items, report_date=None):
    """Record items that could not be evaluated without storing their bodies."""
    failed = [item for item in items if item.get('recommendation_level') == 'evaluation_failed']
    if not failed:
        return None
    audit_dir = Path('reports') / (report_date or TODAY)
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / 'evaluation_failure_audit.jsonl'
    with audit_path.open('w', encoding='utf-8') as handle:
        for item in failed:
            handle.write(json.dumps({
                'title': item.get('title', ''),
                'source': item.get('source', ''),
                'url': item.get('url', ''),
                'evaluation_status': item.get('evaluation_status', ''),
                'evaluation_error': item.get('evaluation_error', ''),
            }, ensure_ascii=False) + '\n')
    return audit_path


def build_article_markdown(item, source_cn, source_summary):
    title_orig = item.get('title', '')
    title_zh = item.get('title_zh', title_orig)
    source_orig = item.get('source', 'Unknown')
    source_key = item.get('source_key', source_orig)
    category = get_category(source_key)
    url = item.get('url', '')
    summary = get_article_summary(item)
    original_content = item.get('content', '') or ''
    original_paragraphs = clean_body_paragraphs(original_content.splitlines())
    heat = parse_heat(item.get('heat', 0))
    pub_date, pub_dt = parse_publish_datetime(item)
    pub_time_full = format_publish_datetime(pub_dt)
    metrics = extract_metrics(item)

    tags_list = ['新闻', category, f'来源-{source_cn}', f'抓取日期-{TODAY}']

    # 中文 frontmatter
    yaml_lines = ['---']
    yaml_lines.append(f'原文标题: "{yaml_escape(title_orig)}"')
    yaml_lines.append(f'中文标题: "{yaml_escape(title_zh)}"')
    yaml_lines.append(f'来源: "{yaml_escape(source_cn)}"')
    yaml_lines.append(f'来源key: "{yaml_escape(source_key)}"')
    yaml_lines.append(f'分类: "{yaml_escape(CATEGORY_CN.get(category, category))}"')
    yaml_lines.append(f'链接: "{yaml_escape(url)}"')
    yaml_lines.append(f'热度: {heat}')
    yaml_lines.append(f'发布时间: "{pub_time_full}"')
    if item.get('time_kind'):
        yaml_lines.append(f'时间类型: "{yaml_escape(item["time_kind"])}"')
    if item.get('time_confidence'):
        yaml_lines.append(f'时间置信度: "{yaml_escape(item["time_confidence"])}"')
    if item.get('time_evidence'):
        yaml_lines.append(f'时间证据: "{yaml_escape(item["time_evidence"])}"')
    yaml_lines.append(f'抓取日期: "{TODAY}"')
    if item.get('content_fetch_status'):
        yaml_lines.append(f'正文抓取状态: "{yaml_escape(item["content_fetch_status"])}"')
        yaml_lines.append(f'正文长度: {item.get("content_length", len(original_content))}')
    if item.get('content_fetch_method'):
        yaml_lines.append(f'正文抓取方式: "{yaml_escape(item["content_fetch_method"])}"')
    if item.get('evidence_status'):
        yaml_lines.append(f'快照状态: "{yaml_escape(item["evidence_status"])}"')
        yaml_lines.append(f'快照长度: {item.get("evidence_length", 0)}')
    if item.get('evidence_method'):
        yaml_lines.append(f'快照方式: "{yaml_escape(item["evidence_method"])}"')
    if item.get('translation_status'):
        yaml_lines.append(f'翻译状态: "{yaml_escape(item["translation_status"])}"')
    if item.get('recommendation_level'):
        yaml_lines.append(f'推荐等级: "{yaml_escape(recommendation_level_label(item["recommendation_level"]))}"')
    if item.get('quality_score') is not None:
        yaml_lines.append(f'质量分数: {item["quality_score"]}')
    if item.get('evaluation_status'):
        yaml_lines.append(f'评估状态: "{yaml_escape(item["evaluation_status"])}"')
    if item.get('engagement_score') is not None:
        yaml_lines.append(f'互动辅助分: {item["engagement_score"]}')
    yaml_lines.append(f'标签: {json.dumps(tags_list, ensure_ascii=False)}')
    if metrics:
        yaml_lines.append('指标:')
        for label, val in metrics.items():
            yaml_lines.append(f'  {label}: {val}')
    yaml_lines.append('---')
    yaml_block = '\n'.join(yaml_lines)

    body = (
        f"## 阅读建议\n\n"
        f"- 推荐等级：{recommendation_level_label(item.get('recommendation_level'))}\n"
        f"- 质量分数：{item.get('quality_score', 0)}\n"
        f"- 推荐理由：{item.get('recommendation_reason', '内容质量评估未完成，已按失败回退继续推送。')}\n"
        f"\n"
        f"## 总结\n\n"
        f"{summary or '（暂无总结）'}\n"
    )
    if original_paragraphs:
        body += "\n## 原文正文\n\n" + "\n\n".join(original_paragraphs) + "\n"
    elif item.get('evidence_status'):
        points = rejection_evidence_points(item)
        body += "\n## 判断依据\n\n"
        if points:
            body += ''.join(f'- {point}\n' for point in points)
        else:
            body += '- 当前判断基于页面文本快照和信息源元数据。\n'
        if item.get('time_evidence'):
            body += f'- 时间依据：{item["time_evidence"]}\n'
        body += f'- 证据质量：{item.get("evidence_quality", "partial")}\n'
    return yaml_block + '\n\n' + body, pub_date, metrics


def split_paragraphs(text):
    """把长文本按段落切分。

    规则：
    - 短行（<80 字符）通常是标题、列表项、字段名，单独成段
    - 长行（>=80 字符）是连续正文，合并为段落
    - 空行作为分段边界
    """
    if not text:
        return []
    lines = [ln.strip() for ln in text.split('\n') if ln.strip()]
    result = []
    buf = ''
    for ln in lines:
        ln = ln.replace('\r', '')
        if len(ln) < 80:
            # 短行单独成段，先把累积的长文本 buffer 输出
            if buf:
                result.append(buf.strip())
                buf = ''
            result.append(ln)
        else:
            # 长行合并
            if buf:
                buf = (buf + ' ' + ln).strip()
            else:
                buf = ln
    if buf:
        result.append(buf.strip())
    return result


def build_article_filename(item, pub_date):
    title_zh = item.get('title_zh', item.get('title', ''))
    title = sanitize_filename(title_zh, max_len=60)
    return f"{pub_date}-{title}.md"


def collect_metric_columns(items_list):
    """收集所有出现过的指标列名（中文），按出现频率排序。"""
    counter = {}
    for item in items_list:
        m = extract_metrics(item)
        for k in m:
            counter[k] = counter.get(k, 0) + 1
    # 优先的指标排序（下载量优先，不要热度）
    priority = ['下载', '分数', '点赞', '评论', '回复', '收藏', '浏览量', '分享', '鼓掌', '喜欢', '互动', '投票']
    def sort_key(label):
        try:
            return (0, priority.index(label))
        except ValueError:
            return (1, label)
    return [k for k, _ in sorted(counter.items(), key=lambda x: sort_key(x[0]))]


def build_source_index_markdown(source_cn, category, items, source_summary, items_by_date=None):
    """生成来源索引页（无 H1 标题，直接进表格，含跳转链接、文章总结、指标）。"""
    lines = [
        '---',
        f'来源: "{source_cn}"',
        f'分类: "{CATEGORY_CN.get(category, category)}"',
        f'抓取日期: "{TODAY}"',
        f'文章数: {len(items)}',
        f'标签: ["信息源", "{category}", "来源-{source_cn}"]',
        '---',
        '',
        '## 最近文章（按发布时间倒序）',
        '',
    ]

    sorted_items = sorted(items, key=lambda x: parse_publish_datetime(x)[1], reverse=True)
    metric_cols = collect_metric_columns(sorted_items)
    base_cols = ['发布时间', '中文标题', '文章总结']
    extra_cols = [c for c in metric_cols if c not in base_cols]
    headers = base_cols + extra_cols
    lines.append('| ' + ' | '.join(headers) + ' |')
    lines.append('|' + '|'.join([' --- '] * len(headers)) + '|')

    for item in sorted_items:
        title_zh = item.get('title_zh', item.get('title', ''))
        title_orig = item.get('title', '')
        _, pub_dt = parse_publish_datetime(item)
        pub_time_full = format_publish_datetime(pub_dt)
        pub_date, _ = parse_publish_datetime(item)
        filename = build_article_filename(item, pub_date)
        # 索引页和文章在同一目录，用相对路径 ./文件名
        rel_path = f"./{filename}".replace(' ', '%20')
        summary_zh = get_article_summary(item)
        # 清理"查看全文"等噪音
        summary_clean = clean_summary(summary_zh)
        # 关键：去掉所有可能破坏表格的字符
        summary_clean = summary_clean.replace('|', '/').replace('\n', ' ').replace('\r', ' ')
        # 显示用的标题也做同样清理（避免破坏表格）
        display_title = (title_zh or title_orig).replace('|', '/').replace('\n', ' ').replace('\r', ' ')
        # 文件路径里的空格需要编码，管道符不允许
        safe_rel_path = rel_path.replace('|', '/').replace(' ', '%20')
        metrics = extract_metrics(item)
        # 用标准 Markdown 链接 [文字](路径)，避免 wiki 链接的 | 破坏表格
        row = [
            pub_time_full,
            f"[{display_title}]({safe_rel_path})",
            summary_clean or '-',
        ]
        for c in extra_cols:
            row.append(metrics.get(c, '-'))
        lines.append('| ' + ' | '.join(row) + ' |')
    lines.append('')
    return '\n'.join(lines)


def build_daily_summary_markdown(source_summaries_cn, items_by_source_cn, report_date=None):
    """生成每日批次总结页（按信源分段）。"""
    report_date = report_date or TODAY
    total = sum(len(v) for v in items_by_source_cn.values())

    lines = [
        '---',
        f'日期: "{report_date}"',
        f'文章总数: {total}',
        f'来源数: {len(items_by_source_cn)}',
        f'来源列表: {json.dumps(list(items_by_source_cn.keys()), ensure_ascii=False)}',
        f'标签: ["今日总结", "新闻", "抓取日期-{report_date}"]',
        '---',
        '',
        f'# {report_date} 今日总结',
        '',
        f'> 共抓取 {len(items_by_source_cn)} 个来源，{total} 篇文章。',
        '',
    ]

    # === 按信源分段 ===
    lines.append('## 总体概览（按信源）')
    lines.append('')
    for src_cn, src_items in items_by_source_cn.items():
        lines.append(f'### {src_cn}（{len(src_items)} 条）')
        lines.append('')
        summary_value = source_summaries_cn.get(src_cn, '')
        if isinstance(summary_value, list):
            summary_text = '\n'.join(
                str(part).strip() for part in summary_value if str(part).strip()
            )
        elif summary_value is None:
            summary_text = ''
        else:
            summary_text = str(summary_value).strip()
        if summary_text:
            lines.append(summary_text)
            lines.append('')
        # 把原来索引.md里的表格：发布时间 | 中文标题 | 文章总结 | [指标列] 放到这里
        sorted_src = sorted(src_items, key=lambda x: parse_publish_datetime(x)[1], reverse=True)
        src_metric_cols = collect_metric_columns(sorted_src)
        src_base_cols = ['是否阅读', '发布时间', '中文标题', '推荐等级', '文章总结']
        src_extra_cols = [c for c in src_metric_cols if c not in src_base_cols]
        src_headers = src_base_cols + src_extra_cols
        src_rows = []
        for item in sorted_src:
            title_zh = item.get('title_zh', item.get('title', ''))
            title_orig = item.get('title', '')
            _, pub_dt = parse_publish_datetime(item)
            pub_time_full = format_publish_datetime(pub_dt)
            pub_date, _ = parse_publish_datetime(item)
            filename = build_article_filename(item, pub_date)
            rel_path = encode_markdown_path(f"./信息源/{src_cn}/{filename}")
            summary_zh = get_article_summary(item)
            summary_clean = clean_summary(summary_zh)
            summary_clean = clean_markdown_cell(summary_clean)
            display_title = clean_markdown_cell(title_zh or title_orig)
            metrics = extract_metrics(item)
            row = [
                '[ ]',
                pub_time_full,
                f"[{display_title}]({rel_path})",
                recommendation_level_label(item.get('recommendation_level')),
                summary_clean or '-',
            ]
            for c in src_extra_cols:
                row.append(metrics.get(c, '-'))
            src_rows.append(row)
        lines.extend(markdown_table(src_headers, src_rows))
        lines.append('')

    return '\n'.join(lines)


def fetch_news(sources, limit, deep=False, preserve_raw_time=False):
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    stats_path = Path('reports') / TODAY / 'fetch_stats.json'
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, 'scripts/fetch_news.py',
           '--source', ','.join(sources),
           '--limit', str(limit), '--no-save', '--stats-file', str(stats_path)]
    if deep:
        cmd.append('--deep')
    if preserve_raw_time:
        cmd.append('--skip-time-filter')
    result = subprocess.run(cmd, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', env=env)
    if result.returncode != 0:
        print(f"抓取失败: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        items = json.loads(result.stdout)
        try:
            stats = json.loads(stats_path.read_text(encoding='utf-8'))
            print_source_fetch_stats(stats)
        except (OSError, ValueError) as error:
            print(f"抓取统计读取失败: {error}", file=sys.stderr)
        return items
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败: {e}", file=sys.stderr)
        print(f"原始输出前200字符: {result.stdout[:200]}", file=sys.stderr)
        sys.exit(1)


def print_source_fetch_stats(payload):
    """Print source-level counts so an empty final result is diagnosable."""
    print("\n来源抓取统计：")
    for source, stats in payload.get('fetchers', {}).items():
        errors = len(stats.get('errors', []))
        print(
            f"  {source}: 请求 {stats.get('requested', 0)}，返回 {stats.get('returned', 0)}，"
            f"过滤统计 {stats.get('filtered', {})}，错误 {errors}"
        )
    if payload.get('time_filter_skipped'):
        print("时间过滤统计：已跳过程序硬过滤，原始时间交由 AI 判断")
        return
    print("时间过滤统计：")
    for source, stats in payload.get('time_filter', {}).items():
        print(
            f"  {source}: 收到 {stats.get('received', 0)}，时间缺失 {stats.get('time_missing', 0)}，"
            f"超出窗口 {stats.get('too_old', 0)}，保留 {stats.get('kept', 0)}"
        )


def select_for_ai_fetch(news_items, topics=None, recency_days=7):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from scripts.llm_summarize import select_candidates
    except ModuleNotFoundError:
        from llm_summarize import select_candidates
    return select_candidates(news_items, topics=topics, recency_days=recency_days)


def process_ai_snapshots(items, batch_tag, topics=None, recency_days=7):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from scripts.llm_summarize import process_selected_snapshots
    except ModuleNotFoundError:
        from llm_summarize import process_selected_snapshots
    return process_selected_snapshots(items, batch_tag, topics=topics, recency_days=recency_days)


def fallback_summary(news_items, batch_tag, error):
    """Keep raw items publishable when the LLM pipeline fails unexpectedly."""
    fallback_items = []
    error_text = str(error)[:500]
    for item in news_items:
        enriched = dict(item)
        enriched.update({
            'title_zh': item.get('title', ''),
            'summary_zh': item.get('summary', '') or item.get('description', ''),
            'translation_status': 'failed',
            'translation_error': error_text,
            'recommendation_level': 'evaluation_failed',
            'evaluation_status': 'failed',
            'evaluation_error': error_text,
            'recommendation_reason': 'LLM 流程失败，保留原始内容供人工复核。',
        })
        fallback_items.append(enriched)
    return {
        'batch_summary': '',
        'source_summaries': {},
        'items': fallback_items,
    }


def fetch_candidate_content(items):
    """Fetch full text for every candidate when deep mode is enabled."""
    if not items:
        return []
    for item in items:
        if not item.get('url', '').startswith('http'):
            item['content_fetch_status'] = 'unavailable'
            item['content_fetch_method'] = 'none'
            item['content_length'] = 0
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from scripts.fetch_news import enrich_items_with_content
    except ModuleNotFoundError:
        from fetch_news import enrich_items_with_content
    with_url = [item for item in items if item.get('url', '').startswith('http')]
    if with_url:
        enrich_items_with_content(with_url)
    for item in items:
        content = item.get('content', '') or ''
        if content:
            # The final editorial pipeline reads evidence_snapshot in both modes.
            item['evidence_snapshot'] = content
            item['evidence_status'] = item.get('content_fetch_status', 'fetched')
            item['evidence_method'] = f"full_{item.get('content_fetch_method', 'text')}"
            item['evidence_length'] = len(content)
    return items


def fetch_candidate_evidence(items):
    """Fetch complete DOM text only for candidates selected by AI."""
    if not items:
        return []
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from scripts.fetch_news import enrich_items_with_evidence
    except ModuleNotFoundError:
        from fetch_news import enrich_items_with_evidence
    return enrich_items_with_evidence(items)


def parse_frontmatter(text):
    """简单解析 YAML frontmatter，返回 dict。"""
    if not text.startswith('---'):
        return {}
    end = text.find('---', 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    data = {}
    current_key = None
    for line in fm_text.split('\n'):
        line = line.rstrip()
        if not line:
            continue
        # 简单键值对
        m = re.match(r'^(\S+?):\s*(.*)$', line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            # 去掉引号
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1]
            if v.startswith('[') and v.endswith(']'):
                try:
                    v = json.loads(v.replace("'", '"'))
                except Exception:
                    pass
            data[k] = v
            current_key = k
        elif line.startswith('  ') and current_key:
            # 嵌套列表/对象（简单处理）
            line = line.strip()
            m2 = re.match(r'^(\S+?):\s*(.*)$', line)
            if m2:
                data[f"{current_key}.{m2.group(1)}"] = m2.group(2).strip().strip('"').strip("'")
    return data


def load_existing_articles(root):
    """
    扫描 Vault 中已有的文章，按 (原文标题, 来源) 去重。
    支持两种结构：
    - 新结构：root/YYYY-MM-DD/信息源/<src>/*.md
    - 旧结构：root/信息源/<src>/*.md（向后兼容）
    返回 dict: {(原文标题, 来源): 文件路径}
    """
    existing = {}

    # 扫描所有可能的文章位置
    scan_roots = []

    # 1. 新结构：每个日期目录下的信息源
    if root.exists():
        for date_dir in root.iterdir():
            if date_dir.is_dir() and re.match(r'^\d{4}-\d{2}-\d{2}$', date_dir.name):
                sources_root = date_dir / '信息源'
                if sources_root.exists():
                    scan_roots.append(sources_root)

    # 2. 旧结构：根目录下的信息源（向后兼容）
    old_sources_root = root / '信息源'
    if old_sources_root.exists():
        scan_roots.append(old_sources_root)

    for sources_root in scan_roots:
        for md_path in sources_root.rglob('*.md'):
            if md_path.name == '索引.md':
                continue
            try:
                text = md_path.read_text(encoding='utf-8')
                fm = parse_frontmatter(text)
                orig_title = fm.get('原文标题', '')
                src = fm.get('来源', '')
                if orig_title and src:
                    existing[(orig_title, src)] = str(md_path)
            except Exception:
                continue
    return existing


def load_articles_for_date(date_dir):
    """读取当天已写入的文章，供增量运行时重新生成完整日报。"""
    sources_root = date_dir / '信息源'
    if not sources_root.exists():
        return []

    articles = []
    for md_path in sources_root.rglob('*.md'):
        if md_path.name == '索引.md':
            continue
        try:
            text = md_path.read_text(encoding='utf-8')
            fm = parse_frontmatter(text)
            title = fm.get('原文标题', '')
            source = fm.get('来源', '')
            if not title or not source:
                continue

            summary = ''
            summary_match = re.search(
                r'##\s+总结\s*\n\s*(.*?)(?=\n##\s+|\Z)',
                text,
                re.DOTALL,
            )
            if summary_match:
                summary = summary_match.group(1).strip()
            item = {
                'source': source,
                'source_key': fm.get('来源key', source),
                'title': title,
                'title_zh': fm.get('中文标题', title),
                'url': fm.get('链接', ''),
                'summary_zh': clean_summary(summary),
                'published': fm.get('发布时间', date_dir.name),
                'fetch_date': fm.get('抓取日期', date_dir.name),
                'recommendation_level': fm.get('推荐等级', ''),
            }
            for key, value in fm.items():
                if key.startswith('指标.'):
                    item[key.split('.', 1)[1]] = value
            articles.append(item)
        except Exception as exc:
            print(f"跳过无法读取的文章 {md_path}: {exc}", file=sys.stderr)

    return dedupe_items(articles)


def group_items_by_source(items):
    by_source_cn = {}
    source_cn_to_orig = {}
    for item in items:
        src_orig = item.get('source', 'Unknown')
        key_candidate = src_orig.lower().replace(' ', '').replace("'", '')
        src_cn = get_source_cn(key_candidate, src_orig)
        item['source_key'] = item.get('source_key', key_candidate)
        by_source_cn.setdefault(src_cn, []).append(item)
        source_cn_to_orig[src_cn] = src_orig
    return by_source_cn, source_cn_to_orig


def summarize_daily(items):
    """只重新生成汇总，文章本身已经在当天目录中持久化。"""
    if not items:
        return {}
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        from scripts.llm_summarize import generate_source_summaries
    except ModuleNotFoundError:
        from llm_summarize import generate_source_summaries

    source_summaries = generate_source_summaries(items)
    return source_summaries


def push_to_obsidian(source_keys, vault_path, limit=15, deep=False, profile='tech', topics=None,
                     recency_days=7, evidence_mode='snapshot'):
    batch_tag = f"{TODAY}_{profile}"
    vault = Path(vault_path)
    if not vault.exists():
        raise RuntimeError(f"Obsidian Vault 路径不存在: {vault}")

    root = vault / '自动获取信息'
    rejection_collection_dir = root / '拒绝集合'
    date_dir = root / TODAY  # 当日日期目录：自动获取信息/YYYY-MM-DD/
    summary_dir = date_dir   # 今日总结直接放在日期目录下
    sources_root = date_dir / '信息源'  # 信息源作为日期目录的子目录
    date_dir.mkdir(parents=True, exist_ok=True)
    sources_root.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"开始抓取：{', '.join(source_keys)}，每源 {limit} 条")
    if deep:
        evidence_mode = 'full'
    print(f"AI 抓取证据模式：{evidence_mode}")
    print(f"{'='*60}")

    # Preserve raw platform times so the AI can interpret their semantics before selection.
    pipeline_stats = {
        'requested_sources': source_keys,
        'limit_per_source': limit,
        'evidence_mode': evidence_mode,
    }
    news_items = fetch_news(source_keys, limit, deep=False, preserve_raw_time=True)
    pipeline_stats['fetched_count'] = len(news_items)
    news_items = dedupe_items(news_items)
    pipeline_stats['deduped_count'] = len(news_items)

    # Skip historical duplicates before any LLM or article-page request.
    existing_articles = load_existing_articles(root)
    rejected_history = load_rejection_index(rejection_collection_dir)
    fresh_items = []
    preexisting_skipped = 0
    rejected_history_skipped = 0
    for item in news_items:
        src_orig = item.get('source', 'Unknown')
        key_candidate = src_orig.lower().replace(' ', '').replace("'", '')
        src_cn = get_source_cn(key_candidate, src_orig)
        if (item.get('title', ''), src_cn) in existing_articles:
            preexisting_skipped += 1
            continue
        if rejection_identity(item) in rejected_history:
            rejected_history_skipped += 1
            continue
        fresh_items.append(item)
    news_items = fresh_items
    pipeline_stats['existing_skipped_before_ai'] = preexisting_skipped
    pipeline_stats['rejected_history_skipped_before_ai'] = rejected_history_skipped
    write_json_report(Path('reports') / TODAY / 'fetched_items.json', news_items)
    print(
        f"抓取完成：共 {len(news_items)} 条新候选；AI 前跳过已发布 {preexisting_skipped} 条，"
        f"跳过确定性拒绝 {rejected_history_skipped} 条"
    )

    if not news_items:
        print("没有抓取到新数据，将使用当天已有文章重建总结。")
        summarized = {'items': [], 'source_summaries': {}, 'batch_summary': ''}
        rejected_selection = []
    else:
        print("\n开始由 AI 判断时间语义、内容价值和是否值得打开页面...")
        try:
            selection = select_for_ai_fetch(news_items, topics=topics, recency_days=recency_days)
            candidates = [item for item in selection if item.get('ai_selected') is True]
            rejected_selection = []
            for item in selection:
                if item.get('ai_selected') is True:
                    continue
                rejected = dict(item)
                if item.get('selection_status') == 'failed':
                    rejected['recommendation_level'] = 'evaluation_failed'
                    rejected['rejection_kind'] = 'transient'
                    rejected['evidence_quality'] = 'insufficient'
                    rejected['evaluation_status'] = 'failed'
                    rejected['evaluation_error'] = item.get('selection_error', '')
                else:
                    rejected['recommendation_level'] = 'not_recommended'
                    rejected['quality_score'] = 0
                    rejected['recommendation_reason'] = item.get('selection_reason', '')
                    rejected['evidence_quality'] = item.get('evidence_quality', 'partial')
                rejected_selection.append(rejected)
            pipeline_stats['ai_selected_count'] = len(candidates)
            pipeline_stats['ai_rejected_before_open_count'] = len(rejected_selection)

            if evidence_mode == 'full':
                print(f"AI 已选择 {len(candidates)} 条，开始抓取入选项完整正文...")
                candidates = fetch_candidate_content(candidates)
            elif evidence_mode == 'snapshot':
                print(f"AI 已选择 {len(candidates)} 条，开始读取完整 DOM 正文证据...")
                candidates = fetch_candidate_evidence(candidates)
            else:
                for item in candidates:
                    item['evidence_status'] = 'metadata_only'
                    item['evidence_method'] = 'feed_metadata'
                    item['evidence_length'] = 0

            write_json_report(Path('reports') / TODAY / 'evidence_snapshots.json', candidates)
            print("开始基于全文分段证据完成翻译、总结、时间确认和最终质量判断...")
            summarized = process_ai_snapshots(
                candidates, batch_tag, topics=topics, recency_days=recency_days
            )
        except Exception as error:
            print(f"AI 抓取流程失败，候选将进入待复核审计：{error}", file=sys.stderr)
            rejected_selection = []
            summarized = fallback_summary(news_items, batch_tag, error)
    items = rejected_selection + summarized['items']
    pipeline_stats['evaluated_count'] = len(items)
    pipeline_stats['not_recommended_count'] = sum(
        1 for item in items if item.get('recommendation_level') == 'not_recommended'
    )
    pipeline_stats['evaluation_failed_count'] = sum(
        1 for item in items if item.get('recommendation_level') == 'evaluation_failed'
    )
    pipeline_stats['pre_open_rejected_count'] = sum(
        1 for item in rejected_selection if item.get('recommendation_level') == 'not_recommended'
    )
    pipeline_stats['post_open_rejected_count'] = sum(
        1 for item in summarized['items'] if item.get('recommendation_level') == 'not_recommended'
    )
    pipeline_stats['definitive_rejected_count'] = sum(is_definitive_rejection(item) for item in items)
    pipeline_stats['transient_rejected_count'] = sum(
        1 for item in items
        if item.get('recommendation_level') in {'not_recommended', 'evaluation_failed'}
        and not is_definitive_rejection(item)
    )
    audit_path = write_recommendation_audit(items)
    failure_audit_path = write_evaluation_failure_audit(items)
    rejection_page, rejection_page_count, rejection_index_count = write_rejection_collection(
        rejection_collection_dir, items
    )
    pipeline_stats['rejection_page_count'] = rejection_page_count
    pipeline_stats['rejection_index_count'] = rejection_index_count
    rejected_count = sum(1 for item in items if item.get('recommendation_level') == 'not_recommended')
    failed_count = sum(1 for item in items if item.get('recommendation_level') == 'evaluation_failed')
    items = [item for item in summarized['items'] if is_publishable(item)]
    pipeline_stats['publishable_count'] = len(items)
    if rejected_count:
        print(f"AI 已拒绝不推荐内容：{rejected_count} 篇，审计日志：{audit_path}")
    if failed_count:
        print(f"已标记评估失败内容：{failed_count} 篇，未写入 Vault；复核日志：{failure_audit_path}")
    if rejection_page_count:
        print(f"Obsidian 拒绝集合：{rejection_page}")
    source_summaries_orig = summarized['source_summaries']
    by_source_cn, source_cn_to_orig = group_items_by_source(items)

    source_summaries_cn = {}
    for src_cn, src_orig in source_cn_to_orig.items():
        source_summaries_cn[src_cn] = source_summaries_orig.get(src_orig, '')

    print(f"\n发现已有文章：{len(existing_articles)} 篇")

    print(f"\n生成文章笔记到：{sources_root}")
    article_count_per_source = {}
    skipped_count = 0
    write_failures = []
    for item in items:
        try:
            src_orig = item.get('source', 'Unknown')
            key_candidate = src_orig.lower().replace(' ', '').replace("'", '')
            src_cn = get_source_cn(key_candidate, src_orig)
            title_orig = item.get('title', '')

            # 跨日期去重：如果 Vault 中已有相同原文标题+来源，跳过
            dup_key = (title_orig, src_cn)
            if dup_key in existing_articles:
                print(f"  [跳过] [{src_cn}] {title_orig}（已存在：{existing_articles[dup_key]}）")
                skipped_count += 1
                continue

            pub_date, _ = parse_publish_datetime(item)
            source_dir = sources_root / src_cn
            source_dir.mkdir(parents=True, exist_ok=True)

            filename = build_article_filename(item, pub_date)
            filepath = source_dir / filename
            md, _, _ = build_article_markdown(
                item,
                src_cn,
                source_summaries_cn.get(src_cn, ''),
            )
            if filepath.exists():
                base = filepath.stem
                suffix = 2
                while (source_dir / f"{base}-{suffix}.md").exists():
                    suffix += 1
                filepath = source_dir / f"{base}-{suffix}.md"
            filepath.write_text(md, encoding='utf-8')
            article_count_per_source[src_cn] = article_count_per_source.get(src_cn, 0) + 1
            print(f"  [{src_cn}] {filepath.name}")
        except Exception as error:
            failure = {
                'title': item.get('title', ''),
                'source': item.get('source', ''),
                'error': str(error)[:500],
            }
            write_failures.append(failure)
            print(f"  [写入失败] [{failure['source']}] {failure['title']}: {failure['error']}", file=sys.stderr)

    # 文章写入后重新读取当天目录，确保日报包含之前单独抓取的来源。
    daily_items = load_articles_for_date(date_dir)
    daily_by_source_cn, daily_source_cn_to_orig = group_items_by_source(daily_items)
    daily_source_summaries_orig = summarize_daily(daily_items)
    daily_source_summaries_cn = {
        src_cn: daily_source_summaries_orig.get(src_orig, '')
        for src_cn, src_orig in daily_source_cn_to_orig.items()
    }

    daily_path = summary_dir / "今日总结.md"
    daily_md = build_daily_summary_markdown(
        daily_source_summaries_cn,
        daily_by_source_cn,
    )
    daily_path.write_text(daily_md, encoding='utf-8')
    print(f"\n已生成批次总结：{daily_path}")

    total = sum(article_count_per_source.values())
    print(f"\n完成！共 {total} 篇新文章，{len(by_source_cn)} 个来源。")
    if skipped_count:
        print(f"已跳过重复文章：{skipped_count} 篇")
    pipeline_stats['written_count'] = total
    pipeline_stats['existing_skipped_count'] = skipped_count
    pipeline_stats['write_failure_count'] = len(write_failures)
    write_json_report(Path('reports') / TODAY / 'write_failures.json', write_failures)
    pipeline_stats_path = Path('reports') / TODAY / 'pipeline_stats.json'
    write_json_report(pipeline_stats_path, pipeline_stats)
    print(f"抓取链路统计：{pipeline_stats_path}")
    print(f"Vault 根目录：{vault}")
    print(f"输出根目录：{root}")


def main():
    parser = argparse.ArgumentParser(description='推送新闻到 Obsidian')
    parser.add_argument('--source', default=','.join(DEFAULT_SOURCE_KEYS),
                        help='逗号分隔的源 key')
    parser.add_argument('--limit', type=int, default=15, help='每源抓取条数')
    parser.add_argument('--vault', help='Obsidian Vault 根目录')
    parser.add_argument('--deep', action='store_true',
                        help='兼容参数：仅对 AI 入选项拉取完整正文，等同 --evidence-mode full')
    parser.add_argument('--evidence-mode', choices=('metadata', 'snapshot', 'full'), default='snapshot',
                        help='AI 入选后的证据模式，默认 snapshot（完整 DOM 正文，分段审阅）')
    parser.add_argument('--profile', default='tech', help='批次标签')
    parser.add_argument('--topics', help='自定义推荐主题，逗号分隔')
    parser.add_argument('--recency-days', type=int, default=7, help='近多少天内容获得时效优先级')
    args = parser.parse_args()

    vault_path = args.vault or os.environ.get('OBSIDIAN_VAULT_PATH')
    if not vault_path:
        print("错误：请通过 --vault 参数或 OBSIDIAN_VAULT_PATH 环境变量指定 Obsidian Vault 根目录。",
              file=sys.stderr)
        sys.exit(1)

    sources = [s.strip() for s in args.source.split(',') if s.strip()]
    topics = [topic.strip() for topic in args.topics.split(',') if topic.strip()] if args.topics else None
    if args.recency_days < 0:
        parser.error('--recency-days 不能小于 0')
    push_to_obsidian(
        sources, vault_path, args.limit, args.deep, args.profile, topics,
        args.recency_days, args.evidence_mode,
    )


if __name__ == '__main__':
    main()
