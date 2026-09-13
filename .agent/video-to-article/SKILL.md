---
name: video-to-article
description: "视频转文章：用户说「视频转文章」「把这个视频整理成文章」「把视频目录批量转成文章」时触发。主入口 scripts/video_to_article.py（视频 URL / 本地文件 / 文件夹 → 转录 → 四件套结构化中文文章）。本技能是 news-aggregator-skill 仓库的功能子技能，主手册见仓库根 SKILL.md。"
---

# Video to Article（视频转文章）

**触发**：用户给视频 URL 或本地视频文件（可批量文件夹），要求转成结构化中文文章。

---

## 命令（cwd = skill root）

```bash
py scripts\video_to_article.py <视频URL|本地视频文件|视频文件夹> [--out 输出根] [--topic 专题名] [--no-frames]
```

| 参数 | 说明 |
| --- | --- |
| 位置参数 | 视频 URL / 本地视频文件 / 视频文件夹（文件夹 = 遍历串行批量，单个失败不中断整批，结束输出失败清单） |
| `--out` | 输出根目录；默认 `<信息流库>/视频整理`（跟随仓库相对解析，也可用 paths.json 的 `video_out` 键覆盖） |
| `--topic` | 专题名；默认 = 批量时文件夹名、单文件时父文件夹名、URL 时「网络视频」 |
| `--no-frames` | 关闭截图抽帧 |

**输入三种形态的转录链**：
- **URL**：bilibili 字幕 API（cookie，`wbi/v2` 匿名返空必须 cookie）→ 失败 Groq whisper 兜底
- **本地文件**：ffmpeg 抽音频（`-vn -ac 1 -b:a 64k` 单声道 mp3）→ Groq 转写
- **文件夹**：遍历批量，单个失败不中断

**产物**（四件套）：frontmatter + 摘要 + 大纲 + 分节正文（带时间点与截图）+ 思维导图（Mermaid）+ 完整文稿（折叠 callout）。

**输出目录结构（方案 B）**：
```
<out>/<专题>/YYYY-MM-DD - 标题.md
<out>/<专题>/附录/<YYYY-MM-DD - 标题>截图/mm-ss.jpg
```
大纲（纯文本树）在文章开头 `## 大纲`；尾部思维导图只留 Mermaid `flowchart LR` 图（节点 ≤12 字、`_flow_label` 剥双引号）。

---

## 依赖（skill 本地 .env）

- `GROQ_API_KEY`（`gsk_` 前缀）：转写。Groq 免费档单文件 ≤25MB（64k ≈ 52 分钟、32k ≈ 104 分钟），超长暂报错不自动分片
- `LLM_*`（`LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`）：写文章（成文脚本内 `os.environ.setdefault("LLM_API_TIMEOUT_SECONDS", "600")` 放宽超时到 600s，仅本脚本生效）
- **ffmpeg**（本机已装 winget Gyan.FFmpeg 8.1.1）：抽音频 + 截图

---

## 去重（2026-09-02 落地）

- **精确指纹**：本地文件 = 大小+时长+首帧hash（`_probe_duration`+`_first_frame_hash`）；URL = 规范化 URL（`_normalize_url` 去 utm_*、query 排序）。命中打印「已处理过+产物路径」并跳过；`--force` 强制重跑覆盖索引
- **语义近似**：转录文本算 64 位 simhash（字符 trigram+md5 加权，无外部依赖），与**同专题**已有记录比汉明距离 ≤8 判「高度重合」，文章开头插 `> [!warning] 内容重合提示` 块（只提示不阻断）；跨专题不比
- 处理索引：`scripts/processed_index.json` 记录 `{指纹键: {kind, topic, title, output_md, processed_at, simhash}}`，成功才写、force 重跑覆盖
- ⚠️ 旧产物不迁移进索引；跑 E2E 后必须删测试索引（`Remove-Item processed_index.json`），否则真跑会误跳

---

## 关键坑（实测）

1. **转录失败即退出**，不靠标题瞎编（文章任务不做「标题+简介」退化）。
2. **LLM 成文超时**：`deepseek-v4-pro` 是 reasoning 模型，长转录（>2k 字）推理常超默认 120s 读超时 → `TimeoutError`（转录已成功、卡在成文）。`main()` 已 `setdefault("LLM_API_TIMEOUT_SECONDS", "600")` 放宽——只在脚本内生效，不影响管线其它调用。
3. **商汤 429 判别**：先看错误体再决定重试还是换 provider——`insufficient_quota`（`Allocated quota exceeded...`）= 账号级配额用尽，等多久都白等，唯一出路 = 等配额恢复或切 timicc；`rpm exhausted`（code 8）= 瞬时限流，等 ~60s RPM 窗口再重试。判别顺序：看 `错误：LLM API HTTP 429:` 后面跟的 message 字段。切 provider 用 `.env` 单文件注释切换（见 references/llm-provider-config.md）。
4. **成文 429 时截图已先落盘**（目录名「未命名视频文章截图」），重跑前先清失败残留目录，避免新旧截图混叠。
5. **frontmatter 用 `_yaml_str` 写值**（json.dumps 转义）：Windows 路径 `\U` 会被 YAML 当 Unicode 转义导致 Obsidian 报「无效属性」。
6. **Mermaid 布局**：夸克式左到右树必须 `flowchart LR` + 节点 ID + `父 --> 子`；`mindmap` 是中心放射状脑图，改节点内容不会变布局。
7. **时间点来源**：优先带时间戳分段转写（Groq `verbose_json` / bilibili 字幕 JSON 自带 `from/to`）；拿不到再回退纯文本（无时间点）。
8. **pre-flight 必做**：时长 / 25MB 上限 / GROQ key / `--topic`；ffprobe 不能经 `&` 直接调（Hermes 守卫拦），用 `py -c` + subprocess。
9. **成文慢 ≠ 卡住**：用户问「跑这么久还没完成」时，先看 `_SINGLE_CALL_CHARS=14000` 分支 + `Get-Process CPU/WS` 判活，别急着杀进程。

---

## 验证与测试

- 全量单测：`$env:PYTHONPATH="<skill>\scripts;<skill>"; py -m unittest tests.test_video_to_article_dedupe tests.test_groq_transcribe tests.test_video_transcribe`
- 修改脚本先 `py -m py_compile scripts\video_to_article.py`
- 真实端到端：单个视频 URL/文件跑一次，检查输出目录结构（专题/日期-标题.md + 附录/截图）与 frontmatter 字段
