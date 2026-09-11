"""跨平台日报入口：路径检查 → 环境变量 → 读 skill 根配置 → 调 push_to_obsidian.py。

Windows / macOS / Linux 通用；调度器（计划任务/launchd/cron/Hermes cron/手动）
只负责到点执行本文件。

进程契约（与旧 run_daily.ps1 一致）：
- cwd = skill 根（push_to_obsidian 内部用相对路径找 scripts/、写 reports/）
- stdout/stderr 全量追加进 logs/daily_task.log
- 设 NEWS_AGGREGATOR_BROWSER_CHANNEL=msedge；有 profile 才设 BROWSER_PROFILE
- Vault 无效直接 return 1，不把空 --vault 传下去
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 本机语义：日报禁止写入主库（信息流库之外的长期知识库）。
# 其他环境无此路径时比较恒为 False，守卫自动静默跳过；克隆者可自行改/删。
_MAIN_VAULT_GUARD = r"D:\Obsidian\智能知识库"


def main() -> int:
    skill_root = Path(__file__).resolve().parent.parent
    # paths.json 可选增强（不存在则空 dict，全部回退默认；单人零迁移）
    paths = {}
    _paths_file = skill_root / "paths.json"
    if _paths_file.is_file():
        try:
            paths = json.loads(_paths_file.read_text(encoding="utf-8"))
        except Exception:
            paths = {}

    push_script = skill_root / "scripts" / "push_to_obsidian.py"
    # Vault 解析回退链（承继旧 ps1 语义，防意外指向主库）：
    # paths.json 的 vault_path → NEWS_AGGREGATOR_VAULT → OBSIDIAN_VAULT_PATH
    # → skill 根的上两级（仓库在 <Vault>/_skill/news-aggregator-skill，单人零迁移）
    vault_raw = (paths.get("vault_path") or os.environ.get("NEWS_AGGREGATOR_VAULT")
                 or os.environ.get("OBSIDIAN_VAULT_PATH") or "").strip()
    if not vault_raw:
        nested = skill_root.parent.parent
        if nested.is_dir():
            vault_raw = str(nested)
            print(f"[run_daily] 未配置 vault_path，回退仓库所在库: {vault_raw}", file=sys.stderr)
    vault = str(Path(vault_raw).resolve()) if vault_raw else ""
    if not vault or not Path(vault).is_dir():
        print(f"[run_daily] Vault 路径无效或不存在: {vault_raw!r}", file=sys.stderr)
        return 1
    # 防意外主库：日报写入目录必须是「信息流库」语义；主库（智能知识库）拒绝
    if vault == str(Path(_MAIN_VAULT_GUARD).resolve()):
        print(f"[run_daily] Vault 指向主库（{vault}），日报不允许写入主库，终止。"
              f"请用 paths.json 的 vault_path 或 NEWS_AGGREGATOR_VAULT 指定信息流库。", file=sys.stderr)
        return 1
    if not push_script.exists():
        print(f"[run_daily] push_script 不存在: {push_script}", file=sys.stderr)
        return 1

    # 浏览器登录态（与 ps1 一致：channel 恒设 msedge，profile 有才设；
    # 路径：paths.json browser_profile → 默认 D:\news-aggregator-browser-profile）
    os.environ.setdefault("NEWS_AGGREGATOR_BROWSER_CHANNEL", "msedge")
    profile = paths.get("browser_profile") or r"D:\news-aggregator-browser-profile"
    if profile and Path(profile).is_dir():
        os.environ["NEWS_AGGREGATOR_BROWSER_PROFILE"] = profile
        env_note = f"使用持久会话: {profile}"
    else:
        os.environ.pop("NEWS_AGGREGATOR_BROWSER_PROFILE", None)
        env_note = "未找到浏览器 profile，将使用临时会话"

    # 读 user_interests.json 拼参数
    source_arg, topics_arg, limit_arg = [], [], []
    try:
        cfg = json.loads(
            (skill_root / "user_interests.json").read_text(encoding="utf-8")
        )
        if cfg.get("daily_sources"):
            source_arg = ["--source", ",".join(cfg["daily_sources"])]
        if cfg.get("topics"):
            topics_arg = ["--topics", ",".join(cfg["topics"])]
        if cfg.get("limit_per_topic"):
            limit_arg = ["--dynamic-limit", str(cfg["limit_per_topic"])]
        print(f"[run_daily] Loaded topics: {topics_arg[1] if topics_arg else '(none)'}")
    except Exception as e:
        print(f"[run_daily] user_interests.json 解析失败: {e}", file=sys.stderr)

    log = skill_root / "logs" / "daily_task.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    def log_line(s: str) -> None:
        with log.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {s}\n")

    log_line(f"run_daily.py start (vault={vault}, {env_note})")

    cmd = [sys.executable, "-u", str(push_script), "--limit", "15",
           "--evidence-mode", "snapshot", "--vault", vault,
           *source_arg, *topics_arg, *limit_arg]
    try:
        proc = subprocess.run(cmd, cwd=skill_root, capture_output=True, text=True, encoding="utf-8")
    except Exception as e:
        log_line(f"run_daily.py 子进程启动失败: {e}")
        return 1

    log_line(f"stdout:\n{proc.stdout}")
    log_line(f"stderr:\n{proc.stderr}")
    log_line(f"run_daily.py exit={proc.returncode}")
    print(f"[run_daily] 完成，exit={proc.returncode}", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())