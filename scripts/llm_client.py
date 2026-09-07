"""读取 skill 本地 .env 的 LLM_* 配置，调用 OpenAI 兼容协议（/responses 或 /chat/completions）进行翻译和总结。"""
import json
import os
import time
import http.client
import urllib.request
import urllib.error
import re

_CONFIG_CACHE = None
_ENV_FILE_TRIED = False


def _env_file_path():
    """返回 skill 根目录下的 .env 路径（可用 LLM_ENV_FILE 覆盖）。"""
    override = os.environ.get('LLM_ENV_FILE')
    if override:
        return override
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(script_dir), '.env')


def _load_env_file():
    """从 skill 根目录 .env 读取 LLM_* 变量注入 os.environ（不覆盖已有环境变量）。

    让 skill 的 LLM 端点/模型与 Codex CLI（~/.codex/config.toml）解耦：
    只有本 skill 的 pipeline 读到 .env 里当前激活的 provider 配置（商汤 sensenova 或 timicc 中转，单文件注释切换），
    Codex 改动默认模型不影响这里。
    """
    global _ENV_FILE_TRIED
    if _ENV_FILE_TRIED:
        return
    _ENV_FILE_TRIED = True
    env_path = _env_file_path()
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, val)
    except Exception:
        pass


def _load_codex_config():
    """读取 ~/.codex/config.toml（带缓存），失败时返回空 dict。"""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    config = {}
    codex_home = os.path.expanduser('~/.codex')
    config_path = os.path.join(codex_home, 'config.toml')
    if os.path.exists(config_path):
        try:
            import tomllib
            with open(config_path, 'rb') as f:
                config = tomllib.load(f)
        except Exception:
            config = {}
    _CONFIG_CACHE = config
    return config


def _active_provider(config):
    """返回当前生效的 provider 名称及其配置块。

    跟随 config.toml 顶层的 model_provider（如 OpenAI、deepseek），
    让流水线与 Codex 当前使用的模型服务保持一致。
    """
    name = config.get('model_provider') or 'OpenAI'
    providers = config.get('model_providers', {})
    provider = providers.get(name) or providers.get('OpenAI') or {}
    return name, provider


def _load_openai_api_key():
    """从环境变量、Codex CLI 的 auth.json 或环境变量读取 API Key。

    优先使用 LLM_API_KEY（方案 A：skill 独立配置），其次当前 provider 声明的
    env_key 环境变量，再次回退到 OPENAI_API_KEY / CODEX_API_KEY，
    最后读取 ~/.codex/auth.json。
    """
    _load_env_file()
    explicit = os.environ.get('LLM_API_KEY')
    if explicit:
        return explicit
    _, provider = _active_provider(_load_codex_config())
    env_key_name = provider.get('env_key')
    env_key = os.environ.get(env_key_name) if env_key_name else None
    env_key = env_key or os.environ.get('OPENAI_API_KEY') or os.environ.get('CODEX_API_KEY')
    if env_key:
        return env_key

    codex_home = os.path.expanduser('~/.codex')
    auth_path = os.path.join(codex_home, 'auth.json')
    if os.path.exists(auth_path):
        with open(auth_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        key = data.get('OPENAI_API_KEY') or data.get('api_key') or data.get('CODEX_API_KEY')
        if key:
            return key

    raise RuntimeError(
        "未找到 OpenAI API Key。请设置 OPENAI_API_KEY 环境变量，或运行 `codex login` 登录。"
    )


def _load_api_base():
    """读取当前 provider 的 API base URL。

    优先使用 LLM_BASE_URL（方案 A：skill 独立配置）。未设置时跟随
    config.toml 顶层的 model_provider：使用 OpenAI（含 timicc.com 中转）
    时走 model_providers.OpenAI.base_url；使用 deepseek 时走
    model_providers.deepseek.base_url（https://api.deepseek.com）。
    """
    _load_env_file()
    explicit = os.environ.get('LLM_BASE_URL')
    if explicit:
        return explicit.rstrip('/')
    _, provider = _active_provider(_load_codex_config())
    base_url = provider.get('base_url')
    if base_url:
        return base_url.rstrip('/')
    # 兼容旧配置
    base_url = _load_codex_config().get('api_base_url')
    if base_url:
        return base_url.rstrip('/')
    return 'https://api.openai.com'


def _load_default_model():
    """读取默认模型：优先 LLM_MODEL（方案 A：skill 独立配置），否则读 Codex 默认模型。"""
    _load_env_file()
    explicit = os.environ.get('LLM_MODEL')
    if explicit:
        return explicit
    return _load_codex_config().get('model')


API_KEY = None
API_BASE = None
DEFAULT_MODEL = None


def _ensure_auth():
    global API_KEY, API_BASE, DEFAULT_MODEL
    if API_KEY is None:
        API_KEY = _load_openai_api_key()
        API_BASE = _load_api_base()
        DEFAULT_MODEL = _load_default_model()


# -------------------- 协议自适应（responses / chat_completions）--------------------
# 默认 LLM_API_MODE=auto：先试 /responses，若提供方不支持（404/协议错误）自动降级 /chat/completions。
# 显式设 LLM_API_MODE=responses 或 chat 可锁定单一协议。选择结果记到 _RESOLVED_PROTOCOL，
# 避免每次调用都先撞一次 404。timicc.com 两种都支持；商汤 sensenova 仅 /chat/completions。
_RESOLVED_PROTOCOL = None


class _ProtocolUnsupported(Exception):
    """表示提供方不支持当前协议端点（如 /responses 返回 404 / NOT_FOUND）。"""


def _http_post_json(path, payload):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }
    last_error = None
    for attempt in range(3):
        req = urllib.request.Request(f"{API_BASE}{path}", data=data, headers=headers, method="POST")
        try:
            try:
                timeout = max(1.0, float(os.environ.get("LLM_API_TIMEOUT_SECONDS", "120")))
            except ValueError:
                timeout = 120.0
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            # HTTP 4xx/5xx 是协议错误，重试无意义，直接抛（/responses 不支持降级判断）
            body = e.read().decode('utf-8', errors='replace')
            code = e.code
            low = body.lower()
            if code in (404, 405, 501) or '"not_found"' in low or '"not found"' in low:
                raise _ProtocolUnsupported(f"HTTP {code}: {body[:200]}") from e
            raise RuntimeError(f"LLM API HTTP {code}: {body[:500]}")
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, OSError) as e:
            # 连接类瞬态错误（服务端断连/读超时/网络抖动），退避后重试
            last_error = e
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"LLM API 连接失败: {e}") from e
    raise RuntimeError(f"LLM API 连接失败: {last_error}") from last_error


def _parse_response_text(res):
    """从通用响应里尽量抽出文本：兼容 Responses API 与 Chat Completions 两种结构。"""
    # Chat Completions: choices[0].message.content / reasoning_content
    choices = res.get('choices')
    if isinstance(choices, list) and choices:
        msg = choices[0].get('message', {})
        if msg.get('content'):
            return msg['content'].strip()
        if msg.get('reasoning_content'):
            return msg['reasoning_content'].strip()
        if msg.get('reasoning'):
            return msg['reasoning'].strip()
    # Responses API: output[].content[].output_text
    if res.get('output'):
        for output in res['output']:
            if output.get('type') == 'message':
                for content in output.get('content', []):
                    if content.get('type') == 'output_text':
                        return content['text'].strip()
        return res.get('output_text', '').strip()
    # 兜底
    return res.get('output_text', '').strip() if isinstance(res, dict) else ''


def _call_responses_api(messages, model, temperature, max_tokens, json_mode):
    """调用 OpenAI Responses API（/responses）。"""
    input_messages = []
    for m in messages:
        input_messages.append({
            "role": m["role"],
            "content": [{"type": "input_text", "text": m["content"]}]
        })
    payload = {
        "model": model,
        "input": input_messages,
        "temperature": temperature,
        "max_output_tokens": max_tokens,
    }
    if json_mode:
        payload["text"] = {"format": {"type": "json_object"}}
    res = _http_post_json("/responses", payload)
    if res.get('error'):
        raise RuntimeError(f"LLM API Error: {res['error']}")
    return _parse_response_text(res)


def _call_chat_completions_api(messages, model, temperature, max_tokens, json_mode):
    """调用 OpenAI Chat Completions API（/chat/completions）。"""
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    res = _http_post_json("/chat/completions", payload)
    if res.get('error'):
        raise RuntimeError(f"LLM API Error: {res['error']}")
    return _parse_response_text(res)


def _resolve_protocol():
    """按环境变量返回锁定协议；auto 时返回 None（由 call_llm 靠异常实测决定）。

    - LLM_API_MODE=responses  → 恒用 /responses
    - LLM_API_MODE=chat       → 恒用 /chat/completions
    - LLM_API_MODE=auto(默认) → 返回 None，call_llm 先试 /responses，协议不支持时降级 /chat，
                                并把结果记到 _RESOLVED_PROTOCOL，避免每次调用都撞一次 404。
    """
    global _RESOLVED_PROTOCOL
    if _RESOLVED_PROTOCOL is not None:
        return _RESOLVED_PROTOCOL
    mode = os.environ.get('LLM_API_MODE', 'auto').strip().lower()
    if mode == 'responses':
        _RESOLVED_PROTOCOL = 'responses'
    elif mode == 'chat':
        _RESOLVED_PROTOCOL = 'chat'
    return _RESOLVED_PROTOCOL


def call_llm(messages, model=None, temperature=0.3, max_tokens=4000, json_mode=False):
    """
    调用 LLM，协议自适应（/responses 或 /chat/completions）。

    Args:
        messages: list of {"role": "system"/"user", "content": str}
        model: 模型名，默认从 .env/Codex 配置读取
        temperature: 0-1
        max_tokens: 最大输出 token
        json_mode: 是否强制输出 JSON

    Returns:
        str: LLM 的文本输出
    """
    _ensure_auth()
    model = model or os.environ.get('LLM_MODEL') or DEFAULT_MODEL or 'gpt-4.1-mini'

    protocol = _resolve_protocol()
    if protocol == 'chat':
        return _call_chat_completions_api(messages, model, temperature, max_tokens, json_mode)
    if protocol == 'responses':
        return _call_responses_api(messages, model, temperature, max_tokens, json_mode)
    # auto：先试 /responses，协议不支持（404）时降级 /chat 并永久记录协议
    global _RESOLVED_PROTOCOL
    try:
        result = _call_responses_api(messages, model, temperature, max_tokens, json_mode)
        _RESOLVED_PROTOCOL = 'responses'
        return result
    except _ProtocolUnsupported:
        _RESOLVED_PROTOCOL = 'chat'
        return _call_chat_completions_api(messages, model, temperature, max_tokens, json_mode)


def extract_json_block(text):
    """从 LLM 输出中提取 JSON 代码块或原始 JSON。"""
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()
