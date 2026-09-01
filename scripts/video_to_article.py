# -*- coding: utf-8 -*-
"""视频 → 结构化中文文章（时间点 + 截图 + 思维导图 + 完整文稿），支持批量。

用法：
    py scripts\\video_to_article.py <视频URL | 本地视频文件 | 视频文件夹> [选项]

输入：
    - 本地视频文件：ffmpeg 抽音频 → Groq whisper verbose_json（带时间戳）
    - 视频 URL：bilibili 字幕分段 → Groq 兜底
    - 视频文件夹：遍历其中所有视频文件，串行逐个处理（单个失败不中断整批）

输出（方案 B：按专题分子目录，专题名默认取文件夹名）：
    <out>\\<专题>\\YYYY-MM-DD - 标题.md
    <out>\\<专题>\\附录\\YYYY-MM-DD - 标题截图\\mm-ss.jpg   ← 截图（每篇文章一个截图文件夹）

每篇笔记自包含：frontmatter + 摘要 + 大纲（开头）+ 分节正文（带时间点与截图）+ 要点
                 + 思维导图（Mermaid flowchart LR）+ 完整文稿（折叠 callout）

依赖：skill 本地 .env 的 GROQ_API_KEY（转写）与 LLM_*（写文章）；ffmpeg 用于抽音频与截图。
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

# 允许以 `py scripts\\video_to_article.py` 从 skill 根目录运行（scripts/ 已在 sys.path）
try:
    from groq_transcribe import transcribe_local_file, transcribe_local_file_segments, transcribe_via_groq, transcribe_via_groq_segments
    from video_transcribe import fetch_video_transcript, fetch_video_transcript_segments, is_video_site
    from llm_client import call_llm
except ModuleNotFoundError:
    from scripts.groq_transcribe import transcribe_local_file, transcribe_local_file_segments, transcribe_via_groq, transcribe_via_groq_segments
    from scripts.video_transcribe import fetch_video_transcript, fetch_video_transcript_segments, is_video_site
    from scripts.llm_client import call_llm

DEFAULT_OUT = r"D:\Obsidian\自动信息获取\视频整理"
_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".ts", ".webm", ".m4v"}
_CHUNK_CHARS = 8000
_SINGLE_CALL_CHARS = 14000

_WIN_ILLEGAL = re.compile(r'[\\/:*?"<>|\r\n\t]')
_SECTION_RE = re.compile(r'^##\s+(.+?)\s*（(\d{1,2}:\d{2}(?::\d{2})?)）\s*$')
_POINTS_HEAD_RE = re.compile(r'^##\s*要点\s*$')


def _sanitize_title(title):
    return _WIN_ILLEGAL.sub(" ", str(title)).strip()[:80]


def _yaml_str(value):
    """把任意字符串变成合法 YAML 双引号标量（JSON 转义，正确处理反斜杠/引号/换行）。"""
    return json.dumps(str(value), ensure_ascii=False)


def _fmt_ts(seconds):
    """秒 → mm:ss 或 h:mm:ss。"""
    seconds = int(round(seconds))
    if seconds < 0:
        seconds = 0
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _parse_ts_seconds(ts):
    """mm:ss 或 h:mm:ss → 秒。"""
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


def _segments_text(segments):
    """分段列表 → 纯文本。"""
    return "\n".join(s["text"] for s in segments)


def _segments_timestamped_text(segments):
    """分段列表 → 每行带 [mm:ss] 前缀的文本。"""
    return "\n".join(f"[{_fmt_ts(s['start'])}] {s['text']}" for s in segments)


def _split_chunks(text, size=_CHUNK_CHARS):
    """按字符数粗切分纯文本，尽量在段落边界断。"""
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            brk = text.rfind("\n", start, end)
            if brk > start + size // 2:
                end = brk
        chunks.append(text[start:end].strip())
        start = end
    return chunks


def _get_segments(target):
    """优先取带时间戳的分段，返回 (segments, method)；拿不到返回 ([], '')。"""
    if os.path.exists(target):
        segs = transcribe_local_file_segments(target)
        return (segs, "local_groq_transcript") if segs else ([], "")
    if is_video_site(target):
        segs = fetch_video_transcript_segments(target)
        if segs:
            return segs, "bilibili_subtitle"
    segs = transcribe_via_groq_segments(target)
    if segs:
        return segs, "groq_transcript"
    return [], ""


def _get_plain_transcript(target):
    """无时间戳的纯文本转录兜底，返回 (text, method)；拿不到返回 ('', '')。"""
    if os.path.exists(target):
        text = transcribe_local_file(target)
        return (text, "local_groq_transcript") if text else ("", "")
    if is_video_site(target):
        text = fetch_video_transcript(target)
        if text:
            return text, "bilibili_subtitle"
    text = transcribe_via_groq(target)
    if text:
        return text, "groq_transcript"
    return "", ""


def _build_article_prompt(timestamped_text, source_label, has_ts):
    ts_rule = (
        "5. 每个小节标题（## 一、... 等）末尾用括号标注该节内容的起始时间点，格式如（00:04）；"
        "「要点」列表每一项末尾也用括号标注起始时间点。时间点从对应文本行开头的 [mm:ss] 取，"
        "不得编造时间点。\n"
        "6. 直接输出 Markdown 正文，不要输出任何解释或前后缀。"
    ) if has_ts else (
        "5. 直接输出 Markdown 正文，不要输出任何解释或前后缀。"
    )
    return (
        "你是视频内容整理助手。根据下面这份视频转录文本（可能含口误、同音错字、口语化表达），"
        "整理成一篇结构化中文文章。要求：\n"
        "1. 忠实原文：只基于转录文本提炼，不得补充转录里没有的事实、数字、结论或因果。\n"
        "2. 修正明显口误与同音错字，但不得改变原意。\n"
        "3. 输出 Markdown，结构固定为：\n"
        "   # 标题\n"
        "   > 摘要：2~3 句概括全文。\n"
        "   ## 一、...（正文分节，小节标题自拟）\n"
        "   ...\n"
        "   ## 要点\n"
        "   - 要点一\n"
        "   - 要点二\n"
        "4. 标题用第一行 `# ` 后的文字，不要加书名号。\n"
        f"{ts_rule}\n\n"
        f"视频来源：{source_label}\n\n"
        f"转录文本：\n{timestamped_text}"
    )


def _build_merge_prompt(points, source_label, has_ts):
    joined = "\n\n".join(f"【第 {i+1} 段要点】\n{p}" for i, p in enumerate(points))
    ts_rule = (
        "4. 每个小节标题（## 一、... 等）与「要点」项末尾用括号标注起始时间点（如（00:04）），"
        "时间点从要点里已有的 [mm:ss] 取，不得编造。\n"
        "5. 直接输出 Markdown 正文，不要输出任何解释或前后缀。"
    ) if has_ts else (
        "4. 直接输出 Markdown 正文，不要输出任何解释或前后缀。"
    )
    return (
        "你是视频内容整理助手。下面是一段视频按分段提取的要点。请把它们合并整理成一篇结构化中文文章。要求：\n"
        "1. 只基于给定要点，不得补充要点里没有的事实、数字、结论或因果。\n"
        "2. 输出 Markdown，结构固定为：\n"
        "   # 标题\n"
        "   > 摘要：2~3 句概括全文。\n"
        "   ## 一、...（正文分节）\n"
        "   ## 要点\n"
        "   - 要点一\n"
        "3. 标题用第一行 `# ` 后的文字，不要加书名号。\n"
        f"{ts_rule}\n\n"
        f"视频来源：{source_label}\n\n{joined}"
    )


def _extract_points_prompt(chunk_text):
    return (
        "你是视频内容整理助手。下面是一段视频转录文本的片段。请提炼本片段的要点，"
        "忠实原文，不要补充转录里没有的内容。用无序列表输出要点；"
        "文本里每行开头的 [mm:ss] 是时间点，请在每个要点后标注它对应的 [mm:ss]。"
        "不要输出解释或前后缀。\n\n"
        f"转录片段：\n{chunk_text}"
    )


def _generate_article(timestamped_text, source_label, has_ts):
    """转录 → 文章。长转录先分段提要点再合并。"""
    if len(timestamped_text) <= _SINGLE_CALL_CHARS:
        return call_llm(
            [{"role": "user", "content": _build_article_prompt(timestamped_text, source_label, has_ts)}],
            temperature=0.3, max_tokens=8000,
        )
    points = []
    for chunk in _split_chunks(timestamped_text):
        p = call_llm(
            [{"role": "user", "content": _extract_points_prompt(chunk)}],
            temperature=0.2, max_tokens=2000,
        )
        points.append(p.strip())
    return call_llm(
        [{"role": "user", "content": _build_merge_prompt(points, source_label, has_ts)}],
        temperature=0.3, max_tokens=8000,
    )


def _extract_title(article):
    m = re.search(r"^\s*#\s+(.+)$", article, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return "未命名视频文章"


def _parse_article(article):
    """解析文章，返回 (标题, 小节列表, 要点列表)。小节=[{title, ts}]，要点=[str]。"""
    title = _extract_title(article)
    sections, points = [], []
    in_points = False
    for line in article.splitlines():
        stripped = line.strip()
        m = _SECTION_RE.match(stripped)
        if m:
            sections.append({"title": m.group(1).strip(), "ts": m.group(2)})
            in_points = False
            continue
        if _POINTS_HEAD_RE.match(stripped):
            in_points = True
            continue
        if stripped.startswith("## "):
            in_points = False
            continue
        if in_points and stripped.startswith("- "):
            points.append(stripped[2:].strip())
    return title, sections, points


# -------------------- 截图 --------------------

def _run_ffmpeg_frame(video_path, seconds, out_jpg):
    """在 video_path 的 seconds 处抽一帧到 out_jpg。成功返回 True。"""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", str(seconds), "-i", video_path,
             "-frames:v", "1", "-q:v", "2", out_jpg],
            capture_output=True, timeout=60, check=True,
        )
    except Exception:
        return False
    return os.path.exists(out_jpg) and os.path.getsize(out_jpg) > 0


def _extract_frames(video_path, sections, frames_dir):
    """按小节时间点抽帧，返回 {ts_str: 文件名}。文件名=mm-ss.jpg。失败的小节跳过。"""
    os.makedirs(frames_dir, exist_ok=True)
    frames, seen = {}, set()
    for s in sections:
        ts = s.get("ts")
        if not ts or ts in seen:
            continue
        seen.add(ts)
        fname = f"{ts.replace(':', '-')}.jpg"
        out = os.path.join(frames_dir, fname)
        if _run_ffmpeg_frame(video_path, _parse_ts_seconds(ts), out):
            frames[ts] = fname
    return frames


def _embed_frames(article, frames, subdir):
    """在小节标题后插入 ![[附录/<subdir>/xxx.jpg]]。subdir=文章名+「截图」。"""
    out = []
    for line in article.splitlines():
        out.append(line)
        m = _SECTION_RE.match(line.strip())
        if m and m.group(2) in frames:
            out.append("")
            out.append(f"![[附录/{subdir}/{frames[m.group(2)]}]]")
            out.append("")
    return "\n".join(out)


# -------------------- 思维导图 --------------------

def _mm_label(s):
    """清理节点文本，去掉时间点和括号类符号，避免破坏 mermaid 语法。"""
    s = re.sub(r'（?\d{1,2}:\d{2}(?::\d{2})?）?', '', s)
    s = re.sub(r'[()\[\]{}（）]', '', s)
    return s.strip()


def _build_mindmap(title, section_titles, points):
    """生成思维导图：优先 LLM 语义分层树，失败回退平铺版。返回 (mermaid块, 纯文本大纲)。"""
    try:
        tree = _build_mindmap_tree_via_llm(title, section_titles, points)
        if tree and sum(1 for _ in _walk_nodes(tree)) >= 3:
            return _render_mindmap_tree(tree), _render_outline_tree(tree)
    except Exception:
        pass
    return _build_mindmap_flat(title, section_titles, points)


def _build_mindmap_prompt(title, section_titles, points):
    section_lines = "\n".join(f"- {s}" for s in section_titles) or "（无）"
    point_lines = "\n".join(f"- {p}" for p in points) or "（无）"
    return (
        "你是思维导图整理助手。根据视频的小节标题和要点，生成一个 2~3 层的语义化思维导图大纲。\n"
        "要求：\n"
        "1. 用缩进列表输出，每行一个节点；层级用两个空格表示缩进。\n"
        "2. 第一行是根节点（=视频主题）。\n"
        "3. 第二层拆成 2~5 个语义分组，把相关内容归并，不要照抄小节编号。\n"
        "4. 第三层是每组下的要点。\n"
        "5. 每个节点 ≤12 个字，只留关键词，不要整句、不要时间点。\n"
        "6. 只输出大纲，不要任何解释、不要代码块。\n\n"
        f"视频主题：{title}\n\n小节标题：\n{section_lines}\n\n要点：\n{point_lines}"
    )


def _build_mindmap_tree_via_llm(title, section_titles, points):
    """LLM 生成语义分层树，返回根节点 dict 或 None。"""
    raw = call_llm(
        [{"role": "user", "content": _build_mindmap_prompt(title, section_titles, points)}],
        temperature=0.2, max_tokens=1500,
    )
    roots = _parse_outline(raw)
    if not roots:
        return None
    root = roots[0]
    # 若 LLM 第一行不是视频主题，直接套一层（比较前归一化空格，避免「有空格/无空格」误判成重复）
    if _norm_title(root["name"]) != _norm_title(title) and root["children"]:
        return {"name": title, "children": roots}
    return root


def _norm_title(s):
    """标题归一化：去所有空白，用于「LLM 第一行是否等于视频主题」的判断。"""
    return re.sub(r"\s+", "", _mm_label(s))


def _parse_outline(text):
    """解析缩进列表为树 [{name, children:[...]}]。"""
    roots, stack = [], []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("```", "#")):
            continue
        name = _mm_label(s.lstrip("-•* ").strip())
        if not name:
            continue
        indent = len(line) - len(line.lstrip())
        node = {"name": name, "children": []}
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((indent, node))
    return roots


def _walk_nodes(node):
    yield node
    for c in node.get("children", []):
        yield from _walk_nodes(c)


def _flow_label(s):
    """flowchart 节点文本：复用 mm_label 清理，再剥掉双引号避免破坏语法。"""
    return _mm_label(s).replace('"', '').replace("'", '')


def _render_mindmap_tree(root):
    """树 → mermaid flowchart LR（根在最左、向右平铺展开，对标夸克导图）。"""
    lines = ["flowchart LR"]
    counter = [0]

    def walk(node, parent_id=None):
        nid = f"n{counter[0]}"
        counter[0] += 1
        lines.append(f'    {nid}["{_flow_label(node["name"])}"]')
        if parent_id is not None:
            lines.append(f"    {parent_id} --> {nid}")
        for c in node.get("children", []):
            walk(c, nid)

    walk(root)
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def _render_outline_tree(root):
    """树 → 纯文本缩进大纲。"""
    out = []
    def walk(node, depth):
        out.append("  " * depth + "- " + node["name"])
        for c in node.get("children", []):
            walk(c, depth + 1)
    walk(root, 0)
    return "\n".join(out)


def _build_mindmap_flat(title, section_titles, points):
    """确定性回退版：根在左的 flowchart LR + 纯文本大纲。"""
    lines = ["flowchart LR"]
    root_id = "n0"
    lines.append(f'    {root_id}["{_flow_label(title)}"]')
    idx = 1
    prev_ids = []
    for st in section_titles:
        nid = f"n{idx}"
        idx += 1
        lines.append(f'    {nid}["{_flow_label(st)}"]')
        lines.append(f"    {root_id} --> {nid}")
        prev_ids.append(nid)
    if points:
        nid = f"n{idx}"
        idx += 1
        lines.append(f'    {nid}["要点"]')
        lines.append(f"    {root_id} --> {nid}")
        for p in points:
            pid = f"n{idx}"
            idx += 1
            lines.append(f'    {pid}["{_flow_label(p)}"]')
            lines.append(f"    {nid} --> {pid}")
    mermaid = "```mermaid\n" + "\n".join(lines) + "\n```"

    outline = [f"- {title}"]
    for st in section_titles:
        outline.append(f"  - {st}")
    if points:
        outline.append("  - 要点")
        for p in points:
            outline.append(f"    - {p}")
    return mermaid, "\n".join(outline)


def _merge_segments_to_paragraphs(segments, gap=1.5, max_chars=200):
    """把 whisper 短分段按停顿合并成段落级，返回 [{start, text}]。

    gap：相邻分段间隔超过该秒数视为新段落；max_chars：段落累计字数上限。
    """
    paras, cur_text, cur_start, prev_end = [], "", None, None
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = seg.get("start", 0.0)
        end = seg.get("end", start)
        if cur_start is None:
            cur_start = start
        if cur_text and prev_end is not None and (start - prev_end) > gap:
            paras.append({"start": cur_start, "text": cur_text})
            cur_text, cur_start = text, start
        elif cur_text and len(cur_text) + len(text) > max_chars:
            paras.append({"start": cur_start, "text": cur_text})
            cur_text, cur_start = text, start
        else:
            cur_text = (cur_text + text) if cur_text else text
        prev_end = end
    if cur_text:
        paras.append({"start": cur_start, "text": cur_text})
    return paras


def _punctuate_batch(lines):
    """LLM 给一批无标点段落补标点，返回 (时间, 文本) 列表；失败返回 None。"""
    prompt = (
        "下面是视频转录的若干段落，每段以 [mm:ss] 开头。请给每段文字添加标点符号（，。？！、），"
        "不要增删改任何字词，保持每段一行、保持 [mm:ss] 前缀与段落顺序不变。"
        "只输出处理后的文本，不要任何解释。\n\n" + "\n".join(lines)
    )
    raw = call_llm(
        [{"role": "user", "content": prompt}],
        temperature=0.1, max_tokens=4000,
    )
    out = []
    for line in raw.splitlines():
        m = re.match(r'^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+)$', line.strip())
        if m:
            out.append((m.group(1), m.group(2).strip()))
    return out if len(out) == len(lines) else None


def _punctuate_paragraphs(paragraphs):
    """按批次给段落补标点；任一批失败则整体回退无标点原文。返回 [(时间, 文本)]。"""
    lines = [f"[{_fmt_ts(p['start'])}] {p['text']}" for p in paragraphs]
    result, batch, batch_chars = [], [], 0
    for line in lines:
        batch.append(line)
        batch_chars += len(line)
        if batch_chars >= 2500:
            part = _punctuate_batch(batch)
            if part is None:
                return [(p["start"], p["text"]) for p in paragraphs]
            result.extend(part)
            batch, batch_chars = [], 0
    if batch:
        part = _punctuate_batch(batch)
        if part is None:
            return [(p["start"], p["text"]) for p in paragraphs]
        result.extend(part)
    return result if len(result) == len(paragraphs) else [(p["start"], p["text"]) for p in paragraphs]


def _build_transcript_callout(segments, plain_text, has_ts):
    """完整文稿 → Obsidian 折叠 callout（默认收起）。

    有时间戳分段时：合并成段落级 + LLM 补标点，每段只标一个起始时间点；
    无时间戳（纯文本兜底）时：原样逐行收起。
    """
    if has_ts and segments:
        paragraphs = _merge_segments_to_paragraphs(segments)
        items = _punctuate_paragraphs(paragraphs)
        body = "\n".join(f"> [{ts}] {text}" for ts, text in items)
        return "> [!note]- 完整文稿（转写全文，已分段）\n>\n" + body
    body = "\n".join(f"> {line}" for line in plain_text.splitlines())
    return "> [!note]- 完整文稿（转写全文）\n>\n" + body


def _assemble_article(article, mindmap, outline, transcript_callout):
    """组装最终文章：大纲放开头（摘要后、正文前），思维导图 + 完整文稿在尾部。"""
    # 大纲插入点：第一个 "## "（正文小节/要点）之前
    lines = article.rstrip().splitlines()
    idx = next((i for i, ln in enumerate(lines) if ln.startswith("## ")), None)
    if idx is not None:
        head = "\n".join(lines[:idx]).rstrip()
        tail = "\n".join(lines[idx:])
        body = head + "\n\n## 大纲\n\n" + outline + "\n\n" + tail
    else:
        body = article.rstrip() + "\n\n## 大纲\n\n" + outline
    parts = [body]
    parts.append("\n---\n\n## 思维导图\n\n" + mindmap)
    parts.append("\n---\n\n## 完整文稿\n\n" + transcript_callout)
    return "\n".join(parts)


# -------------------- 落盘 --------------------

def _write_article(topic_dir, article, title, source_label, method, today, topic):
    os.makedirs(topic_dir, exist_ok=True)
    fname = f"{today} - {_sanitize_title(title)}.md"
    path = os.path.join(topic_dir, fname)
    front = (
        "---\n"
        f'标题: {_yaml_str(title)}\n'
        f'专题: {_yaml_str(topic)}\n'
        f'来源: {_yaml_str(source_label)}\n'
        f'转录方式: {_yaml_str(method)}\n'
        f'整理日期: {_yaml_str(today)}\n'
        "---\n\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(front + article.strip() + "\n")
    return path


# -------------------- 单条处理 --------------------

def _process_one(target, topic, out_root, do_frames, today):
    """处理单个视频，返回输出文件路径；致命失败抛异常。"""
    print(f"  [转录] {target}", file=sys.stderr)

    segments, method = _get_segments(target)
    if segments:
        has_ts = any(s.get("start") for s in segments)
        transcript = _segments_timestamped_text(segments) if has_ts else _segments_text(segments)
    else:
        has_ts = False
        transcript, method = _get_plain_transcript(target)
        if not transcript:
            raise RuntimeError("无法获得转录文本（字幕 API 与 Groq whisper 均失败，或音频超 25MB）")
    print(f"  [转录] 成功（{method}，{len(transcript)} 字，时间点={'有' if has_ts else '无'}）", file=sys.stderr)

    print("  [成文] 生成文章…", file=sys.stderr)
    article = _generate_article(transcript, target, has_ts)
    if not article or not article.strip():
        raise RuntimeError("LLM 未返回文章内容")
    title = _extract_title(article)
    _, sections, points = _parse_article(article)

    # 截图：仅本地文件，且有时间点
    topic_dir = os.path.join(out_root, _sanitize_title(topic))
    frames = {}
    if do_frames and os.path.isfile(target) and has_ts:
        subdir = f"{today} - {_sanitize_title(title)}截图"
        frames_dir = os.path.join(topic_dir, "附录", subdir)
        frames = _extract_frames(target, sections, frames_dir)
        if frames:
            article = _embed_frames(article, frames, subdir)
            print(f"  [截图] {len(frames)} 张", file=sys.stderr)
        else:
            print("  [截图] 0 张（抽帧失败或无时间点）", file=sys.stderr)
    elif os.path.isfile(target) and not do_frames:
        print("  [截图] 已跳过（--no-frames）", file=sys.stderr)
    else:
        print("  [截图] 网络视频暂不截图", file=sys.stderr)

    # 思维导图 + 文稿
    mindmap, outline = _build_mindmap(title, [s["title"] for s in sections], points)
    callout = _build_transcript_callout(segments, transcript, has_ts)
    final = _assemble_article(article, mindmap, outline, callout)

    path = _write_article(topic_dir, final, title, target, method, today, topic)
    print(f"  [落盘] {path}", file=sys.stderr)
    return path


# -------------------- 批量 --------------------

def _scan_videos(folder):
    """遍历文件夹下所有视频文件（不递归）。"""
    files = []
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if os.path.isfile(p) and os.path.splitext(name)[1].lower() in _VIDEO_EXTS:
            files.append(p)
    return files


def main(argv=None):
    # 本脚本用 reasoning 模型（deepseek-v4-pro 等）成文，长转录 prompt 大，推理常超默认 120s。
    os.environ.setdefault("LLM_API_TIMEOUT_SECONDS", "600")

    ap = argparse.ArgumentParser(description="视频 → 结构化中文文章（时间点+截图+思维导图+文稿，支持批量）")
    ap.add_argument("target", help="视频 URL / 本地视频文件 / 视频文件夹")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"输出根目录（默认 {DEFAULT_OUT}）")
    ap.add_argument("--topic", default=None, help="专题名（默认：批量=文件夹名；单文件=父文件夹名；URL=网络视频）")
    ap.add_argument("--no-frames", action="store_true", help="不抽截图")
    ap.add_argument("--net-frames", action="store_true", help="网络视频也抽截图（暂未实现，接受但跳过）")
    args = ap.parse_args(argv)

    target = args.target.strip()
    if not target:
        print("错误：请提供视频 URL / 本地文件 / 文件夹路径。", file=sys.stderr)
        return 2

    if args.net_frames:
        print("提示：--net-frames 尚未实现，网络视频本版不抽截图。", file=sys.stderr)

    today = datetime.date.today().isoformat()

    # 批量：文件夹
    if os.path.isdir(target):
        videos = _scan_videos(target)
        if not videos:
            print(f"错误：文件夹 {target} 里没有视频文件。", file=sys.stderr)
            return 1
        topic = args.topic or os.path.basename(os.path.normpath(target))
        print(f"批量处理：{len(videos)} 个视频，专题=「{topic}」", file=sys.stderr)
        failed = []
        for i, v in enumerate(videos, 1):
            print(f"\n[{i}/{len(videos)}] {os.path.basename(v)}", file=sys.stderr)
            try:
                _process_one(v, topic, args.out, not args.no_frames, today)
            except Exception as e:
                failed.append((v, str(e)))
                print(f"  [失败] {e}", file=sys.stderr)
        print(f"\n批量完成：成功 {len(videos) - len(failed)} / 失败 {len(failed)}", file=sys.stderr)
        for v, err in failed:
            print(f"  失败：{os.path.basename(v)} -> {err}", file=sys.stderr)
        return 0 if not failed else 1

    # 单文件 / URL
    if os.path.exists(target):
        topic = args.topic or os.path.basename(os.path.dirname(os.path.abspath(target)))
    else:
        topic = args.topic or "网络视频"
    try:
        path = _process_one(target, topic, args.out, not args.no_frames, today)
    except Exception as e:
        print(f"错误：{e}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
