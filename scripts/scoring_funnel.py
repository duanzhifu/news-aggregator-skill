import sys

def evaluate_item_score(item: dict) -> tuple:
    """
    三级评分与防拦截漏斗（严苛 60 分标准）：
    - ≥ 80 分: ⭐ 推荐阅读（高干货，高亮头条与独立笔记）
    - 60 ~ 79 分: 📖 可选阅读（有参考价值，正常入库归档）
    - < 60 分: ❌ 直接拦截（水文、标题党、营销号，打入拒绝集合）
    
    返回值：(score: int, level_label: str, action: str)
    """
    base_score = 70
    
    # 标题党或营销号特征扣分
    title = str(item.get("title", ""))
    summary = str(item.get("summary", ""))
    content = str(item.get("content", ""))
    
    clickbait_words = ["震惊", "速看", "颠覆", "必看", "重磅炸弹", "绝密", "千万别错过"]
    for word in clickbait_words:
        if word in title or word in summary:
            base_score -= 15
            
    # 高价值干货/开源项目/论文加分
    if item.get("fetch_method") in ("agent_browser_dynamic", "github_repo") or "github.com" in item.get("url", ""):
        base_score += 15
    if len(content) > 1000 or len(summary) > 200:
        base_score += 10
    if item.get("engagement_metrics") or item.get("heat"):
        base_score += 10
        
    # 限制在 0-100 范围内
    score = max(0, min(100, base_score))
    
    if score >= 80:
        level_label = "⭐ 推荐阅读"
        action = "recommend"
    elif score >= 60:
        level_label = "📖 可选阅读"
        action = "archive"
    else:
        level_label = "❌ 直接拦截"
        action = "reject"
        
    return score, level_label, action
