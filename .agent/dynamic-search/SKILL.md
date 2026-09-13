---
name: dynamic-search
description: "动态搜索（按主题全网搜，非固定源）：用户说「搜索 xxx」「全网搜 xxx」「针对 xxx 主题搜一下」[动态搜索 xxx]时触发。主入口 push_to_obsidian.py --topics ... --assess-only（先看先评不落盘）或 --add-url 写入。引擎 AnySearch 主 + Bing 登录态兜底。本技能是 news-aggregator-skill 仓库的功能子技能，主手册见仓库根 SKILL.md。"
---

# Dynamic Search（动态搜索：按主题全网搜，非固定源）

**触发**：用户说「搜索 xxx」「全网搜 xxx」「针对 xxx 主题搜一下」。

与固定源抓取（`--source`）是两条路：动态搜索**不经过 `--source`**，按用户给的主题词在全网搜。

---

## 命令（cwd = skill root）

```bash
# 先看先评（搜 + AI 评估出「值得看/不值得看 + 理由」表，不写库不落盘，跑完即停）
py scripts\push_to_obsidian.py --topics "主题词" --dynamic-limit 5 --assess-only

# 用户挑选后写入（不收藏）
py scripts\push_to_obsidian.py --vault <信息流库根> --add-url <URL>

# 写入 + 收藏（已仔细看过，跳过 AI 判断，kind=manual）
py scripts\push_to_obsidian.py --vault <信息流库根> --add-url <URL> --saved
```

| Arg               | 说明                                                                                      |
| ----------------- | ----------------------------------------------------------------------------------------- |
| `--topics`        | 逗号分隔搜索主题（如 `"DeepSeek,Agent"`）                                                 |
| `--dynamic-limit` | 每主题条数（默认读 `user_interests.json` 的 `limit_per_topic`，当前 5；CLI 显式传参优先） |
| `--assess-only`   | 只搜 + AI 评估出表（值得看/不值得看 + 理由 + 依据），不写库不落盘，跑完即停               |
| `--add-url`       | 手动写入一个 URL，可重复多传；默认先 AI 判断值不值得看，值得才生成笔记进当天总结          |

---

## 引擎链（AnySearch 主 + Bing 兜底）

- 默认引擎 **AnySearch**（`scripts/fetch_dynamic_search.py`，`engine="anysearch"`，zone=cn、language=zh-CN）。`.env` 配 `ANYSEARCH_API_KEY`（`as_sk_` 前缀）后使用独立额度；未配置时匿名低限额运行。
- 某主题**全部**动态结果被 AI 评估拒绝（死主题）时，管线自动用 **Bing 登录态**兜底重搜：`cn.bing.com` + `setmkt=zh-CN&setlang=zh-hans`，复用 `D:\news-aggregator-browser-profile`（`_U` cookie = 已登录）。
- 兜底链在 `push_to_obsidian.py` 的 `_dead_dynamic_topics` 块自动触发，无需人工干预。

**诊断日志特征**：

- `[AnySearch] 正在检索关键词: ...` = AnySearch 成功
- `[AnySearch Error]` = AnySearch 失败
- `[DynamicSearch] 检测到 N 个主题 AnySearch 结果全部被拒，回退 Bing 登录态重搜: [...]` = Bing 兜底触发

---

## L0 零成本硬挡（先于 AI 评估）

过滤**只作用于动态搜索项**，RSS 固定源全部保留：

- `reject`（`user_interests.json`）→ 注入 AI 准入 prompt（语义判断，不按标题硬删）
- `block_hosts` → 匹配 hostname（精确或 `.host` 子域）直接丢弃，不进 LLM。语义：`en.wikipedia.org` 挡本域及子域（`blog.en.wikipedia.org`），但 `en.m.wikipedia.org` 是 `m.wikipedia.org` 子域、与 `en` 平级**不命中**——要挡整站需加根域（如 `wikipedia.org`）
- `block_url_patterns` → 匹配 URL **path**（如 `/docs/`、`/tutorials/`）直接丢弃
- 官网首页/去重：`is_site_homepage` 命中（`/`、`/index.html`、`/home`）即 continue；`/index/jalapeno-…`、`/docs/tutorials/` 不判首页

---

## 多义词坑（trellis 类）

英文多义词（如 `trellis`）AnySearch 会返回错误义项（园艺花架），`zone=cn` 锁不住市场。**不要**改 `block_url_patterns` / 术语映射去过滤（那是跟随关键词维护的方案，用户明确反对）。正确做法：

1. 默认走兜底链：AnySearch 错误义项 → 评估层整批 reject → 自动 Bing 重搜（锁中文市场，返回正确义项）；
2. 兜底仍不对 → 先跟用户确认「要哪个义项」，只保留目标义项的结果走 AI 评估；
3. 可开启 `user_interests.json` 的 `search_query_optimization`（默认 `false`）：每个 topic 先经 LLM 消歧为 1~3 个变体、全搜合并（多变体每变体降为 2 条），消歧失败自动回退原词直搜。

---

## 相关配置（user_interests.json）

| 键                                              | 作用                                                    |
| ----------------------------------------------- | ------------------------------------------------------- |
| `topics`                                        | 动态搜索主题，经 `--topics` 传入（bilibili 关键词同源） |
| `limit_per_topic`                               | 每主题条数（当前 5），经 `--dynamic-limit` 注入         |
| `search_query_optimization`                     | 搜索词 LLM 消歧开关（默认 false，见上）                 |
| `reject` / `block_url_patterns` / `block_hosts` | L0 硬挡规则（见上）                                     |

> 首次使用：复制 `user_interests.json.example` → `user_interests.json` 后按需修改（`user_interests.json` 已被 .gitignore 忽略，不会提交个人兴趣；`_` 开头的键是注释，程序会忽略）。
