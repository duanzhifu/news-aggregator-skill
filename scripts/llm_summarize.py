"""Use an LLM to translate news metadata and generate daily summaries."""
import json
import os
import re
import time
from datetime import datetime, timezone

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
    parsed = json.loads(extract_json_block(raw))
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


def normalize_topics(topics=None):
    if topics is None:
        return list(DEFAULT_RECOMMENDATION_TOPICS)
    return [str(topic).strip() for topic in topics if str(topic).strip()]


def build_candidate_selection_prompt(items, topics=None, recency_days=7, now_iso=None):
    """Ask AI which metadata candidates deserve a page visit."""
    topics_text = ", ".join(normalize_topics(topics)) or "通用前沿技术"
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
近期优先参考窗口：{recency_days} 天，但高价值旧文章、当前热榜或持续更新的项目可以保留。

逐条完成：
1. 理解不同来源的时间字段，区分发布、更新、仓库最近推送和榜单采集时间；不得把更新或推送时间伪装成首次发布时间。
2. 结合标题、摘要、来源、互动信号和时间语义判断是否值得打开页面。
3. 广告、推广、标题党、重复转载、与主题无关或证据明显不足的社交内容应拒绝。
4. 时间缺失不能单独成为拒绝理由；当前热榜可保留并标记 ranking_observed 或 unknown。
5. 不得猜测没有证据的日期。无法可靠归一化时 published_at 返回空字符串。

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
    parsed = json.loads(extract_json_block(raw))
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


def _select_batch(items, topics=None, recency_days=7, was_retried=False, split_depth=0):
    try:
        raw = call_llm(
            build_candidate_selection_prompt(items, topics, recency_days),
            temperature=0.1, max_tokens=3500, json_mode=True,
        )
        return _parse_candidate_selection(raw, items)
    except Exception as error:
        print(f"[LLM Warning] {len(items)} 条 AI 准入失败：{error}")
        _retry_backoff()
        if len(items) > 1 and split_depth == 0:
            middle = len(items) // 2
            return (
                _select_batch(items[:middle], topics, recency_days, True, 1)
                + _select_batch(items[middle:], topics, recency_days, True, 1)
            )
        return [_failed_selection(items[0], error)]


def select_candidates(items, batch_size=5, topics=None, recency_days=7):
    """Use metadata and raw source times to decide which pages AI should open."""
    prepared = attach_engagement_scores(attach_source_keys(items))
    results = []
    for i in range(0, len(prepared), batch_size):
        batch = prepared[i:i + batch_size]
        print(f"[AI 抓取] 候选准入批次 {i // batch_size + 1}/{(len(prepared) - 1) // batch_size + 1}，{len(batch)} 条...")
        results.extend(_select_batch(batch, topics, recency_days))
        time.sleep(_batch_delay())
    return results


def build_snapshot_processing_prompt(items, topics=None, recency_days=7, minimal=False):
    """Build one request for translation, summary, final quality, and time confirmation."""
    topics_text = ", ".join(normalize_topics(topics)) or "通用前沿技术"
    lines = []
    for idx, raw_item in enumerate(items):
        item = sanitize_item_for_llm(raw_item)
        evidence = item.get("evidence_snapshot") or item.get("summary") or item.get("description") or ""
        limit = 1200 if minimal else 4000
        lines.append("\n".join([
            f"[{idx}] Source: {item.get('source', '')}",
            f"Original title: {item.get('title', '')}",
            f"Raw time: {item.get('time') or item.get('published') or ''}",
            f"Preliminary normalized time: {item.get('published_at', '')}",
            f"Preliminary time kind/confidence: {item.get('time_kind', 'unknown')}/{item.get('time_confidence', 'unknown')}",
            f"Snapshot status: {item.get('evidence_status', 'unknown')}",
            f"Snapshot type: {item.get('evidence_method', 'feed_metadata')}",
            f"Snapshot: {evidence[:limit]}",
        ]))
    prompt = f"""请基于短文本快照完成最终编辑判断。用户关注主题：{topics_text}，近期优先参考窗口为 {recency_days} 天。
快照不是完整正文，不得补充快照中没有的实现、实验、因果关系或结论。一次性返回自然中文标题、2 至 3 句中文总结、质量判断及时间确认。明显广告、低价值转载、标题党或快照与标题不符时可在最终阶段拒绝。
发布时间沿用可验证证据；快照提供更明确时间时可修正初步结果。不得把更新时间或仓库推送时间写成首次发布时间，也不得猜测缺失日期。

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
    parsed = json.loads(extract_json_block(raw))
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


def _process_snapshot_batch(items, topics=None, recency_days=7, split_depth=0):
    try:
        raw = call_llm(
            build_snapshot_processing_prompt(items, topics, recency_days),
            temperature=0.2, max_tokens=4500, json_mode=True,
        )
        return _parse_snapshot_processing(raw, items)
    except Exception as error:
        print(f"[LLM Warning] {len(items)} 条快照处理失败：{error}")
        _retry_backoff()
        if len(items) > 1 and split_depth == 0:
            middle = len(items) // 2
            return (
                _process_snapshot_batch(items[:middle], topics, recency_days, 1)
                + _process_snapshot_batch(items[middle:], topics, recency_days, 1)
            )
        try:
            raw = call_llm(
                build_snapshot_processing_prompt(items, topics, recency_days, minimal=True),
                temperature=0.1, max_tokens=1200, json_mode=True,
            )
            return _parse_snapshot_processing(raw, items)
        except Exception as retry_error:
            return [_failed_snapshot_processing(items[0], retry_error)]


def process_selected_snapshots(items, batch_tag, batch_size=3, topics=None, recency_days=7):
    """Finalize AI-selected snapshot items in small, isolated batches."""
    results = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        print(f"[AI 抓取] 快照处理批次 {i // batch_size + 1}/{(len(items) - 1) // batch_size + 1}，{len(batch)} 条...")
        results.extend(_process_snapshot_batch(batch, topics, recency_days))
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
        summaries = json.loads(extract_json_block(call_llm(build_source_summary_prompt(by_source), temperature=0.4, max_tokens=4000, json_mode=True)))
    except Exception as error:
        print(f"[LLM Error] 源总结生成失败：{error}")
        summaries = {}
    for source, source_items in by_source.items():
        summaries.setdefault(source, f"{source} 今日收录 {len(source_items)} 条内容。")
    return summaries
