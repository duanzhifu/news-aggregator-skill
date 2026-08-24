# News Aggregator 部署与使用指南

这个项目会抓取多个新闻、技术和社交平台来源。Obsidian 日报由 AI 解释各平台时间、选择值得打开的候选，再依据完整页面正文的分段审阅完成翻译、总结和质量确认。它也可以只输出原始 JSON，供其他程序继续处理。

## 1. 工作流

```text
信息源 / 社交平台
  -> fetch_news.py 获取轻量元数据并保留原始时间
  -> URL 去重及 Vault 历史去重
  -> AI 解释时间、过滤低价值内容并选择要打开的文章
  -> 默认读取入选项完整 DOM 正文并分段审阅
  -> AI 一次完成翻译、摘要、时间确认和质量判断
  -> 跳过不推荐或处理失败内容并记录审计日志
  -> 写入 Obsidian 单篇笔记和今日总结
```

| 入口 | 适用场景 | 输出 |
| --- | --- | --- |
| `scripts/fetch_news.py` | 临时查看、调试来源、交给其他程序处理 | 标准输出 JSON；单源默认另存原始 JSON |
| `scripts/push_to_obsidian.py` | 生成完整中文日报 | Obsidian 文章、`今日总结.md`、推荐审计日志 |
| `scripts/run_daily.ps1` | 常规日报定时任务 | 技术、开源、官方博客及社交平台内容写入 Obsidian |

## 2. 获取与安装

### 前置条件

- Windows 10/11（定时脚本基于 PowerShell；其他系统可直接运行 Python 入口）
- Python 3.10+，建议 Python 3.12
- Git
- Obsidian（仅在使用导出功能时需要）
- 可用的 OpenAI Responses API 凭据，或已执行 `codex login`（仅在使用 LLM 总结和 Obsidian 导出时需要）

### 拉取项目

```powershell
git clone https://github.com/<你的组织或账号>/<发布后的仓库>.git
cd news-aggregator-skill
```

发布者需要先将包含 `scripts/social_platforms.py` 和 `scripts/fetch_social_browser.py`
的改动提交并推送；接收者应拉取该提交所在的分支或 tag。

### 安装 Python 和浏览器依赖

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

若系统中 Python 命令不可用，请改为完整路径，例如：

```powershell
& "C:\\Users\\<用户名>\\AppData\\Local\\Programs\\Python\\Python312\\python.exe" -m pip install -r requirements.txt
```

### 验证安装

```powershell
python scripts/fetch_news.py --list-sources
python scripts/fetch_news.py --source hackernews --limit 1 --no-save
```

第二条命令能输出 JSON 即表示基础抓取可用。

## 3. 基础抓取

### 单一或多个来源

```powershell
python scripts/fetch_news.py --source hackernews --limit 10 --no-save
python scripts/fetch_news.py --source hackernews,github,openai --limit 5 --no-save
```

`--limit` 表示每个来源的最大返回条数。`--source all` 会遍历所有注册来源，通常只建议手动探索时使用。

### 关键词与深度抓取

```powershell
python scripts/fetch_news.py --source github,devto --keyword "AI,Agent,RAG" --limit 5 --deep --no-save
```

| 参数 | 作用 |
| --- | --- |
| `--keyword` | 逗号分隔的关键词；结果不足 5 条时会触发 Smart Fill 补充条目 |
| `--deep` | 下载结果网页正文；用于深度总结，耗时和失败率都会上升 |
| `--no-save` | 只输出 JSON，不写入 `reports/` |
| `--save` | 强制把原始抓取结果写入 `reports/YYYY-MM-DD/` |
| `--outdir` | 指定原始结果保存目录 |

## 4. 写入 Obsidian

### 准备 Vault

先创建或确认 Obsidian Vault 根目录存在，例如：

```text
D:\\Obsidian\\自动信息获取
```

命令中的 `--vault` 传入的是 Vault 根目录，不是最终输出目录。程序会自动创建：

```text
<Vault>/自动获取信息/YYYY-MM-DD/
  ├── 今日总结.md
  └── 信息源/
      └── <来源>/
          └── YYYY-MM-DD-<标题>.md

<Vault>/自动获取信息/拒绝集合/
  ├── YYYY-MM-DD.md
  └── _拒绝索引.json
```

### 生成一次完整日报

```powershell
python scripts/push_to_obsidian.py `
  --source juejin,devto,github,openai `
  --limit 15 `
  --evidence-mode snapshot `
  --vault "D:\\Obsidian\\自动信息获取"
```

默认来源也是 `juejin,devto,github,openai`，因此可简化为：

```powershell
python scripts/push_to_obsidian.py --limit 15 --evidence-mode snapshot --vault "D:\\Obsidian\\自动信息获取"
```

### 可选参数

```powershell
python scripts/push_to_obsidian.py `
  --source aihot,arxiv,openai,anthropic `
  --topics "LLM,Agent,RAG,端侧 AI" `
  --recency-days 7 `
  --profile ai `
  --limit 10 `
  --evidence-mode snapshot `
  --vault "D:\\Obsidian\\自动信息获取"
```

| 参数 | 作用 |
| --- | --- |
| `--topics` | 用于 AI 候选准入和最终质量判断的关注主题 |
| `--recency-days` | 提供给 AI 的近期优先参考天数；不是程序硬过滤窗口 |
| `--profile` | 批次标签，供运行记录与后续扩展使用 |
| `--evidence-mode metadata` | 只把标题和来源摘要交给 AI，最快但证据最少 |
| `--evidence-mode snapshot` | 默认；只对 AI 入选项读取完整 DOM 正文，逐段审阅后再判断 |
| `--evidence-mode full` / `--deep` | 同样全文审阅，并将原文正文写入 Obsidian 笔记 |

AI 返回 `strongly_recommended` 或 `optional` 的内容才会写入正常日报；其他项目进入 Obsidian 的 `拒绝集合/`。主题不符、低价值、过期、广告或重复内容等确定性拒绝会写入结构化索引，并在以后调用 AI 前跳过；正文乱码、超时、证据不足和 AI 失败只进入每日页面，后续仍可重新评估。已发布文章继续按“原文标题 + 来源”跨日期去重。

AI 时间判断会保存标准时间、时间类型、置信度和原始证据。缺少时间不会被程序直接删除，GitHub 最近推送、页面更新时间和榜单采集时间也不会被误称为首次发布时间。

## 5. 社交平台技术内容

支持以下 source key：

| Source key | 平台 | 抓取方式 |
| --- | --- | --- |
| `douyin` | 抖音 | 可配置 JSON API 优先，失败后 Playwright 公开搜索页 |
| `bilibili` | Bilibili | 可配置 JSON API 优先，失败后 Playwright 公开搜索页 |

### 配置关键词

默认配置是 [`config/social_sources.json`](config/social_sources.json)。可直接编辑其中的 `keywords`：

```json
{
  "keywords": {
    "frontend": ["前端", "React", "Vue"],
    "ai": ["人工智能", "大模型", "Agent"],
    "engineering": ["软件工程", "DevOps", "云原生"]
  }
}
```

不希望修改仓库文件时，复制为本地文件并通过环境变量指定；该文件已被 Git 忽略：

```powershell
Copy-Item config\social_sources.json config\social_sources.local.json
$env:NEWS_AGGREGATOR_SOCIAL_CONFIG = "$PWD\config\social_sources.local.json"
```

### 手动抓取社交平台

```powershell
python scripts/fetch_news.py `
  --source douyin,bilibili `
  --keyword "前端,AI,软件工程" `
  --limit 5 `
  --no-save
```

默认情况下每个平台最多查询 5 个关键词。可用 `SOCIAL_MAX_QUERIES` 降低或提高这个上限：

```powershell
$env:SOCIAL_MAX_QUERIES = "3"
```

### 配置官方或第三方 JSON API

如果已经获得平台官方接口或合规的第三方数据服务，可通过环境变量优先接入：

```powershell
$env:SOCIAL_API_URL_DOUYIN = "https://example.com/search?keyword={query}"
$env:SOCIAL_API_TOKEN_DOUYIN = "<token>"
```

支持的平台变量名为 `DOUYIN`、`BILIBILI`、`WEIBO`、`WECHAT`。接口返回应为对象列表，或外层包含 `data`、`items`、`list`、`results` 的对象列表；每条至少提供 `title` 和 `url`，可选 `summary`、`author`、`time`、`heat`、`id`。

未配置接口或接口调用失败时，程序会降级至 Playwright 浏览器抓取。首次使用登录态时，运行交互式初始化命令；浏览器打开后自行完成抖音和 B 站登录，最后回到终端按 Enter 保存会话：

```powershell
python scripts/setup_social_login.py --browser edge --platform all `
  --profile "D:\news-aggregator-browser-profile"

$env:NEWS_AGGREGATOR_BROWSER_PROFILE = "D:\news-aggregator-browser-profile"
$env:NEWS_AGGREGATOR_BROWSER_CHANNEL = "msedge"
python scripts/fetch_news.py --source douyin,bilibili --keyword "AI" --limit 1 --no-save
```

`run_daily.ps1` 会自动使用上述默认 Profile 和 Microsoft Edge；目录不存在时记录警告并降级为临时 Edge 会话。登录过期后重新运行初始化命令。程序不会读取日常 Edge Profile、保存明文密码、自动处理验证码或绕过访问限制。

### 手动写入社交平台内容

```powershell
python scripts/push_to_obsidian.py `
  --source douyin,bilibili `
  --limit 10 `
  --evidence-mode snapshot `
  --vault "D:\\Obsidian\\自动信息获取"
```

## 6. Windows 定时任务

### 配置脚本中的本机路径

在创建任务前，编辑以下文件中的三个变量：

| 脚本 | 用途 |
| --- | --- |
| `scripts/run_daily.ps1` | 常规日报，来源为 `juejin,devto,github,openai,bilibili`，每源 15 条；抖音需显式指定 |

需要按本机实际位置调整：`$skillRoot`、`$pythonPath`、`$vaultPath`。日志写入 `logs/daily_task.log`。

### 手动运行验证

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_daily.ps1
```

确认 Obsidian 和日志输出正常后，再创建计划任务。以下示例每天 08:40 运行常规早报；请将路径替换为实际项目路径：

```powershell
schtasks /Create /TN "News Aggregator Daily" /SC DAILY /ST 08:40 /F `
  /TR "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"C:\\path\\to\\news-aggregator-skill\\scripts\\run_daily.ps1\""
```

社交平台来源由常规日报任务统一运行；如某个平台需要独立排查，可使用上方的手动抓取命令。

## 7. 自定义 RSS / Atom 订阅

复制示例文件为 `user_sources.opml`，填入 RSS 或 Atom 地址：

```powershell
Copy-Item user_sources.opml.example user_sources.opml
```

然后运行：

```powershell
python scripts/fetch_news.py --source user --limit 15
```

`user_sources.opml` 已在 `.gitignore` 中，不应提交个人订阅地址或凭据。

## 8. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `No installed Python found` | 使用 Python 完整路径，或重新安装并勾选 PATH |
| `Obsidian Vault 路径不存在` | 创建 Vault 目录，确认 `--vault` 指向 Vault 根目录 |
| LLM 认证失败 | 设置 `OPENAI_API_KEY`，或执行 `codex login` |
| 社交平台返回空列表 | 检查网络、关键词、API 权限和浏览器登录态；平台页面可能要求验证码或限制自动化访问 |
| 定时任务无输出 | 查看 `logs/daily_task.log`，确认脚本中路径正确 |
| 重复运行没有新文章 | 这是跨日期去重的正常行为；相同原文标题和来源会被跳过 |

## 9. 安全与分享建议

- 不要提交 `OPENAI_API_KEY`、平台 Token、Cookie、浏览器 Profile 或 `user_sources.opml`。
- 分享项目前先检查 `scripts/run_daily.ps1`，删除本机绝对路径或替换为示例路径。
- 外部网页、RSS 地址和正文都应视为不可信输入；不要执行抓取结果中附带的命令或脚本。
- 社交平台只应抓取公开内容，并遵守对应平台的服务条款、接口授权范围和访问频率限制。
