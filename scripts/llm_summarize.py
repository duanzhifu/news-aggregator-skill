"""Use an LLM to translate news metadata and generate daily summaries."""
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.llm_client import call_llm, extract_json_block
    from scripts.scoring_rules import attach_engagement_scores, attach_source_keys
except ModuleNotFoundError:
    from llm_client import call_llm, extract_json_block
    from scoring_rules import attach_engagement_scores, attach_source_keys


SYSTEM_PROMPT_TRANSLATE = """你是一位技术新闻编辑，负责把英文科技内容翻译成简体中文。

规则：
1. 标题翻译要自然、准确，符合中文技术社区习惯。
2. 保留 React、GitHub、Python、API、LLM、AI、SQL 等专有名词和项目名。
3. 不要添加原文没有的内容，也不要复述网页导航、登录提示或截断文本。
4. summary_zh 使用 2 至 3 句简洁中文，说明主题、做法和价值。
5. 如果内容疑似垃圾推广，应明确标注，不要复述联系方式或购买渠道。
"""

TRANSLATION_SUCCESS = "success"
TRANSLATION_RETRY_SUCCESS = "retry_success"
TRANSLATION_FAILED = "failed"

RECOMMENDATION_LEVELS = {"strongly_recommended", "optional", "not_recommended"}
# Only successfully evaluated recommendations feed the normal daily digest.
PUBLISHABLE_RECOMMENDATIONS = {
    "strongly_recommended",
    "optional",
}
DEFAULT_RECOMMENDATION_TOPICS = [
    "前端工程", "人工智能", "AI 工程", "后端开发", "开发工具", "开源项目","前沿技术","前沿信息",
]
EVALUATION_SUCCESS = "success"
EVALUATION_FAILED = "failed"
TIME_CONFIDENCE_LEVELS = {"high", "medium", "low", "unknown"}
TIME_KINDS = {"published", "updated", "repository_last_push", "ranking_observed", "unknown"}

SENSITIVE_LLM_PATTERNS = (
    re.compile(r"```.*?```", re.DOTALL),
    re.compile(r"(?i)(?:api[_ -]?key|secret|password|token)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:exploit|payload|reverse shell|remote code execution|privilege escalation|penetration test)\b"),
    re.compile(r"(?i)\b(?:sql injection|xss|cross[- ]site scripting|command injection|漏洞利用|渗透测试|攻击测试)\b"),
)


def sanitize_for_llm(value):
    """Redact high-risk payloads in the copy sent to the provider."""
    text = str(value or "")
    for pattern in SENSITIVE_LLM_PATTERNS:
        text = pattern.sub("[内容已脱敏]", text)
    return text


def sanitize_item_for_llm(item):
    sanitized = dict(item)
    for field in ("title", "summary", "description", "content"):
        if field in sanitized:
            sanitized[field] = sanitize_for_llm(sanitized[field])
    return sanitized


def build_translate_prompt(items, translate_content=True, minimal=False):
    """Build a translation request; minimal requests omit full article bodies."""
    lines = []
    for idx, raw_item in enumerate(items):
        item = sanitize_item_for_llm(raw_item)
        parts = [f"[{idx}]", f"Source: {item.get('source', '')}", f"Title: {item.get('title', '')}"]
        if minimal:
            summary = item.get("summary", "")
            if summary:
                parts.append(f"Excerpt: {summary[:800]}")
        else:
            content = item.get("content", "")
            if content:
                parts.append(f"Article body (全文正文): {content[:3000]}")
            elif item.get("summary", ""):
                parts.append(f"Feed excerpt (信息源摘要，不是全文): {item['summary'][:800]}")
        lines.append("\n".join(parts))

    content_instruction = (
        '同时返回 content_zh；有 Content 时翻译正文并保留段落，无 Content 时返回空字符串。'
        if translate_content
        else '不要翻译正文，content_zh 必须返回空字符串。'
    )
    user_prompt = f"""请逐条生成自然准确的中文标题和 2 至 3 句中文总结。{content_instruction}
输出严格合法的 JSON 对象：
{{"items":[{{"title_zh":"中文标题","summary_zh":"中文总结","content_zh":""}}]}}
items 数量和顺序必须与输入完全一致，不要输出 JSON 之外的文字。

待处理内容：
""" + "\n\n".join(lines)
    return [
        {"role": "system", "content": SYSTEM_PROMPT_TRANSLATE},
        {"role": "user", "content": user_prompt},
    ]


def _parse_translation_response(raw, expected_count):
    parsed = parse_llm_json(raw)
    translated = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(translated, list) or len(translated) != expected_count:
        actual = len(translated) if isinstance(translated, list) else 0
        raise ValueError(f"翻译返回数量不匹配：期望 {expected_count}，实际 {actual}")
    for entry in translated:
        if not isinstance(entry, dict) or not entry.get("title_zh", "").strip() or not entry.get("summary_zh", "").strip():
            raise ValueError("翻译响应缺少 title_zh 或 summary_zh")
    return translated


def _enrich_translation(item, translated, status, translate_content):
    enriched = dict(item)
    enriched["title_zh"] = translated["title_zh"].strip()
    enriched["summary_zh"] = translated["summary_zh"].strip()
    enriched["translation_status"] = status
    if translate_content and item.get("content"):
        content_zh = translated.get("content_zh", "").strip()
        if content_zh:
            enriched["content"] = content_zh
    return enriched


def _failed_translation(item, error):
    enriched = dict(item)
    enriched["title_zh"] = ""
    enriched["summary_zh"] = ""
    enriched["translation_status"] = TRANSLATION_FAILED
    enriched["translation_error"] = str(error)[:300]
    return enriched


def _retry_backoff():
    try:
        delay = max(0.0, float(os.environ.get("LLM_RETRY_BACKOFF_SECONDS", "0.5")))
    except ValueError:
        delay = 0.5
    if delay:
        time.sleep(delay)


def _batch_delay():
    try:
        return max(0.0, float(os.environ.get("LLM_BATCH_DELAY_SECONDS", "0.25")))
    except ValueError:
        return 0.25


def _translate_batch(items, translate_content, was_retried=False, split_depth=0):
    """Translate a batch with one bounded split and one minimal per-item retry."""
    try:
        raw = call_llm(
            build_translate_prompt(items, translate_content=translate_content),
            temperature=0.3,
            max_tokens=6000,
            json_mode=True,
        )
        translated = _parse_translation_response(raw, len(items))
        status = TRANSLATION_RETRY_SUCCESS if was_retried else TRANSLATION_SUCCESS
        return [_enrich_translation(item, trans, status, translate_content) for item, trans in zip(items, translated)]
    except Exception as error:
        print(f"[LLM Warning] {len(items)} 条翻译失败：{error}")
        _retry_backoff()
        if len(items) > 1 and split_depth == 0:
            middle = len(items) // 2
            return (
                _translate_batch(items[:middle], translate_content, was_retried=True, split_depth=1)
                + _translate_batch(items[middle:], translate_content, was_retried=True, split_depth=1)
            )

        item = items[0]
        try:
            raw = call_llm(
                build_translate_prompt([item], translate_content=False, minimal=True),
                temperature=0.2,
                max_tokens=1200,
                json_mode=True,
            )
            translated = _parse_translation_response(raw, 1)[0]
            return [_enrich_translation(item, translated, TRANSLATION_RETRY_SUCCESS, translate_content=False)]
        except Exception as retry_error:
            print(f"[LLM Error] 单篇最小请求仍失败：{retry_error}")
            return [_failed_translation(item, retry_error)]


def translate_items(items, batch_size=5, translate_content=True):
    """Translate in bounded batches without allowing one item to poison a batch."""
    results = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        print(f"[LLM] 翻译批次 {i // batch_size + 1}/{(len(items) - 1) // batch_size + 1}，{len(batch)} 条...")
        results.extend(_translate_batch(batch, translate_content))
        time.sleep(_batch_delay())
    return results


def _repair_truncated_json(text):
    """尽力修复截断的 JSON：截到最后一个完整对象边界，补闭合括号；无法修复返回 None。"""
    if not text or not text.strip():
        return None
    for end in range(len(text), 0, -1):
        if text[end - 1] == '}':
            candidate = text[:end]
            depth_braces = candidate.count('{') - candidate.count('}')
            depth_brackets = candidate.count('[') - candidate.count(']')
            if depth_braces == 0 and depth_brackets == 0:
                try:
                    return json.loads(candidate)
                except Exception:
                    continue
            if depth_braces >= 0 and depth_brackets >= 0 and depth_braces <= 40:
                fixed = candidate + ']' * depth_brackets + '}' * depth_braces
                try:
                    return json.loads(fixed)
                except Exception:
                    continue
    return None


def parse_llm_json(raw, kind='snapshot'):
    """解析 LLM 输出 JSON；失败时尝试截断修复，仍失败才抛错。"""
    text = extract_json_block(raw)
    try:
        return json.loads(text)
    except (ValueError, json.JSONDecodeError):
        repaired = _repair_truncated_json(text)
        if repaired is not None:
            return repaired
        raise


def normalize_topics(topics=None):
    if topics is None:
        return list(DEFAULT_RECOMMENDATION_TOPICS)
    return [str(topic).strip() for topic in topics if str(topic).strip()]


# ==== 用户画像：从主库读取并提炼（A 方案：run 时现场提炼一次，全局复用）====
USER_PROFILE_KB_ROOT = r'D:\Obsidian\智能知识库'
USER_PROFILE_FILES = [
    '知识库/个人系统/个人档案.md',
    '知识库/个人系统/能力地图.md',
]
USER_PROFILE_PROGRESS_GLOB = '知识库/个人系统/项目/*/项目进度/*.md'
REPORTS_DIR = Path(__file__).resolve().parent.parent / 'reports'
PROFILE_CACHE_PATH = REPORTS_DIR / 'user_profile_cache.json'


def load_user_profile_materials(max_progress_entries=3, kb_root=USER_PROFILE_KB_ROOT):
    """读个人档案 + 能力地图 + 最近 N 篇项目进度，返回原始文本（仅读，不写两座库）。

    项目进度按 mtime 取最新 N 篇、自动跨项目；呼应「每 5 篇就沉淀」，取 3 篇不漏且省。
    """
    root = Path(kb_root)
    if not root.is_dir():
        print(f"[画像] 主库材料目录不存在: {kb_root}。已回退为按 topics 判断（不写死身份）。", file=sys.stderr)
        return ""
    sections = []
    for rel in USER_PROFILE_FILES:
        p = root / rel
        if p.exists():
            sections.append(f"# 来源：{rel}\n\n{p.read_text(encoding='utf-8', errors='replace')}")
    try:
        progress = sorted(
            root.glob(USER_PROFILE_PROGRESS_GLOB),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        # 只取「按日进度纪要」格式 YYYY-MM-DD - 标题.md；排除 README 等非流水文件（它们会挤占真实纪要名额）
        progress = [p for p in progress if re.match(r'\d{4}-\d{2}-\d{2}\s+-', p.name)]
        progress = progress[:max_progress_entries]
    except Exception:
        progress = []
    for p in progress:
        rel = p.relative_to(root).as_posix()
        sections.append(f"# 来源：{rel}\n\n{p.read_text(encoding='utf-8', errors='replace')}")
    if not sections:
        print(f"[画像] 主库 {kb_root} 下未读到任何画像材料（文件: {USER_PROFILE_FILES}, glob: {USER_PROFILE_PROGRESS_GLOB}）。"
              f"已回退为按 topics 判断（不写死身份）。", file=sys.stderr)
        return ""
    return "\n\n".join(sections)


def build_user_profile_prompt(materials):
    return f"""你是一位用户画像提炼助手。下面是从用户 Obsidian 知识库读到的原始材料（个人档案 + 能力地图 + 最近项目进度）。
提炼成一段面向「新闻内容判断与推荐决策」的精炼用户画像（1200 字以内，简体中文，分三小节）。

只提取与「判断技术内容是否适合该用户」相关的信号，不要照抄档案表格或能力清单原文：
- 【我是谁】：阶段、长期目标、已具备/正在用的技术、能力边界。
- 【我现在做/学什么】：当前核心项目、正在做的事。注意：能力地图里「待验证」项表示该技能还没验证，恰恰是接下来要攻的方向；项目进度是最近讨论/落地的主题。
- 【我现在需要什么内容】：明确三类都要——（1）概念扫盲/名词解释，（2）与我现在项目强相关的（如 Obsidian 知识库、Claude Code、agent 人格优化），（3）前沿动态。
  概念扫盲的判断依据 = 材料里我是否已有基础（2026-09-08 用户拍板）：材料未覆盖/我完全没听过的概念 → 需要入门扫盲（哪怕与当前项目不直接相关，先知道它是什么、解决什么问题）；材料里已有基础的技术（如 HTML/CSS/JS/React）→ 不需要入门，只要深度、实战、踩坑类内容；材料里标「待验证」的 → 需要具体实操与落地内容，不是入门也不是纯概念。

这份画像将用于：判断候选标题值不值得打开、最终决定是否推荐阅读。因此请写出能让判断更贴合他需求的信号，不要泛泛而谈。

材料：
{materials}

只输出画像正文，不要其他解释。"""


def distill_user_profile(materials):
    """调 LLM 把知识库材料提炼成一版面向推荐决策的用户画像。"""
    if not materials.strip():
        return ""
    raw = call_llm(
        [{"role": "system", "content": "你是一位严谨的用户画像提炼助手。"},
         {"role": "user", "content": build_user_profile_prompt(materials)}],
        temperature=0.2, max_tokens=None, json_mode=False,
    )
    return (raw or "").strip()


def _load_profile_cache():
    """读画像缓存（材料哈希 + 画像文本 + 提炼日期）；不存在/损坏返回 None。"""
    try:
        if PROFILE_CACHE_PATH.exists():
            return json.loads(PROFILE_CACHE_PATH.read_text(encoding='utf-8'))
    except Exception:
        return None
    return None


def _save_profile_cache(materials_hash, profile):
    """写画像缓存。失败只打 stderr，不中断主流程。"""
    try:
        PROFILE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_CACHE_PATH.write_text(
            json.dumps({
                "materials_hash": materials_hash,
                "profile": profile,
                "date": datetime.now().strftime('%Y-%m-%d'),
            }, ensure_ascii=False, indent=2),
            encoding='utf-8')
    except Exception as e:
        print(f"[画像] 写画像缓存失败：{e}", file=sys.stderr)


def get_user_profile(max_progress_entries=3, snapshot=True):
    """读材料 → 提炼画像 → 返回画像文本（一次 run 调一次，结果由下游各判断层复用）。

    缓存机制（2026-09-08 用户拍板）：对读到的材料算 sha256，
    - 与缓存哈希相同 = 用户信息没变 → 延用缓存画像，不重新提炼、不写当日快照（省 LLM token）；
    - 材料有变化（个人档案/能力地图/项目进度更新）→ 重新提炼 + 更新缓存 + 写当日快照。
    失败返回空串兜底，不影响本轮按 topics 判断。
    """
    profile = ""
    try:
        materials = load_user_profile_materials(max_progress_entries)
        current_hash = hashlib.sha256(materials.encode('utf-8')).hexdigest()
        cache = _load_profile_cache()
        if cache and cache.get('materials_hash') == current_hash and cache.get('profile'):
            profile = cache['profile']
            print(f"[画像] 用户材料无变化，延用 {cache.get('date', '?')} 提炼的画像（{len(profile)}字），本轮不重新提炼")
        else:
            profile = distill_user_profile(materials)
            if profile:
                _save_profile_cache(current_hash, profile)
    except Exception as e:
        print(f"[画像] 提炼用户画像失败，本轮仅按 topics 判断：{e}", file=sys.stderr)
    profile = profile or ""
    if snapshot and profile:
        try:
            snap_dir = REPORTS_DIR / datetime.now().strftime('%Y-%m-%d')
            snap_dir.mkdir(parents=True, exist_ok=True)
            (snap_dir / 'user_profile.md').write_text(profile, encoding='utf-8')
            print(f"[画像] 已写当日画像快照：{snap_dir / 'user_profile.md'}")
        except Exception as e:
            print(f"[画像] 写当日快照失败：{e}", file=sys.stderr)
    if profile:
        print(f"[画像] 本次提炼用户画像（{len(profile)}字）：{profile[:120]}...")
    else:
        print("[画像] 本轮未提炼到用户画像")
    return profile


def build_candidate_selection_prompt(items, topics=None, recency_days=7, now_iso=None, reject=None, user_profile=None):
    """Ask AI which metadata candidates deserve a page visit."""
    topics_text = ", ".join(normalize_topics(topics)) or "通用前沿技术"
    reject_text = "、".join(str(r) for r in (reject or [])) or "（无）"
    profile_text = (user_profile or "").strip()
    now_iso = now_iso or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    lines = []
    for idx, raw_item in enumerate(items):
        item = sanitize_item_for_llm(raw_item)
        raw_times = {
            key: item.get(key) for key in (
                "published", "published_at", "pub_time", "publish_time",
                "created_at", "created_at_i", "time", "time_kind", "time_source",
            ) if item.get(key) not in (None, "")
        }
        lines.append("\n".join([
            f"[{idx}] Source: {item.get('source', '')}",
            f"Title: {item.get('title', '')}",
            f"URL: {item.get('url', '')}",
            f"Raw times: {json.dumps(raw_times, ensure_ascii=False)}",
            f"Feed/API excerpt: {(item.get('summary') or item.get('description') or '')[:1000]}",
            f"Heat/metrics: {item.get('heat', '')} {item.get('engagement_metrics', '')}",
        ]))
    prompt = f"""你是新闻抓取代理，负责决定哪些候选值得进一步打开页面。
当前时间（也是所有相对时间的基准）：{now_iso}
用户关注主题：{topics_text}
用户明确不想要的类别（即使表面相关也应降级或拒绝）：{reject_text}
用户画像（此人背景，判断时请结合，别只按字面关键词匹配）：
{profile_text}
近期优先参考窗口：{recency_days} 天，但高价值旧文章、当前热榜或持续更新的项目可以保留。

逐条完成：
1. 理解不同来源的时间字段，区分发布、更新、仓库最近推送和榜单采集时间；不得把更新或推送时间伪装成首次发布时间。
2. 结合标题、摘要、来源、互动信号和时间语义判断是否值得打开页面。
3. 广告、推广、标题党、重复转载、与主题无关或证据明显不足的社交内容应拒绝。
4. 时间缺失不能单独成为拒绝理由；当前热榜可保留并标记 ranking_observed 或 unknown。
5. 不得猜测没有证据的日期。无法可靠归一化时 published_at 返回空字符串。
6. 看清上方「用户画像」：识别画像中此人当前的阶段、项目与内容需求（概念扫盲/项目强相关/前沿动态），判断候选是否值得打开要结合画像理解其真实意图，而不是只按字面关键词命中与否来判；若画像为空则按主题相关度判断，不臆测固定身份。

只输出严格合法 JSON：
{{"items":[{{"selected":true,"title_zh":"中文标题","selection_reason":"具体理由","rejection_kind":"not_applicable","evidence_points":["标题与用户关注主题直接相关","摘要包含可验证的技术细节"],"published_at":"2026-08-04T09:30:00+08:00","time_kind":"published","time_confidence":"high","time_evidence":"原始字段或页面列表中的证据"}}]}}
若 selected=false，rejection_kind 必须是 definitive 或 transient。主题不符、低价值、过期、广告和重复内容属于 definitive；信息缺失、标题异常、抓取故障或证据不足属于 transient。selected=true 时使用 not_applicable。evidence_points 返回 1 至 3 条本条候选的具体判断依据，不得使用统一套话。
title_zh 必须将原标题翻译为简体中文；如果原标题已经是中文则保持原意。即使 selected=false 也必须返回 title_zh。
time_kind 只能是 published、updated、repository_last_push、ranking_observed、unknown；time_confidence 只能是 high、medium、low、unknown。items 数量和顺序必须与输入一致。

候选：
""" + "\n\n".join(lines)
    return [
        {"role": "system", "content": "你是一位严谨、保守且可审计的新闻抓取代理。"},
        {"role": "user", "content": prompt},
    ]


def _parse_candidate_selection(raw, items):
    parsed = parse_llm_json(raw)
    entries = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(entries, list) or len(entries) != len(items):
        raise ValueError("AI 准入返回数量不匹配")
    result = []
    for item, entry in zip(items, entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("selected"), bool):
            raise ValueError("AI 准入缺少 selected 布尔值")
        title_zh = entry.get("title_zh")
        if not isinstance(title_zh, str) or not title_zh.strip():
            raise ValueError("AI 准入缺少 title_zh")
        kind = entry.get("time_kind", "unknown")
        confidence = entry.get("time_confidence", "unknown")
        if kind not in TIME_KINDS or confidence not in TIME_CONFIDENCE_LEVELS:
            raise ValueError("AI 准入返回了无效的时间类型或置信度")
        enriched = dict(item)
        enriched.update({
            "ai_selected": entry["selected"],
            "title_zh": title_zh.strip(),
            "selection_reason": str(entry.get("selection_reason", "")).strip(),
            "rejection_kind": str(entry.get("rejection_kind", "not_applicable" if entry["selected"] else "definitive")).strip(),
            "evidence_points": [str(value).strip() for value in entry.get("evidence_points", []) if str(value).strip()][:3],
            "published_at": str(entry.get("published_at", "")).strip(),
            "time_kind": kind,
            "time_confidence": confidence,
            "time_evidence": str(entry.get("time_evidence", "")).strip(),
            "selection_status": "success",
        })
        if enriched["rejection_kind"] not in {"definitive", "transient", "not_applicable"}:
            raise ValueError("AI 准入返回了无效的拒绝类型")
        result.append(enriched)
    return result


def _failed_selection(item, error):
    enriched = dict(item)
    enriched.update({
        "ai_selected": False,
        "selection_reason": "AI 准入判断失败，未自动打开页面。",
        "rejection_kind": "transient",
        "evidence_points": ["AI 准入调用失败，当前没有形成可靠判断。"],
        "published_at": "",
        "time_kind": "unknown",
        "time_confidence": "unknown",
        "time_evidence": "",
        "selection_status": "failed",
        "selection_error": str(error)[:300],
    })
    return enriched


def _select_batch(items, topics=None, recency_days=7, was_retried=False, split_depth=0, reject=None, user_profile=None):
    try:
        raw = call_llm(
            build_candidate_selection_prompt(items, topics, recency_days, reject=reject, user_profile=user_profile),
            temperature=0.1, max_tokens=6000, json_mode=True,
        )
        return _parse_candidate_selection(raw, items)
    except Exception as error:
        print(f"[LLM Warning] {len(items)} 条 AI 准入失败：{error}")
        _retry_backoff()
        if len(items) > 1 and split_depth == 0:
            middle = len(items) // 2
            return (
                _select_batch(items[:middle], topics, recency_days, True, 1, reject, user_profile)
                + _select_batch(items[middle:], topics, recency_days, True, 1, reject, user_profile)
            )
        return [_failed_selection(items[0], error)]


def select_candidates(items, batch_size=5, topics=None, recency_days=7, reject=None, user_profile=None):
    """Use metadata and raw source times to decide which pages AI should open."""
    prepared = attach_engagement_scores(attach_source_keys(items))
    results = []
    for i in range(0, len(prepared), batch_size):
        batch = prepared[i:i + batch_size]
        print(f"[AI 抓取] 候选准入批次 {i // batch_size + 1}/{(len(prepared) - 1) // batch_size + 1}，{len(batch)} 条...")
        results.extend(_select_batch(batch, topics, recency_days, reject=reject, user_profile=user_profile))
        time.sleep(_batch_delay())
    return results


FULL_TEXT_CHUNK_CHARS = 6000


def split_evidence_chunks(text, max_chars=FULL_TEXT_CHUNK_CHARS):
    """Split extracted article text on paragraph boundaries without dropping text."""
    text = str(text or "").strip()
    if not text:
        return []
    chunks = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while paragraph:
            available = max_chars - len(current) - (2 if current else 0)
            if available <= 0:
                chunks.append(current)
                current = ""
                available = max_chars
            if len(paragraph) <= available:
                current = f"{current}\n\n{paragraph}".strip() if current else paragraph
                paragraph = ""
                continue
            cut = paragraph.rfind("\n", 0, available)
            if cut <= 0:
                cut = available
            piece = paragraph[:cut].strip()
            if current:
                chunks.append(f"{current}\n\n{piece}".strip())
                current = ""
            else:
                chunks.append(piece)
            paragraph = paragraph[cut:].strip()
    if current:
        chunks.append(current)
    return chunks


def build_full_text_chunk_prompt(item, chunk, chunk_number, chunk_count, topics=None):
    """Ask for auditable facts from one complete-article segment."""
    topics_text = ", ".join(normalize_topics(topics)) or "通用前沿技术"
    safe_item = sanitize_item_for_llm(item)
    prompt = f"""你正在逐段审阅一篇完整文章。用户关注主题：{topics_text}。
这是第 {chunk_number}/{chunk_count} 段。只提取该段明确写出的事实、时间线索、价值信息与风险；不要推断段外内容，也不要在单段内给出整篇推荐结论。

只输出严格合法 JSON：
{{"chunk_summary":"不超过 250 字的中文事实摘要","evidence_points":["具体事实"],"time_evidence":"本段明确时间证据或空字符串","concerns":"本段的局限、广告或异常，或空字符串"}}
evidence_points 必须为 1 至 4 条具体事实；没有可验证事实时返回空数组。

来源：{safe_item.get('source', '')}
原标题：{safe_item.get('title', '')}
正文第 {chunk_number} 段：
{sanitize_for_llm(chunk)}"""
    return [
        {"role": "system", "content": "你是一位严谨的中文技术新闻编辑。"},
        {"role": "user", "content": prompt},
    ]


def _parse_full_text_chunk(raw):
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("chunk_summary"), str):
        raise ValueError("全文分块处理返回无效")
    points = parsed.get("evidence_points", [])
    if not isinstance(points, list):
        raise ValueError("全文分块处理缺少证据列表")
    return {
        "chunk_summary": parsed["chunk_summary"].strip()[:1200],
        "evidence_points": [str(point).strip() for point in points if str(point).strip()][:4],
        "time_evidence": str(parsed.get("time_evidence", "")).strip()[:500],
        "concerns": str(parsed.get("concerns", "")).strip()[:500],
    }


def _process_full_text_chunk(item, chunk, chunk_number, chunk_count, topics):
    try:
        raw = call_llm(
            build_full_text_chunk_prompt(item, chunk, chunk_number, chunk_count, topics),
            temperature=0.1, max_tokens=2000, json_mode=True,
        )
        return _parse_full_text_chunk(raw)
    except Exception as error:
        _retry_backoff()
        try:
            raw = call_llm(
                build_full_text_chunk_prompt(item, chunk, chunk_number, chunk_count, topics),
                temperature=0.0, max_tokens=2000, json_mode=True,
            )
            return _parse_full_text_chunk(raw)
        except Exception as retry_error:
            raise RuntimeError(f"第 {chunk_number}/{chunk_count} 段处理失败：{retry_error}") from error


def build_snapshot_processing_prompt(items, topics=None, recency_days=7, minimal=False, reject=None, user_profile=None):
    """Build the final decision from metadata and every article-segment finding."""
    topics_text = ", ".join(normalize_topics(topics)) or "通用前沿技术"
    reject_text = "、".join(str(r) for r in (reject or [])) or "（无）"
    profile_text = (user_profile or "").strip()
    lines = []
    for idx, raw_item in enumerate(items):
        item = sanitize_item_for_llm(raw_item)
        evidence = item.get("full_text_findings") or item.get("evidence_snapshot") or item.get("summary") or item.get("description") or ""
        limit = 1200 if minimal else None
        lines.append("\n".join([
            f"[{idx}] Title: {item.get('title', '')}",
            f"URL: {item.get('url', '')}",
            f"Source: {item.get('source', '')}",
            f"Full article findings: {evidence[:limit] if limit else evidence}",
        ]))
    prompt = f"""请基于完整文章所有分段的审阅结论完成最终编辑判断。用户关注主题：{topics_text}，近期优先参考窗口为 {recency_days} 天。
用户明确不想要的类别（这类内容即使表面相关也应降级为 not_recommended 或 optional，不要判为 strongly_recommended）：{reject_text}
用户画像（此人背景，判断是否推荐阅读时请结合，别只按字面关键词匹配）：
{profile_text}
每一段都已处理；只能使用下方元数据和分段结论中的证据，不得补充没有的实现、实验、因果关系或结论。一次性返回自然中文标题、2 至 3 句中文总结、质量判断及时间确认。明显广告、低价值转载、标题党或全文与标题不符时可在最终阶段拒绝。
判断推荐等级时请结合上方「用户画像」：若内容与他当前项目、学习阶段或前沿方向相关，即使标题不含关键词也应考虑推荐；若只是表面沾边 AI 但实际低价值/无信息量，仍应降级。画像为空时按主题相关度判断，不臆测固定身份。
质量分档锚点（quality_score 必须先归到下列四档，再在档内给出具体分数；分数与推荐等级必须一致，不得出现「高分低档」或「低分高档」的矛盾）：
【高价值干货 80-100】原创、有深度、有可复用信息（可复用代码/命令/配置、具体数据或对比实验、第一手经验或独到见解、原创非转载）。→ recommendation_level=strongly_recommended。
【有用参考 60-79】内容相关、信息真实、有价值，但偏介绍/综述/转述，深度或原创性一般。→ recommendation_level=optional。
【一般 40-59】表面沾边、信息量低、泛泛而谈、入门扫盲/名词解释。仍可入库（optional），但 recommendation_reason 必须明确写出「为何在信息量有限的情况下仍值得保留」的具体理由。→ recommendation_level=optional。
【低价值 0-39】标题党/夸大、拼凑或 AI 生成痕迹、广告/软文/引流、全文与标题不符、信息过时。→ recommendation_level=not_recommended。
打分规则：先判断属于哪一档（档位判断依据必须写进 recommendation_reason 与 evidence_points），再在该档区间内给出具体分数。
发布时间沿用可验证证据；全文提供更明确时间时可修正初步结果。不得把更新时间或仓库推送时间写成首次发布时间，也不得猜测缺失日期。

只输出严格合法 JSON：
{{"items":[{{"title_zh":"中文标题","summary_zh":"中文总结","quality_score":73,"recommendation_level":"optional","recommendation_reason":"具体依据","rejection_kind":"not_applicable","evidence_quality":"good","evidence_points":["快照明确说明了项目目标","页面给出了可复用的实现信息"],"published_at":"","time_kind":"published","time_confidence":"high","time_evidence":"具体时间证据"}}]}}
recommendation_level 只能是 strongly_recommended、optional、not_recommended；时间枚举与候选阶段相同。items 数量和顺序必须与输入一致。
若 recommendation_level=not_recommended，rejection_kind 必须是 definitive 或 transient；正文乱码、快照不足、标题缺失和页面故障属于 transient，不得永久拒绝。其他等级使用 not_applicable。evidence_quality 只能是 good、partial、insufficient；evidence_points 返回 1 至 3 条来自本条快照或元数据的具体证据。

内容：
""" + "\n\n".join(lines)
    return [
        {"role": "system", "content": "你是一位严谨的中文技术新闻编辑。"},
        {"role": "user", "content": prompt},
    ]


def _parse_snapshot_processing(raw, items):
    parsed = parse_llm_json(raw)
    entries = parsed.get("items") if isinstance(parsed, dict) else parsed
    if not isinstance(entries, list) or len(entries) != len(items):
        raise ValueError("快照处理返回数量不匹配")
    results = []
    for item, entry in zip(items, entries):
        score = entry.get("quality_score")
        level = entry.get("recommendation_level")
        kind = entry.get("time_kind", item.get("time_kind", "unknown"))
        confidence = entry.get("time_confidence", item.get("time_confidence", "unknown"))
        if not isinstance(entry.get("title_zh"), str) or not entry["title_zh"].strip():
            raise ValueError("快照处理缺少中文标题")
        if not isinstance(entry.get("summary_zh"), str) or not entry["summary_zh"].strip():
            raise ValueError("快照处理缺少中文总结")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError("快照处理质量分数无效")
        if level not in RECOMMENDATION_LEVELS or kind not in TIME_KINDS or confidence not in TIME_CONFIDENCE_LEVELS:
            raise ValueError("快照处理枚举值无效")
        enriched = dict(item)
        enriched.update({
            "title_zh": entry["title_zh"].strip(),
            "summary_zh": entry["summary_zh"].strip(),
            "quality_score": score,
            "recommendation_level": level,
            "recommendation_reason": str(entry.get("recommendation_reason", "")).strip(),
            "rejection_kind": str(entry.get("rejection_kind", "definitive" if level == "not_recommended" else "not_applicable")).strip(),
            "evidence_quality": str(entry.get("evidence_quality", "partial")).strip(),
            "evidence_points": [str(value).strip() for value in entry.get("evidence_points", []) if str(value).strip()][:3],
            "published_at": str(entry.get("published_at", item.get("published_at", ""))).strip(),
            "time_kind": kind,
            "time_confidence": confidence,
            "time_evidence": str(entry.get("time_evidence", item.get("time_evidence", ""))).strip(),
            "translation_status": TRANSLATION_SUCCESS,
            "evaluation_status": EVALUATION_SUCCESS,
        })
        if enriched["rejection_kind"] not in {"definitive", "transient", "not_applicable"}:
            raise ValueError("快照处理返回了无效的拒绝类型")
        if enriched["evidence_quality"] not in {"good", "partial", "insufficient"}:
            raise ValueError("快照处理返回了无效的证据质量")
        results.append(enriched)
    return results


def _failed_snapshot_processing(item, error):
    enriched = dict(item)
    enriched.update({
        "recommendation_level": "evaluation_failed",
        "quality_score": 0,
        "recommendation_reason": "快照处理未完成，已移入待复核清单。",
        "rejection_kind": "transient",
        "evidence_quality": "insufficient",
        "evidence_points": ["快照处理调用失败，当前没有足够证据完成评估。"],
        "evaluation_status": EVALUATION_FAILED,
        "evaluation_error": str(error)[:300],
    })
    return enriched


def _process_snapshot_batch(items, topics=None, recency_days=7, split_depth=0, reject=None, user_profile=None):
    try:
        raw = call_llm(
            build_snapshot_processing_prompt(items, topics, recency_days, reject=reject, user_profile=user_profile),
            temperature=0.2, max_tokens=8000, json_mode=True,
        )
        return _parse_snapshot_processing(raw, items)
    except Exception as error:
        print(f"[LLM Warning] {len(items)} 条快照处理失败：{error}")
        _retry_backoff()
        if len(items) > 1 and split_depth == 0:
            middle = len(items) // 2
            return (
                _process_snapshot_batch(items[:middle], topics, recency_days, 1, reject, user_profile)
                + _process_snapshot_batch(items[middle:], topics, recency_days, 1, reject, user_profile)
            )
        try:
            raw = call_llm(
                build_snapshot_processing_prompt(items, topics, recency_days, minimal=True, reject=reject, user_profile=user_profile),
                temperature=0.1, max_tokens=2000, json_mode=True,
            )
            return _parse_snapshot_processing(raw, items)
        except Exception as retry_error:
            return [_failed_snapshot_processing(items[0], retry_error)]


def _process_full_text_item(item, topics=None, recency_days=7, reject=None, user_profile=None):
    """Process every segment before allowing a final article-level decision."""
    evidence = item.get("evidence_snapshot") or ""
    chunks = split_evidence_chunks(evidence)
    if not chunks:
        return _process_snapshot_batch([item], topics, recency_days, reject=reject, user_profile=user_profile)[0]

    findings = []
    failed_chunks = 0
    for index, chunk in enumerate(chunks, start=1):
        print(f"[AI 抓取] 全文分段 {index}/{len(chunks)}...")
        try:
            finding = _process_full_text_chunk(item, chunk, index, len(chunks), topics)
            lines = [f"[第 {index}/{len(chunks)} 段] {finding['chunk_summary']}"]
            lines.extend(f"- {point}" for point in finding["evidence_points"])
            if finding["time_evidence"]:
                lines.append(f"- 时间线索：{finding['time_evidence']}")
            if finding["concerns"]:
                lines.append(f"- 注意：{finding['concerns']}")
        except Exception as error:
            failed_chunks += 1
            lines = [f"[第 {index}/{len(chunks)} 段] （本段提取失败：{str(error)[:120]}）",
                     "- 本段未能提取可靠证据，跳过该段"]
        findings.append("\n".join(lines))

    if failed_chunks == len(chunks):
        # 所有段都失败：降级用元数据评估（等价无全文证据），不直接作废
        print(f"[AI 抓取] {len(chunks)} 段全部提取失败，降级用元数据评估")
        return _process_snapshot_batch([item], topics, recency_days, reject=reject, user_profile=user_profile)[0]

    enriched = dict(item)
    enriched["full_text_findings"] = "\n\n".join(findings)
    enriched["evidence_chunk_count"] = len(chunks)
    return _process_snapshot_batch([enriched], topics, recency_days, reject=reject, user_profile=user_profile)[0]


def process_selected_snapshots(items, batch_tag, batch_size=3, topics=None, recency_days=7, reject=None, user_profile=None):
    """Finalize selected articles only after processing their complete text."""
    results = []
    for i, item in enumerate(items, start=1):
        print(f"[AI 抓取] 全文处理文章 {i}/{len(items)}...")
        results.append(_process_full_text_item(item, topics, recency_days, reject=reject, user_profile=user_profile))
        time.sleep(_batch_delay())
    for item in results:
        item["batch_tag"] = batch_tag
    publishable = [item for item in results if item.get("recommendation_level") in PUBLISHABLE_RECOMMENDATIONS]
    return {
        "batch_summary": "",
        "source_summaries": generate_source_summaries(publishable) if publishable else {},
        "items": results,
    }


def build_source_summary_prompt(items_by_source):
    lines = []
    for source, items in items_by_source.items():
        lines.append(f"\n=== {source} ===")
        for idx, item in enumerate(items[:10]):
            title = sanitize_for_llm(item.get('title_zh') or item.get('title', ''))
            summary = sanitize_for_llm(item.get('summary_zh') or item.get('summary', '')[:150])
            lines.append(f"{idx + 1}. {title}\n   {summary}")
    prompt = "请为每个来源生成 3 至 5 句中文趋势总结。输出 JSON 对象，键为来源名，不要输出其他文字。\n" + "\n".join(lines)
    return [{"role": "system", "content": "你是一位科技新闻主编。"}, {"role": "user", "content": prompt}]


def generate_source_summaries(items):
    by_source = {}
    for item in items:
        by_source.setdefault(item.get("source", "Unknown"), []).append(item)
    try:
        summaries = parse_llm_json(call_llm(build_source_summary_prompt(by_source), temperature=0.4, max_tokens=6000, json_mode=True))
    except Exception as error:
        print(f"[LLM Error] 源总结生成失败：{error}")
        summaries = {}
    for source, source_items in by_source.items():
        summaries.setdefault(source, f"{source} 今日收录 {len(source_items)} 条内容。")
    return summaries
