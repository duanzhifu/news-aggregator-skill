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


def _extract_audio_local(video_path, tmpdir):
    """本地视频文件抽音频成单声道 mp3，返回路径；失败返回 None。

    用 ffmpeg 抽音轨（-vn 去视频、-ac 1 单声道、64k 控制体积，ASR 够用）。
    """
    out_mp3 = os.path.join(tmpdir, "local_audio.mp3")
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn", "-ac", "1", "-b:a", "64k",
        out_mp3,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=600, check=True)
    except Exception as e:
        print(f"[视频转录] ffmpeg 抽音频失败：{e}", file=sys.stderr)
        return None
    return out_mp3 if os.path.exists(out_mp3) else None


def _post_audio_to_groq(mp3, api_key):
    """把 mp3 发给 Groq whisper，返回转写文本；任何失败返回空串。"""
    try:
        import requests
    except ImportError:
        print("[视频转录] requests 未安装，Groq 兜底不可用", file=sys.stderr)
        return ""
    size = os.path.getsize(mp3)
    if size > _MAX_FILE_BYTES:
        print(f"[视频转录] 音频 {size/1024/1024:.1f}MB 超过 25MB 上限，放弃 Groq 兜底", file=sys.stderr)
        return ""
    try:
        with open(mp3, "rb") as f:
            resp = requests.post(
                _GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.mp3", f, "audio/mpeg")},
                data={"model": _groq_model()},
                timeout=300,
            )
    except Exception as e:
        print(f"[视频转录] Groq 请求异常：{e}", file=sys.stderr)
        return ""
    if resp.status_code != 200:
        print(f"[视频转录] Groq 转写失败 HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return ""
    try:
        payload = resp.json()
    except Exception:
        return ""
    text = (payload or {}).get("text", "").strip()
    if not text:
        print("[视频转录] Groq 转写返回空文本", file=sys.stderr)
    return text


def _post_audio_to_groq_segments(mp3, api_key):
    """把 mp3 发给 Groq whisper（verbose_json），返回带时间戳的分段列表。

    返回 [{start, end, text}, ...]；任何失败返回 []。供 video_to_article 标注时间点。
    """
    try:
        import requests
    except ImportError:
        print("[视频转录] requests 未安装，Groq 分段转写不可用", file=sys.stderr)
        return []
    size = os.path.getsize(mp3)
    if size > _MAX_FILE_BYTES:
        print(f"[视频转录] 音频 {size/1024/1024:.1f}MB 超过 25MB 上限，放弃 Groq 兜底", file=sys.stderr)
        return []
    try:
        with open(mp3, "rb") as f:
            resp = requests.post(
                _GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.mp3", f, "audio/mpeg")},
                data={"model": _groq_model(), "response_format": "verbose_json"},
                timeout=300,
            )
    except Exception as e:
        print(f"[视频转录] Groq 分段转写请求异常：{e}", file=sys.stderr)
        return []
    if resp.status_code != 200:
        print(f"[视频转录] Groq 分段转写失败 HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
        return []
    try:
        payload = resp.json()
    except Exception:
        return []
    out = []
    for seg in (payload or {}).get("segments", []) or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({"start": seg.get("start", 0.0), "end": seg.get("end", 0.0), "text": text})
    if not out:
        # verbose_json 偶发只给 text 不给 segments（短音频常见）：退化为单段，时间 0 起
        fallback_text = (payload or {}).get("text", "").strip()
        if fallback_text:
            out = [{"start": 0.0, "end": 0.0, "text": fallback_text}]
        else:
            print("[视频转录] Groq 分段转写返回空分段", file=sys.stderr)
    return out


def transcribe_via_groq(url, source_hint=None):
    """从视频 URL 提取转写文本（第 2 级兜底）。失败/超限/异常 → 返回空串。"""
    if not url:
        return ""
    api_key = _groq_api_key()
    if not api_key:
        return ""
    tmpdir = tempfile.mkdtemp(prefix="groq_transcribe_")
    try:
        mp3 = _download_audio(url, tmpdir)
        if not mp3:
            return ""
        return _post_audio_to_groq(mp3, api_key)
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def transcribe_local_file(video_path, source_hint=None):
    """从本地视频文件提取转写文本（ffmpeg 抽音频 → Groq whisper）。

    供 video_to_article.py 处理网盘/本地下载的视频。失败/超限/异常 → 返回空串。
    """
    if not video_path or not os.path.exists(video_path):
        print(f"[视频转录] 本地视频不存在：{video_path}", file=sys.stderr)
        return ""
    api_key = _groq_api_key()
    if not api_key:
        return ""
    tmpdir = tempfile.mkdtemp(prefix="groq_local_")
    try:
        mp3 = _extract_audio_local(video_path, tmpdir)
        if not mp3:
            return ""
        return _post_audio_to_groq(mp3, api_key)
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def transcribe_via_groq_segments(url, source_hint=None):
    """从视频 URL 提取带时间戳的分段转写（第 2 级兜底）。失败 → 返回 []。"""
    if not url:
        return []
    api_key = _groq_api_key()
    if not api_key:
        return []
    tmpdir = tempfile.mkdtemp(prefix="groq_transcribe_")
    try:
        mp3 = _download_audio(url, tmpdir)
        if not mp3:
            return []
        return _post_audio_to_groq_segments(mp3, api_key)
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


def transcribe_local_file_segments(video_path, source_hint=None):
    """从本地视频文件提取带时间戳的分段转写（ffmpeg 抽音频 → Groq verbose_json）。

    供 video_to_article.py 标注时间点。失败/超限/异常 → 返回 []。
    """
    if not video_path or not os.path.exists(video_path):
        print(f"[视频转录] 本地视频不存在：{video_path}", file=sys.stderr)
        return []
    api_key = _groq_api_key()
    if not api_key:
        return []
    tmpdir = tempfile.mkdtemp(prefix="groq_local_")
    try:
        mp3 = _extract_audio_local(video_path, tmpdir)
        if not mp3:
            return []
        return _post_audio_to_groq_segments(mp3, api_key)
    finally:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
