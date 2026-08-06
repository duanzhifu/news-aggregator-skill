"""Normalize source metadata and calculate source-local engagement signals."""
import re
SOURCE_KEY_ALIASES = {
    "dev.to": "devto",
    "devto": "devto",
    "github trending": "github",
    "githubtrending": "github",
    "github": "github",
    "openai blog": "openai",
    "openai": "openai",
    "掘金热榜": "juejin",
    "juejin": "juejin",
    "douyin": "douyin",
    "weibo search": "weibo_search",
    "weibo_search": "weibo_search",
    "wechat official account": "wechat",
    "wechat": "wechat",
}

ENGAGEMENT_ALIASES = {
    "浏览": ("view", "views", "metric_view", "metric_views", "metric_hits", "metric_reads"),
    "点赞": ("like", "likes", "metric_like", "metric_likes", "metric_thumbsup", "metric_upvotes"),
    "喜欢": ("heart", "hearts", "metric_heart", "metric_hearts", "claps", "metric_claps"),
    "评论": ("comment", "comments", "metric_comment", "metric_comments", "replies", "metric_replies", "responses", "metric_responses"),
    "收藏": ("collect", "favorite", "favorites", "bookmarks", "metric_collect", "metric_favorites", "metric_bookmarks", "metric_saves"),
    "转发": ("share", "shares", "repost", "reposts", "metric_share", "metric_shares"),
    "星标": ("star", "stars", "metric_stars"),
    "分叉": ("fork", "forks", "metric_forks"),
}


def normalize_source_key(item):
    """Return a stable source key without relying on display names downstream."""
    value = item.get("source_key") or item.get("source") or ""
    normalized = " ".join(str(value).strip().lower().split())
    return SOURCE_KEY_ALIASES.get(normalized, normalized.replace(" ", ""))


def attach_source_keys(items):
    """Copy items and fill source_key for scoring and later export."""
    result = []
    for item in items:
        enriched = dict(item)
        enriched["source_key"] = normalize_source_key(enriched)
        result.append(enriched)
    return result


def _parse_metric_value(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"([\d.]+)\s*([km]?)", str(value).lower().replace(",", ""))
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    return number * (1000 if suffix == "k" else 1000000 if suffix == "m" else 1)


def _first_metric(item, keys):
    for key in keys:
        value = _parse_metric_value(item.get(key))
        if value is not None:
            return value
    return None


def engagement_metrics(item):
    """Extract only engagement data actually present on the source item."""
    metrics = {}
    for label, keys in ENGAGEMENT_ALIASES.items():
        value = _first_metric(item, keys)
        if value is not None:
            metrics[label] = value

    source_key = normalize_source_key(item)
    heat = _parse_metric_value(item.get("heat"))
    if heat is not None and source_key in {"devto", "github", "douyin", "weibo_search"}:
        metrics.setdefault("热度", heat)
    hot_rank = _parse_metric_value(item.get("hot_rank"))
    if hot_rank is not None and source_key == "juejin":
        metrics["热榜位次"] = hot_rank
    return metrics


def attach_engagement_scores(items):
    """Attach source-local engagement scores without comparing platforms."""
    grouped = {}
    for item in items:
        grouped.setdefault(normalize_source_key(item), []).append(item)

    results = []
    for group in grouped.values():
        metric_sets = [engagement_metrics(item) for item in group]
        for index, item in enumerate(group):
            enriched = dict(item)
            metrics = metric_sets[index]
            component_scores = []
            for label, value in metrics.items():
                ranked = [(position, values.get(label)) for position, values in enumerate(metric_sets) if label in values]
                if len(ranked) == 1:
                    component_scores.append(50)
                    continue
                ranked.sort(key=lambda entry: entry[1], reverse=label != "热榜位次")
                positions = [position for position, _ in ranked]
                rank = positions.index(index)
                component_scores.append(round(100 * (len(ranked) - 1 - rank) / (len(ranked) - 1)))
            enriched["engagement_metrics"] = metrics
            enriched["engagement_score"] = round(sum(component_scores) / len(component_scores)) if component_scores else None
            results.append(enriched)
    return results
