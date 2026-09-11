# 🗞️ News Aggregator 指令菜单

请回复 **序号** 执行任务。所有报告均自动保存到 `reports/YYYY-MM-DD/` 并以中文呈现。

---

### 🎯 核心新闻源

| # | 名称 | 命令 |
|---|---|---|
| 1 | 🦄 硅谷热点 (Hacker News) | `--source hackernews` |
| 2 | 🐙 开源趋势 (GitHub Trending) | `--source github` |
| 3 | 🚀 创投快讯 (36Kr) | `--source 36kr` |
| 4 | 🐱 产品猎人 (Product Hunt) | `--source producthunt` |
| 5 | 🤓 极客社区 (V2EX) | `--source v2ex` |
| 6 | 🐧 腾讯科技 (Tencent News) | `--source tencent` |
| 7 | 📈 华尔街见闻 (WallStreetCN) | `--source wallstreetcn` |
| 8 | 🤗 HF 每日论文 (Hugging Face) | `--source huggingface` |

---

### 📧 AI 行业内参

| # | 名称 | 命令 |
|---|---|---|
| 9 | 🧪 Latent Space AINews (swyx) | `--source latentspace_ainews` |
| 10 | ChinAI (Jeffrey Ding) | `--source chinai` |
| 11 | Memia (Ben Reid) | `--source memia` |
| 12 | Ben's Bites | `--source bensbites` |
| 13 | One Useful Thing (Ethan Mollick) | `--source oneusefulthing` |
| 14 | Interconnects (Nathan Lambert) | `--source interconnects` |
| 15 | AI to ROI | `--source aitoroi` |
| 16 | KDnuggets | `--source kdnuggets` |
| 17 | 🧠 全部 AI 内参聚合 | `--source ai_newsletters --limit 3` |

---

### ✍️ 深度思考 & 播客

| # | 名称 | 命令 |
|---|---|---|
| 18 | Paul Graham | `--source paulgraham` |
| 19 | Wait But Why | `--source waitbutwhy` |
| 20 | James Clear | `--source jamesclear` |
| 21 | Farnam Street | `--source farnamstreet` |
| 22 | Scott Young | `--source scottyoung` |
| 23 | Dan Koe | `--source dankoe` |
| 24 | 📚 全部文章聚合 | `--source essays --limit 3` |
| 25 | Lex Fridman Podcast | `--source lexfridman` |
| 26 | Latent Space (swyx) | `--source latentspace` |
| 27 | 80,000 Hours | `--source 80000hours` |
| 28 | 🎧 全部播客聚合 | `--source podcasts --limit 3` |

---

### ☕️ 每日早报 (Daily Briefings)

> 执行带「风格指引」的菜单项前，先读取对应 `instructions/briefing_*.md`，按其 Focus Areas / Report Structure / Anti-Laziness 约束组织输出。

| # | 名称 | 命令 | 风格指引 |
|---|---|---|---|
| 29 | 🌅 默认早报 | `push_to_obsidian.py --limit 15 --vault <Vault>` | `instructions/briefing_general.md` |
| 30 | 💰 财经早报 | `push_to_obsidian.py --source wallstreetcn,36kr,tencent --limit 15 --vault <Vault>` | `instructions/briefing_finance.md` |
| 31 | 🤖 科技早报 | `push_to_obsidian.py --source hackernews,github,producthunt --limit 15 --vault <Vault>` | `instructions/briefing_tech.md` |
| 32 | 🍉 社区热点 | `push_to_obsidian.py --source v2ex,tencent --limit 15 --vault <Vault>` | `instructions/briefing_social.md` |
| 33 | 🧠 AI 深度日报 | `push_to_obsidian.py --source aihot,openai,anthropic,arxiv --limit 15 --deep --vault <Vault>` | `instructions/briefing_ai_daily.md` |
| 34 | 📚 深度阅读清单 | `fetch_news.py --source essays,podcasts --limit 15 --deep --no-save` | —（无对应 briefing） |

---

### 🆕 扩展源 (v2)

| # | 名称 | 命令 |
|---|---|---|
| 35 | 🦞 Lobsters 技术深度 | `--source lobsters` |
| 36 | 👩‍💻 Dev.to 开发者热门 | `--source devto` |
| 37 | 📜 arXiv AI 最新论文 (cs.AI/CL/LG) | `--source arxiv` |
| 38 | 📕 少数派 (sspai) | `--source sspai` |
| 39 | 💻 InfoQ 中文 (软件工程/AI) | `--source infoq_cn --deep` |

---

### 🎯 AI 精选聚合 (v3) —— 二次精选，密度最高

> 这一档是别人的编辑团队替你筛过的 AI 高价值内容，单源信息密度顶 5-10 个原始信源。

| # | 名称 | 命令 |
|---|---|---|
| 40 | 🔥 AIHOT 中文 AI 精选（跨源 + 中文编辑稿） | `--source aihot` |
| 41 | 📨 TLDR AI（英文日刊，每天 5-10 主题摘要） | `--source tldr_ai` |
| 42 | 📜 Import AI by Jack Clark（英文周刊深度评论） | `--source import_ai --deep` |
| 43 | 🌐 AI 精选三件套（一次拉全） | `--source aihot,tldr_ai,import_ai` |

---

### 🔧 自定义订阅源

| # | 名称 | 命令 |
|---|---|---|
| 44 | 🔧 我的订阅源 (OPML) | `--source user` |

> 💡 **首次使用 OPML（44）**：先 `cp user_sources.opml.example user_sources.opml`，编辑里面的 `<outline xmlUrl="...">` 加自己的源；或从 Feedly/Inoreader 导出 OPML 覆盖即可。

---

### 🌍 国际新闻源

| # | 名称 | 命令 |
|---|---|---|
| 45 | 🌍 国际新闻聚合 (最近 24h) | `--source international --limit 20` |
| 46 | 📰 BBC Top News (最近 24h) | `--source bbc_top` |
| 47 | 🌐 BBC World (最近 24h) | `--source bbc_world` |
| 48 | 🈶 BBC 中文 (最近 24h) | `--source bbc_chinese` |
| 49 | 🗞️ The Guardian World (最近 24h) | `--source guardian_world` |
| 50 | 🛰️ Al Jazeera (最近 24h) | `--source aljazeera` |
| 51 | 🇫🇷 France 24 (最近 24h) | `--source france24` |
| 52 | 🧭 Reuters fallback (Google News RSS, 最近 24h) | `--source reuters` |

> 💡 **输出与时间窗口**：国际新闻源必须按统一报告模板输出；抓取只保留最近 24 小时内容，不用旧闻补位。`reuters` 使用 Google News RSS 的 `site:reuters.com` 检索结果。Reuters 官方公开 RSS 不稳定；如有 Reuters Connect 账号，可把 authenticated RSS 放进 OPML。

---

### 🔀 自由组合

直接指定多个源，用逗号分隔：

```
hackernews,github,wallstreetcn
```

例如：*"帮我看看 HN 和 GitHub 今天有什么热点"* → Agent 自动执行 `--source hackernews,github`

---

**✨ 请输入序号 (1-52) 或源名组合来执行**
