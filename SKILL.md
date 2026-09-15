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

> **按主题全网动态搜索**（非固定源，如「搜索 DeepSeek」）不经过 `--source`，完整操作见 `.agent/dynamic-search/SKILL.md`：
> 先看先评：`py scripts/push_to_obsidian.py --topics "DeepSeek" --dynamic-limit 5 --assess-only`（搜 + AI 评估出表，不写库不落盘）；写入用 `--add-url <URL>`，写入+收藏用 `--add-url <URL> --saved`。

### Step 2: Generate Report

Read the output JSON and format **every** item using the **Unified Report Template** below. Translate all content to **Simplified Chinese**.

### Step 3: Save & Present

Save raw JSON to `reports/YYYY-MM-DD/`（单源默认自动保存；`--no-save` 只输出 stdout），then display the full content to the user.

---

## 📰 Unified Report Template

**All sources use this single template.** Show/hide optional fields based on data availability.

```markdown
#### N. [标题 (中文翻译)](https://original-url.com)

- **Source**: 源名 | **Time**: 时间 | **Heat**: 🔥 热度值
- **Links**: [Discussion](hn_url) ← 仅在数据存在时显示（如 Hacker News 讨论帖；GitHub 项链接即 `url` 本身，无需额外字段）
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

### fetch_dynamic_search.py（动态搜索：按主题全网搜，非固定源）

触发：用户说「搜索 xxx」「全网搜 xxx」「针对 xxx 主题搜一下」。**完整操作手册见 `.agent/dynamic-search/SKILL.md`**（命令 / 引擎链 / L0 硬挡 / 多义词坑 / 配置键）。

速查：先看先评 `py scripts\push_to_obsidian.py --topics "主题" --dynamic-limit 5 --assess-only`（不写库）；写入 `--add-url <URL>`、写入+收藏 `--add-url <URL> --saved`，可多传。

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
|                         | `latentspace_ainews` | Latent Space AINews (swyx)                                                     |
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
|                         | `youtube_tech`   | YouTube 科技频道                                                                    |

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

默认来源为 `juejin,devto,github,openai,bilibili`，每源上限为 15 条。可用 `--source` 覆盖默认来源；如需单独抓取抖音，可显式指定 `--source douyin`。日报默认让 AI 先从来源元数据中选择值得打开的候选，再使用 `--evidence-mode snapshot` 读取完整 DOM 正文并逐段审阅；`--deep` 在此基础上将原文正文写入笔记。社交来源：bilibili 复用 `user_interests.json` 的 `topics`（经 `NEWS_AGGREGATOR_TOPICS` 环境变量透传），搜索 URL 模板内置在 `scripts/social_platforms.py`；douyin 已停用。

动态搜索引擎链（AnySearch 主 + Bing 兜底）与诊断详见 `.agent/dynamic-search/SKILL.md`；日志特征 `[AnySearch]`（成功）/`[AnySearch Error]`（失败）/`[DynamicSearch]`（Bing 兜底）。

`user_interests.json` 的 `search_query_optimization`（默认 `false`）控制**搜索词 LLM 消歧**：开启时每个 topic 先经 LLM 消歧为 1~3 个变体、全搜合并（多变体每变体降为 2 条），用于英文多义词（如 trellis）防错义项；默认关闭零成本，仅在动态搜索英文多义词 topic 翻车时开启。消歧失败自动回退原词直搜，不影响主流程。

### 用户画像注入（评估个性化）

每日拉取前，管线从主库 `D:\Obsidian\智能知识库` 读取 3 份材料（个人档案.md + 能力地图.md + 最近 3 篇项目进度按日纪要），由 LLM 提炼成一段 ~1200 字的「我是谁 / 我在做什么 / 我现在需要什么」画像，注入 AI 候选准入（`build_candidate_selection_prompt`）与最终推荐（`build_snapshot_processing_prompt`）两个判断层；**纯事实提取段不注入**（省 token）。

**缓存机制**：材料 sha256 不变 → 延用 `reports/user_profile_cache.json` 缓存画像（零 LLM 调用）；材料变化才重新提炼并更新缓存 + 写当日 `reports/<日期>/user_profile.md` 快照。⚠️ 修改提炼 prompt 语义后必须删除缓存文件（缓存键是材料哈希，不含 prompt 版本）。画像失败自动回退按原 topics 判断，不阻断拉取。管线只读主库、不写 Obsidian 库。

### 手动收藏（--add-url）与收藏集合

除在 `今日总结.md` 表格末列勾选「是否收藏」外，可直接命令收藏：

```
py scripts\push_to_obsidian.py --vault <信息流库根> --add-url <URL> [--note 理由] [--saved]
```

- `--add-url` 可重复多传；默认先 AI 判断值不值得看，值得才生成笔记并进当天总结；`--saved` 跳过 AI 判断直接写入收藏集合（kind=manual）并生成笔记。
- 笔记落 `信息源/<站点域名>/`，frontmatter `收藏理由` 字段可随时补写「为什么收藏它」。
- 收藏集合 = `自动获取信息/收藏集合/收藏.md`（**四列：收藏时间 | 来源 | 文章 | 收藏理由**）+ `_收藏索引.json`，跨日汇总所有勾选；`收藏时间` = 首次观察到勾选的扫描日 **-1 天**（粘住不回跳）。取消勾选会从集合删除。
- 收藏时间口径已拍板：扫描日-1 是唯一方案（勾选时刻对每日批处理不可观测；仅「扫描当天 08:40 前勾选」会早 1 天，属已接受的罕见误差）。

### user_interests.json 配置键

> 首次使用：复制 `user_interests.json.example` → `user_interests.json` 后按需修改（`user_interests.json` 已被 .gitignore 忽略，不会提交个人兴趣；`_` 开头的键是注释，程序会忽略）。

| 键 | 作用 | 生效点 |
| --- | --- | --- |
| `topics` | 动态搜索主题，经 `--topics` 传入（bilibili 关键词同源） | `push_to_obsidian.py` |
| `reject` | 主题拒绝词，注入 AI 准入与最终推荐 prompt | `llm_summarize.py` |
| `daily_sources` | 定时任务默认来源（`--source`），当前 `juejin,devto,github,openai,bilibili` | `run_daily.py` → `--source` |
| `limit_per_topic` | 动态搜索每主题条数（当前 5），经 `--dynamic-limit` 注入；CLI 显式传参优先 | `run_daily.py` → `--dynamic-limit` → `fetch_dynamic_search_news(limit_per_topic=)` |
| `limit_per_source` | 每日定时任务每源抓取条数（当前 15），经 `--limit` 注入；CLI 显式传参优先 | `run_daily.py` → `--limit` → `push_to_obsidian(limit=)` |
| `search_query_optimization` | 搜索词 LLM 消歧开关（默认 false，见上） | `push_to_obsidian.py` |
| `block_url_patterns` | L0 零成本硬挡：匹配 URL **path** 的动态项（如 `/docs/`、`/tutorials/`），不进 LLM | `apply_zero_cost_rules` |
| `block_hosts` | L0 零成本硬挡：匹配 hostname（精确或 `.host` 子域）的动态项（当前 8 条域名黑名单） | `apply_zero_cost_rules` |

> 过滤只作用于动态搜索项，RSS 固定源全部保留；`block_hosts` 语义：`en.wikipedia.org` 挡本域及子域（`blog.en.wikipedia.org`），但 `en.m.wikipedia.org` 是 `m.wikipedia.org` 子域、与 `en` 平级不命中——要挡整站需加根域（如 `wikipedia.org`）。

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
4. 对带「风格指引」标注的菜单项（早报 29–33；2 号 🐙 开源趋势 → `instructions/briefing_github.md`）：先读取对应的 `instructions/briefing_*.md`，按其 Focus Areas / Report Structure / Anti-Laziness 约束组织输出。

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

### 去重规则

已发布文章按 `规范化 URL` 跨日期去重（URL identity wins）；仅当条目无可靠 URL 时，才回退到 `(标准化标题 + 来源)`。AI 的确定性拒绝、正文乱码、页面超时、证据不足和 AI 调用失败属于临时结果，只展示在每日拒绝页，不进入永久去重索引。

### 定时运行

跨平台入口为 `scripts/run_daily.py`（Windows / macOS / Linux 通用；自动读 skill 根配置拼
`--source/--topics/--dynamic-limit`，日志写 `logs/daily_task.log`，Vault 无效或指向主库
直接终止）。Windows 计划任务历史配置可继续指向 `scripts/run_daily.ps1`（薄壳转发），
新部署建议直接指向 `run_daily.py`。调度器随平台任选（schtasks / launchd / cron /
Hermes cron / 手动），只做一件事：到点执行 `run_daily.py`。运行前检查 `run_daily.py`
读到的 paths.json / 环境变量路径；脚本会执行已配置的默认日报来源。

### 内容质量评估与推荐过滤

`push_to_obsidian.py` 会保留各来源的原始时间字段并先进行历史去重。AI 根据标题、摘要、来源信号、原始时间和主题判断是否值得打开；未入选内容直接进入审计。入选项默认读取完整 DOM 正文，每个段落块先提取可验证证据，再基于全部分段结论完成中文标题、摘要、最终质量判断和时间确认。高价值旧文章、当前热榜及持续更新项目仍可保留。

日报流程不使用统一的程序时间解析器提前淘汰候选。AI 必须返回 `published_at`、`time_kind`、`time_confidence` 和 `time_evidence`，区分首次发布、更新、GitHub 最近推送、榜单采集及未知时间。缺失时间不能单独成为拒绝理由；程序只校验结构并保存判断证据。

默认主题覆盖前端工程、人工智能与 AI 工程、后端开发、开发工具和开源项目。可用 `--topics "LLM,Agent,RAG,端侧 AI"` 传入自定义主题。

AI 根据用户主题、页面快照中的可验证证据、内容完整度、时间语义自主给出 0-100 质量分及推荐等级（质量分不使用固定权重）；同来源互动信号由固定公式计算互动辅助分（engagement_score，见 scoring_rules.py），两者独立写入笔记。推荐等级只能是 `strongly_recommended`、`optional` 或 `not_recommended`，且必须附带具体理由。

只有前两档会写入文章和日报。不推荐或 AI 处理失败的内容不进入正常日报，而是写入 `<Vault>/自动获取信息/拒绝集合/`；其中确定性拒绝同时进入 `_拒绝索引.json`，临时失败保留后续重试机会。AI 必须返回逐条 `evidence_points`，文章笔记和拒绝页展示真实判断依据，不再使用统一证据套话。文章 frontmatter 包含推荐结果、快照状态以及 AI 的时间类型、时间置信度和时间证据。

最终评估沿用批次拆分和单篇重试逻辑。最终评估失败时不进入正常日报，等级为 `evaluation_failed`，状态为 `failed`，并写入本地复核日志。

---

### 视频转文章（video_to_article.py）

触发：用户给视频 URL / 本地视频文件 / 视频文件夹，要求转成结构化中文文章。**完整操作手册见 `.agent/video-to-article/SKILL.md`**（转录链 / 四件套 / 去重 / 依赖 / 关键坑）。

速查：`py scripts\video_to_article.py <视频URL|本地视频文件|视频文件夹> [--out 输出根] [--topic 专题名] [--no-frames]`；输出根默认 `<信息流库>/视频整理`（paths.json `video_out` 或 `--out` 覆盖）；依赖 `.env` 的 `GROQ_API_KEY` 与 `LLM_*` + ffmpeg。

---

### 社交平台技术内容

新增平台 key：`douyin`、`bilibili`。bilibili 搜索关键词复用 `user_interests.json` 的 `topics`；搜索 URL 模板内置在 `scripts/social_platforms.py`（`_DEFAULT_SEARCH_URLS`）。`douyin` 已停用。如需自定义搜索 URL，可用 `NEWS_AGGREGATOR_SOCIAL_CONFIG` 指向含 `search_urls` 字段的配置文件，优先于内置默认。
Bilibili 已纳入常规 Obsidian 日报的默认来源列表；抖音仍可通过显式 `--source douyin` 单独抓取。


```bash
python scripts/fetch_news.py --source douyin,bilibili --limit 5 --keyword "前端,AI,软件工程"
```

适配器优先使用显式配置的 JSON API：`SOCIAL_API_URL_DOUYIN`、`SOCIAL_API_URL_BILIBILI`（当前仅这两个平台已接入）；可选的
`SOCIAL_API_TOKEN_<PLATFORM>` 会作为 Bearer Token 发送。未配置时，bilibili 自动改走官方搜索 API 直连（`_bilibili_api_search`，
`search/all/v2` 无签名、无需登录，实测稳定满 20 条/词、字段完整），直连失败才降级到
Playwright 公开搜索页（浏览器兜底）。浏览器会话目录可通过 `NEWS_AGGREGATOR_BROWSER_PROFILE` 配置；未配置时默认查找 `D:\news-aggregator-browser-profile`（执行过 `setup_social_login.py` 登录才有登录态，否则自动用临时会话），
不要把 Cookie、Token 或会话目录提交到仓库。首次使用或登录失效时，运行
`py scripts/setup_social_login.py --browser edge --platform all` 在专用 Edge 窗口中登录；会话默认保存在仓库外的 `D:\news-aggregator-browser-profile`，不会读取日常 Edge Profile。

社交平台抓取标题、简介、作者、发布时间和可见互动数据。Bilibili 和抖音只保留真实视频链接，
不下载视频；视频内容已接入两级转录：①平台字幕 API（bilibili，cookie）②Groq Whisper 语音兜底（fetch_news 视频分支，method="groq_transcript"）。

GitHub Trending 继续保留热榜排名，并通过 GitHub API 补充仓库最近一次 `pushed_at`；笔记中将其标为“最近推送时间”，不将其误称为发布时间。普通 GitHub 搜索结果仍遵守默认 24 小时窗口。

## Requirements

- Python 3.10+, `pip install -r requirements.txt`
- Playwright（仅深度抓取或需要浏览器的来源）：`playwright install chromium`
- Obsidian Vault 路径：`push_to_obsidian.py` 用 `--vault` / `OBSIDIAN_VAULT_PATH`；`run_daily.py` 按 paths.json → `NEWS_AGGREGATOR_VAULT` → `OBSIDIAN_VAULT_PATH` → 仓库所在库解析（**无 `--vault` 参数**；指向主库会拒绝写入）
