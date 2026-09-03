---
name: translate-summarize-skill
description: "翻译与总结 Skill。将 news-aggregator-skill 抓取的原始 JSON 数据翻译为中文，并生成文章级、源级、批次级三层总结。用于无人值守定时任务或手动管道流。当用户请求「翻译总结」、「生成中文报告」或相关工作流触发时使用。"
---

# Translate & Summarize Skill

将 AI 入选文章的来源元数据与完整 DOM 正文的分段证据转换为中文标题、摘要、时间判断和质量结论，输出供本地 Obsidian 发布流程读取的结构化 JSON。

---

## 📥 输入格式（JSON）

来自 `fetch_news.py --no-save` 或其他抓取脚本的标准输出：

```json
[
  {/
    "source": "GitHub Trending",
    "title": "owner/repo - Project description in English",
    "url": "https://github.com/...",
    "heat": 12345,
    "time": "Today",
    "content": "Original English content or description..."
  },
  ...
]
```

---

## 📤 输出格式（JSON）

```json
{
  "batch_summary": "",
  "source_summaries": {
    "GitHub Trending": "GitHub 今日三大趋势：① ... ② ... ③ ...",
    "微博热搜": "微博热搜今日以 ... 为主 ...",
    "腾讯新闻": "腾讯新闻今日 ... "
  },
  "items": [
    {
      "title_zh": "中文标题（翻译后）",
      "title_raw": "原始英文/中文标题",
      "source": "GitHub Trending",
      "category": "tech",
      "url": "https://github.com/...",
      "heat": 12345,
      "published_at": "2026-07-27T09:30:00+08:00",
      "time_kind": "published",
      "time_confidence": "high",
      "time_evidence": "页面列表显示 2026-07-27 09:30",
      "summary_zh": "2-3句话总结，说明主题、做法和价值，用于快速判断值不值得读。",
      "evidence_method": "bounded_dom_text",
      "evidence_status": "fetched",
      "quality_score": 82,
      "recommendation_level": "strongly_recommended",
      "recommendation_reason": "快照包含可验证的技术信息。",
      "batch_tag": "2026-07-27_tech",
      "translation_status": "success",
      "tags": ["AI", "开源"]
    }
  ]
}
```

---

## 🔄 工作流程

### Step 1: AI 候选准入

接收 `fetch_news.py --skip-time-filter --no-save` 的原始候选。程序先跳过已经发布及历史确定性拒绝的条目；AI 再结合来源、标题、摘要、互动指标和原始时间字段，解释时间语义并选择值得打开页面的文章。每个判断必须返回具体 `evidence_points`；拒绝项还需区分可长期去重的 `definitive` 与需要以后重试的 `transient`。

### Step 2: 读取入选项快照

默认仅对入选项读取完整的 DOM 正文。完整文本保存在本地运行报告中供失败重试复用，不作为原文正文写入文章笔记。

### Step 3: 快照处理与错误隔离

由 `scripts/llm_summarize.py` 中的 `process_selected_snapshots` 执行：

1. **全文分段调用**：按段落拆分全文，每段先提取可验证事实，再用所有分段结论完成最终翻译、摘要、时间确认和质量判断。
2. **单段重试隔离**：单段解析或 LLM 返回失败时只重试该段；仍失败的文章标为临时失败，不会以不完整正文做出推荐判断。
3. **全文可审计**：运行报告保留完整正文及其长度，最终判断保留具体证据点。
4. **时间可审计**：输出标准时间、时间类型、置信度和实际证据；不得把更新或推送时间伪装成首次发布时间，也不得猜测缺失日期。

### Step 4: 两层结构化总结

1. **文章总结 (`summary_zh`)**：每篇 2-3 句中文，说明主题、做法和价值。
2. **源总结 (`source_summaries`)**：按来源聚合，调用 LLM 生成 3-5 句的趋势总结。

### Step 5: 结构化输出

输出最终的 JSON 结构，供下游 `push_to_obsidian.py` 等脚本处理并归档至 Obsidian Vault。

---

## 📋 翻译与总结核心规则

| 规则项       | 说明                                                                               |
| ------------ | ---------------------------------------------------------------------------------- |
| **标题翻译** | 直译为主，保留专业技术词汇及项目名（如 React、Vue、GitHub、Python、API、LLM 等）。 |
| **禁止幻觉** | 不得添加原文没有的内容，不复述网页导航、登录提示、或截断无关噪音。                 |
| **文章总结** | 以用户视角写，2-3 句，明确指出"做什么的"与"对谁有价值"。                           |
| **证据边界** | DOM 快照或 RSS 摘要不是完整正文，不得推断输入之外的实现、实验或结论。              |
| **容错处理** | 记录选择、翻译和评估状态；最终失败项进入 Obsidian 每日拒绝页，但不进入永久去重。   |

---

## 📋 总结生成规则

| 层级                        | 长度   | 受众目标                       |
| --------------------------- | ------ | ------------------------------ |
| 文章总结 (`summary_zh`)     | 2-3 句 | 快速扫一眼判断要不要点进去阅读 |
| 源总结 (`source_summaries`) | 3-5 句 | 了解某个特定来源的整体热点趋势 |

---

## 🔧 常见分类与源映射

- `tech`: `github`, `hackernews`, `user` 等技术源
- `ai`: `huggingface`, `arxiv`, `import_ai`, `tldr_ai`, `aihot` 等 AI 专区
- `finance`: `36kr`, `wallstreetcn` 等财经创投源
- `social`: `douyin`, `bilibili`, `tencent` 等热搜社会源
- `international`: `bbc_top`, `bbc_world`, `guardian_world` 等国际新闻源

---

## 🏷️ 标签规则

根据正文/标题关键词自动打标：

- **AI**: `AI`, `LLM`, `GPT`, `Claude`, `Agent`, `RAG`, `diffusion`
- **开源**: `开源`, `open source`, `MIT`, `Apache`
- **创业**: `创业`, `startup`, `融资`, `funding`
- **金融**: `金融`, `finance`, `stock`, `market`
- **安全**: `安全`, `security`, `hack`, `vulnerability`
- **移动**: `移动`, `iOS`, `Android`, `app`
- **日前**: `前端`, `frontend`, `React`, `Vue`
