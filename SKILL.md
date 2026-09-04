---
name: news-aggregator-skill
description: "Comprehensive news aggregator that fetches, filters, and deeply analyzes real-time content from 53 sources including Hacker News, Lobsters, Dev.to, GitHub, arXiv, Hugging Face Papers, AIHOT, TLDR AI, Import AI, BBC, The Guardian, Al Jazeera, France 24, Reuters fallback, AI Newsletters, WallStreetCN, 少数派, InfoQ 中文, Podcasts, and user-defined OPML feeds. Use when user requests 'daily scans', 'tech news', 'finance updates', 'AI briefings', 'international news', 'deep analysis', or says '如意如意' to open the interactive menu."
---

# News Aggregator Skill

Fetch real-time hot news from 53 sources (including international news + AI curated aggregators + user-defined OPML feeds), generate deep analysis reports in Chinese.

---

## 🔄 Universal Workflow (3 Steps)

**Every** news request follows the same workflow, regardless of source or combination:

### Step 1: Fetch Data

```bash
# Single source
python3 scripts/fetch_news.py --source <source_key> --no-save

# Multiple sources (comma-separated)
python3 scripts/fetch_news.py --source hackernews,github,wallstreetcn --no-save

# All sources (broad scan)
python3 scripts/fetch_news.py --source all --limit 15 --deep --no-save

# With keyword filter (literal comma-separated: "AI,LLM,GPT")
python3 scripts/fetch_news.py --source hackernews --keyword "AI,LLM,GPT" --deep --no-save
```

### Step 2: Generate Report

Read the output JSON and format **every** item using the **Unified Report Template** below. Translate all content to **Simplified Chinese**.

### Step 3: Save & Present

Save the report to `reports/YYYY-MM-DD/<source>_report.md`, then display the full content to the user.

---

## 📰 Unified Report Template

**All sources use this single template.** Show/hide optional fields based on data availability.

```markdown
#### N. [标题 (中文翻译)](https://original-url.com)

- **Source**: 源名 | **Time**: 时间 | **Heat**: 🔥 热度值
- **Links**: [Discussion](hn_url) | [GitHub](gh_url) ← 仅在数据存在时显示
- **Summary**: 一句话中文摘要。
- **Deep Dive**: 💡 **Insight**: 深度分析（背景、影响、技术价值）。
```

### Source-Specific Adaptations

Only the **differences** from the universal template:

| Source                 | Adaptation                                                                                                                                                                                                    |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hacker News**        | **MUST** include `[Discussion](hn_url)` link                                                                                                                                                                  |
| **GitHub**             | Use `🌟 Stars` for Heat, add `Lang` field, add `#Tags` in Deep Dive                                                                                                                                           |
| **Hugging Face**       | Use `🔥 +N` upvotes for Heat, include `[GitHub](url)` if present, write **深度解读** (not just translate abstract)                                                                                            |
| **AIHOT**              | `summary` 已是中文编辑稿，**直接引用**不要再翻译；Heat 字段为空也别造数据；保留 `推荐理由` 风格的一句话点评                                                                                                   |
| **TLDR AI**            | 单条标题往往是多主题混合（`Topic A 💻, Topic B ⚡, Topic C ⛪`），**拆成 bullet 列出每个主题**；`summary` 是 HTML 段落，需要拆出每个主题对应的一两句概述                                                      |
| **Import AI**          | 周刊长文，标题形如 `Import AI 458: 主题1; 主题2; 主题3`。**建议默认配 `--deep`**，否则 RSS summary 只是开头几句；Deep Dive 直接提炼 Jack Clark 的核心观点而非平铺事实                                         |
| **International News** | **MUST** use the Unified Report Template for every item；只使用最近 24h RSS 条目，不用更早新闻 Smart Fill；英文标题与摘要翻译成简体中文，保留原始媒体名与链接；同一事件多家媒体重复时可合并观点但不能合并链接 |
| **Reuters**            | `reuters` 使用 Google News RSS 的 `site:reuters.com` fallback；报告里保留 `Reuters (Google News fallback)` source，不要写成官方公开 RSS                                                                       |

---

## 🛠️ Tools

### fetch_news.py

| Arg         | Description                                      | Default                |
| ----------- | ------------------------------------------------ | ---------------------- |
| `--source`  | Source key(s), comma-separated. See table below. | `all`                  |
| `--limit`   | Max items per source                             | `10`                   |
| `--keyword` | Comma-separated keyword filter                   | None                   |
| `--deep`    | Download article text for richer analysis        | Off                    |
| `--save`    | Force save to reports dir                        | Auto for single source |
| `--outdir`  | Custom output directory                          | `reports/YYYY-MM-DD/`  |

### Available Sources (53 with user OPML)

| Category                | Key              | Name                                                                                |
| ----------------------- | ---------------- | ----------------------------------------------------------------------------------- |
| **Global News**         | `hackernews`     | Hacker News                                                                         |
|                         | `36kr`           | 36氪                                                                                |
|                         | `wallstreetcn`   | 华尔街见闻                                                                          |
|                         | `tencent`        | 腾讯新闻                                                                            |
|                         | `v2ex`           | V2EX                                                                                |
|                         | `producthunt`    | Product Hunt                                                                        |
|                         | `github`         | GitHub Trending                                                                     |
| **Tech Community** (v2) | `lobsters`       | Lobsters                                                                            |
|                         | `devto`          | Dev.to                                                                              |
|                         | `devto_react`    | Dev.to React 专区                                                                   |
|                         | `react_blog`     | React 官方博客                                                                      |
| **AI/Tech**             | `huggingface`    | HF Daily Papers                                                                     |
|                         | `arxiv`          | arXiv (cs.AI/cs.CL/cs.LG, v2)                                                       |
|                         | `ai_newsletters` | All AI Newsletters (aggregate)                                                      |
|                         | `openai`         | OpenAI 官方博客                                                                     |
|                         | `anthropic`      | Anthropic 官方博客                                                                  |
|                         | `bensbites`      | Ben's Bites                                                                         |
|                         | `interconnects`  | Interconnects (Nathan Lambert)                                                      |
|                         | `oneusefulthing` | One Useful Thing (Ethan Mollick)                                                    |
|                         | `chinai`         | ChinAI (Jeffrey Ding)                                                               |
|                         | `memia`          | Memia                                                                               |
|                         | `aitoroi`        | AI to ROI                                                                           |
|                         | `kdnuggets`      | KDnuggets                                                                           |
| **Chinese** (v2)        | `sspai`          | 少数派                                                                              |
|                         | `infoq_cn`       | InfoQ 中文站（RSS 只给标题，**推荐配 `--deep`** 拿正文）                            |
|                         | `juejin`         | 掘金热榜                                                                            |
| **AI Curated** (v3)     | `aihot`          | AIHOT 中文 AI 精选（跨源 + 中文编辑稿）                                             |
|                         | `tldr_ai`        | TLDR AI 英文日刊                                                                    |
|                         | `import_ai`      | Import AI by Jack Clark 周刊（**推荐 `--deep`**）                                   |
| **International News**  | `international`  | 最近 24h 国际新闻聚合（BBC / Guardian / Al Jazeera / France 24 / Reuters fallback） |
|                         | `bbc_top`        | BBC Top News (24h)                                                                  |
|                         | `bbc_world`      | BBC World (24h)                                                                     |
|                         | `bbc_chinese`    | BBC 中文 (24h)                                                                      |
|                         | `guardian_world` | The Guardian World (24h)                                                            |
|                         | `aljazeera`      | Al Jazeera (24h)                                                                    |
|                         | `france24`       | France 24 (24h)                                                                     |
|                         | `reuters`        | Reuters via Google News RSS fallback (24h)                                          |
| **Podcasts**            | `podcasts`       | All Podcasts (aggregate)                                                            |
|                         | `lexfridman`     | Lex Fridman                                                                         |
|                         | `80000hours`     | 80,000 Hours                                                                        |
|                         | `latentspace`    | Latent Space                                                                        |
| **Essays**              | `essays`         | All Essays (aggregate)                                                              |
|                         | `paulgraham`     | Paul Graham                                                                         |
|                         | `waitbutwhy`     | Wait But Why                                                                        |
|                         | `jamesclear`     | James Clear                                                                         |
|                         | `farnamstreet`   | Farnam Street                                                                       |
|                         | `scottyoung`     | Scott Young                                                                         |
|                         | `dankoe`         | Dan Koe                                                                             |
| **Custom** (v2)         | `user`           | Your OPML feeds (see below)                                                         |
| **Social**              | `douyin`         | Douyin technical-content discovery                                                  |
|                         | `bilibili`       | Bilibili technical-content discovery                                                |

### 自定义订阅源 (User OPML)

把你常看的 RSS/Atom 源写进 OPML，`--source user` 即可统一抓取。

**1. 放置 OPML 文件**（按优先级查找）：

- `~/.config/news-aggregator/user_sources.opml`（推荐，跨 skill 复用）
- `<skill_root>/user_sources.opml`（本仓库内）

**2. 文件格式**：标准 OPML 2.0，可直接从 Feedly / Inoreader / NetNewsWire 导出。参考 `user_sources.opml.example`：

```xml
<outline type="rss" text="Simon Willison" title="Simon Willison"
         xmlUrl="https://simonwillison.net/atom/everything/" />
```

只 `xmlUrl` 必填，其它可选。

**3. 运行**：`python3 scripts/fetch_news.py --source user --limit 15`

### Obsidian Daily Briefing

Write a daily briefing and individual article notes to an Obsidian Vault:

```bash
py scripts/push_to_obsidian.py --vault "D:/Obsidian/自动信息获取"
```

默认来源为 `juejin,devto,github,openai,bilibili`，每源上限为 15 条。可用 `--source` 覆盖默认来源；如需单独抓取抖音，可显式指定 `--source douyin`。日报默认让 AI 先从来源元数据中选择值得打开的候选，再使用 `--evidence-mode snapshot` 读取完整 DOM 正文并逐段审阅；`--deep` 在此基础上将原文正文写入笔记。社交来源的搜索关键词：bilibili 复用 `user_interests.json` 的 `topics`（经 `NEWS_AGGREGATOR_TOPICS` 环境变量透传），douyin 读 `config/social_sources.json`。

The output is `<Vault>/自动获取信息/YYYY-MM-DD/`: article notes are stored in `信息源/<来源中文名>/`, and `今日总结.md` at the date root is regenerated from every article already stored for that day. AI 拒绝结果写入同级的 `拒绝集合/`：每日页面展示全部拒绝与待复核项，`_拒绝索引.json` 供程序执行确定性拒绝的历史去重。

---

## ⚠️ Rules (Strict)

1. **Language**: ALL output in **Simplified Chinese (简体中文)**. Keep well-known English proper nouns (ChatGPT, Python, etc.).
2. **Time**: **MANDATORY** field. Never skip. If missing in JSON, mark as "Unknown Time". Preserve "Real-time" / "Today" / "Hot" as-is.
3. **Anti-Hallucination**: Only use data from the JSON. Never invent news items. Use simple SVO sentences. Do not fabricate causal relationships.
4. **Keyword Filter**: `--keyword` takes literal comma-separated terms (e.g. `"AI,LLM,GPT"`), matched as word-boundary regex. No automatic expansion.
5. **Time Window**（仅命令行 `fetch_news.py` 生效）：默认只保留发布时间在最近 72 小时内的条目（可用 `--hours N` 调整）；发布时间缺失或无法解析的条目不进入默认结果。掘金热榜会优先从文章页面的 `time[datetime]` 读取发布时间；GitHub Trending 当前热榜项目是例外，即使最近 push 超过 72 小时也保留，但必须有 GitHub API 提供的 `pushed_at`。推送流水线 `push_to_obsidian.py` 固定 `--skip-time-filter`（`preserve_raw_time=True`），时间语义由 AI 判断而非硬窗口。
6. **Smart Fill**: If results < 5 items in a time window, supplement only with other items from the same time window and mark them with ⚠️. 不再使用更早内容补齐；International News sources remain a hard 24h window.
6. **Save**: Always save report to `reports/YYYY-MM-DD/` before displaying.

---

## 📋 Interactive Menu

When the user says **"如意如意"** or asks for "menu/help":

1. Read `templates.md`
2. Display the menu
3. Execute the user's selection using the **Universal Workflow** above

---

## 📱 Obsidian 集成（定时写入）

### 整体流程

```
[Windows 任务计划] 每天定时执行
    ↓
[fetch_news.py 保留原始时间并抓取来源列表]
    ↓
[AI 判断时间语义、过滤低价值候选并选择要打开的文章]
    ↓
[读取入选文章完整 DOM 正文并分段审阅]
    ↓
[AI 一次完成翻译、总结与最终质量确认]
    ↓
[push_to_obsidian.py 写入文章、日报与拒绝集合]
    ↓
[你在 Obsidian Vault 阅读和检索]
```

### Obsidian 文章元数据

| 字段名       | 类型   | 说明                                         |
| ------------ | ------ | -------------------------------------------- |
| 中文标题 | 文本 | 翻译后的中文标题 |
| 原文标题 | 文本 | 原始标题 |
| 文章总结 | 文本 | 文章级中文总结或明确的回退说明 |
| 来源 | 文本 | 文章来源 |
| 分类 | 文本 | ai / programmer / github / frontend / social / other |
| 链接 | URL | 原文链接 |
| 热度 | 数字 | HN points / GitHub stars / 热榜排名等 |
| 发布时间 | 日期 | 原始发布时间 |
| 抓取日期 | 日期 | 写入 Vault 的日期 |
| 标签 | 列表 | 用于 Obsidian 检索 |

### 核心脚本

| 脚本 | 功能 |
| --- | --- |
| `scripts/fetch_news.py` | 抓取一个或多个信息源，输出 JSON |
| `scripts/push_to_obsidian.py` | 抓取、翻译、生成总结并写入 Obsidian |
| `scripts/reprocess_articles.py` | 重新处理已经写入 Obsidian 的文章 |
| `scripts/rebuild_content.py` | 从已有文章重建日报内容 |

### 去重规则

已发布文章按 `规范化 URL` 跨日期去重（URL identity wins）；仅当条目无可靠 URL 时，才回退到 `(标准化标题 + 来源)`。AI 的确定性拒绝、正文乱码、页面超时、证据不足和 AI 调用失败属于临时结果，只展示在每日拒绝页，不进入永久去重索引。

### 定时运行

Windows 使用 `scripts/run_daily.ps1` 作为任务计划程序入口。运行前检查其中的仓库、Python 和 Vault 路径；脚本会执行已配置的默认日报来源。

### 内容质量评估与推荐过滤

`push_to_obsidian.py` 会保留各来源的原始时间字段并先进行历史去重。AI 根据标题、摘要、来源信号、原始时间和主题判断是否值得打开；未入选内容直接进入审计。入选项默认读取完整 DOM 正文，每个段落块先提取可验证证据，再基于全部分段结论完成中文标题、摘要、最终质量判断和时间确认。高价值旧文章、当前热榜及持续更新项目仍可保留。

日报流程不使用统一的程序时间解析器提前淘汰候选。AI 必须返回 `published_at`、`time_kind`、`time_confidence` 和 `time_evidence`，区分首次发布、更新、GitHub 最近推送、榜单采集及未知时间。缺失时间不能单独成为拒绝理由；程序只校验结构并保存判断证据。

默认主题覆盖前端工程、人工智能与 AI 工程、后端开发、开发工具和开源项目。可用 `--topics "LLM,Agent,RAG,端侧 AI"` 传入自定义主题。

AI 根据用户主题、页面快照中的可验证证据、内容完整度、时间语义自主给出 0-100 质量分及推荐等级（质量分不使用固定权重）；同来源互动信号由固定公式计算互动辅助分（engagement_score，见 scoring_rules.py），两者独立写入笔记。推荐等级只能是 `strongly_recommended`、`optional` 或 `not_recommended`，且必须附带具体理由。

只有前两档会写入文章和日报。不推荐或 AI 处理失败的内容不进入正常日报，而是写入 `<Vault>/自动获取信息/拒绝集合/`；其中确定性拒绝同时进入 `_拒绝索引.json`，临时失败保留后续重试机会。AI 必须返回逐条 `evidence_points`，文章笔记和拒绝页展示真实判断依据，不再使用统一证据套话。文章 frontmatter 包含推荐结果、快照状态以及 AI 的时间类型、时间置信度和时间证据。

最终评估沿用批次拆分和单篇重试逻辑。最终评估失败时不进入正常日报，等级为 `evaluation_failed`，状态为 `failed`，并写入本地复核日志。

---

### 社交平台技术内容

新增平台 key：`douyin`、`bilibili`。默认关键词配置位于
`config/social_sources.json`，也可用 `NEWS_AGGREGATOR_SOCIAL_CONFIG` 指定本地配置文件。
Bilibili 已纳入常规 Obsidian 日报的默认来源列表；抖音仍可通过显式 `--source douyin` 单独抓取。


```bash
python scripts/fetch_news.py --source douyin,bilibili --limit 5 --keyword "前端,AI,软件工程"
```

适配器优先使用显式配置的 JSON API：`SOCIAL_API_URL_DOUYIN`、`SOCIAL_API_URL_BILIBILI`（当前仅这两个平台已接入）；可选的
`SOCIAL_API_TOKEN_<PLATFORM>` 会作为 Bearer Token 发送。未配置或调用失败时，降级到
Playwright 公开搜索页。浏览器会话目录可通过 `NEWS_AGGREGATOR_BROWSER_PROFILE` 配置，
不要把 Cookie、Token 或会话目录提交到仓库。

社交平台抓取标题、简介、作者、发布时间和可见互动数据。Bilibili 和抖音只保留真实视频链接，
不下载视频；视频内容已接入两级转录：①平台字幕 API（bilibili，cookie）②Groq Whisper 语音兜底（fetch_news 视频分支，method="groq_transcript"）。

GitHub Trending 继续保留热榜排名，并通过 GitHub API 补充仓库最近一次 `pushed_at`；笔记中将其标为“最近推送时间”，不将其误称为发布时间。普通 GitHub 搜索结果仍遵守默认 24 小时窗口。

## Requirements

- Python 3.10+, `pip install -r requirements.txt`
- Playwright（仅深度抓取或需要浏览器的来源）：`playwright install chromium`
- Obsidian Vault 路径：通过 `--vault` 或 `OBSIDIAN_VAULT_PATH` 指定
