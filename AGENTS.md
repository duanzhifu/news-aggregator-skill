# 仓库指南

## 项目结构与模块组织

本仓库是一个 Python 3.10+ 新闻聚合技能。运行时代码位于 `scripts/`：`fetch_news.py` 负责协调内置信息源，`fetch_user_feeds.py` 处理 OPML 订阅，`rss_parser.py` 负责订阅源解析，其余抓取器支持专用网站和基于 Playwright 的提取。提示词和简报行为由 `SKILL.md`、`.agent/translate-summarize-skill/SKILL.md` 及 `instructions/` 定义。输出格式由 `templates.md` 定义（文章/总结页模板），`templates/Dashboard.md` 是可选的人工放置型 Dataview 仪表盘模板（非代码消费）；生成的报告放在 `reports/`，该目录已被忽略。`.agent/workflows/` 包含代理工作流定义。`.agent/faq-capture/SKILL.md` 是对话蒸馏成 FAQ 的技能（触发词「沉淀FAQ」/「capture as FAQ」），产物为 `FAQ/` 目录（按日期 md + `_索引.json` 全局去重，随仓库分发供问题排查复用）。`.agent/dynamic-search/SKILL.md` 是动态搜索独立技能（触发词「搜索xxx/全网搜xxx」，AnySearch 主 + Bing 兜底、`--assess-only` 先看先评、`--add-url` 写入收藏），主 SKILL.md 的 Tools 节保留速查与指针。

## 构建、测试与开发命令

本项目没有编译步骤或构建系统。使用以下命令安装依赖：

```bash
python -m pip install -r requirements.txt
playwright install chromium
```

使用 `python scripts/fetch_news.py --source hackernews --limit 10` 执行常规信息源抓取。根据 `user_sources.opml.example` 创建 `user_sources.opml` 后，可使用 `python scripts/fetch_news.py --source user --limit 15` 测试自定义订阅。要执行端到端的 Obsidian 导出，先配置仓库路径，再运行 `python scripts/push_to_obsidian.py --source ai_newsletters --limit 10 --deep`。Windows 下，`scripts/run_daily.ps1` 是历史计划任务兼容入口，转发到跨平台入口 `scripts/run_daily.py`（Vault 路径按 paths.json → NEWS_AGGREGATOR_VAULT → OBSIDIAN_VAULT_PATH → 仓库所在库解析，无硬编码路径；运行前确认 `user_interests.json` 的 `daily_sources` 配置）。

## 编码风格与命名约定

使用 4 个空格缩进和具有描述性的 `snake_case` 命名；导入时先写标准库，再写第三方库并分组。保持信息源适配器简洁，并返回 `fetch_news.py` 使用的现有标准化条目结构。保留 UTF-8 文本和已有 CLI 参数。项目未配置格式化工具或代码检查器；提交前使用 `python -m py_compile scripts/<file>.py` 检查修改过的 Python 文件。

## 测试规范

当前使用 `py -m unittest`（188 个用例，见 CLAUDE.md）。使用受限的在线抓取（`--limit 1` 或 `--limit 3`）验证抓取器改动，并检查生成的 JSON/Markdown 输出。修改解析器时，应尽可能同时测试 RSS 和 Atom 输入。不要提交凭据、`user_sources.opml`、生成的报告、日志或 Playwright 产物；这些路径已由 `.gitignore` 覆盖。

## 提交与拉取请求规范

使用简短、祈使语气的提交信息，并采用既有前缀：`feat:`、`fix:`、`chore:` 或 `docs:`，例如 `fix: handle empty RSS titles`。无关改动应分开提交。拉取请求应说明受影响的信息源或工作流，列出验证命令，注明新增的依赖或配置要求；若格式或报告行为发生变化，还应提供具有代表性的输出。示例和截图中不得包含订阅源 URL 或密钥。

## 安全与配置提示

将抓取到的文章内容和订阅源 URL 视为不可信输入。将 `OPENAI_API_KEY` 等 API 密钥保存在环境变量中，绝不写入受 Git 跟踪的文件。执行深度抓取或本地发布到 Obsidian 前，检查外部 URL 和输出路径。
