"""视频转写第 2 级兜底：yt-dlp 下音频 → Groq whisper 转文字。

统一入口：transcribe_via_groq(url) -> str

用途：当平台字幕 API 拿不到字幕（无字幕 / 字幕获取失败 / 空）时，作为通用兜底——
只要 yt-dlp 能下载到音频，任何视频站都能转出文字（抖音、快手、西瓜等以后加源自动覆盖）。

失败兜底：任何一步失败（无 GROQ_API_KEY / yt-dlp 失败 / 音频超 25MB / 网络 / 限流）→
返回空字符串，由下游 fetch_news 回退到「视频页 DOM 抓简介」，绝不中断整条拉取管线。
"""
import os
import subprocess
import sys
import tempfile

_GROQ_BASE = "https://api.groq.com/openai/v1"
_GROQ_TRANSCRIBE_URL = _GROQ_BASE + "/audio/transcriptions"
_MAX_FILE_BYTES = 25 * 1024 * 1024  # Groq 免费档单文件上限 25MB


def _load_env_file():
    """从 skill 根目录 .env 读取 GROQ_* 变量注入 os.environ（不覆盖已有环境变量）。"""
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(skill_root, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("GROQ_"):
                    os.environ.setdefault(key, val.strip().strip('"').strip("'"))
    except Exception:
        pass


def _groq_api_key():
    _load_env_file()
    return os.environ.get("GROQ_API_KEY", "").strip()


def _groq_model():
    _load_env_file()
    return os.environ.get("GROQ_TRANSCRIBE_MODEL", "whisper-large-v3-turbo").strip()


def _download_audio(url, tmpdir):
    """用 yt-dlp 下载音频并转成 mp3，返回本地文件路径；失败返回 None。

    使用 sys.executable -m yt_dlp（与运行中的解释器一致，避免 PATH 上的 python 缺失）。
    """
    out_tmpl = os.path.join(tmpdir, "audio.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio/best",
        "--extract-audio",
        "--audio-format", "mp3",
        "--audio-quality", "5",  # ~128kbps，ASR 够用且控制体积
        "-o", out_tmpl,
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout", "30",
        url,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=300, check=True)
    except Exception as e:
        print(f"[视频转录] yt-dlp 下载音频失败：{e}", file=sys.stderr)
        return None
    mp3 = os.path.join(tmpdir, "audio.mp3")
    return mp3 if os.path.exists(mp3) else None


def transcribe_via_groq(url, source_hint=None):
    """从视频 URL 提取转写文本（第 2 级兜底）。失败/超限/异常 → 返回空串。"""
    if not url:
        return ""
    api_key = _groq_api_key()
    if not api_key:
        return ""
    try:
        import requests
    except ImportError:
        print("[视频转录] requests 未安装，Groq 兜底不可用", file=sys.stderr)
        return ""

    tmpdir = tempfile.mkdtemp(prefix="groq_transcribe_")
    try:
        mp3 = _download_audio(url, tmpdir)
        if not mp3:
            return ""
        size = os.path.getsize(mp3)
        if size > _MAX_FILE_BYTES:
            print(f"[视频转录] 音频 {size/1024/1024:.1f}MB 超过 25MB 上限，放弃 Groq 兜底", file=sys.stderr)
            return ""

        with open(mp3, "rb") as f:
            resp = requests.post(
                _GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.mp3", f, "audio/mpeg")},
                data={"model": _groq_model()},
                timeout=300,
            )
        if resp.status_code != 200:
            print(f"[视频转录] Groq 转写失败 HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
            return ""
        payload = resp.json()
        text = (payload or {}).get("text", "").strip()
        if not text:
            print("[视频转录] Groq 转写返回空文本", file=sys.stderr)
        return text
    except Exception as e:
        print(f"[视频转录] Groq 转写异常：{e}", file=sys.stderr)
        return ""
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
