"""读取 Codex CLI 的认证信息，调用 OpenAI Responses API 进行翻译和总结。"""
import json
import os
import urllib.request
import urllib.error
import re

_CONFIG_CACHE = None


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
    """从 Codex CLI 的 auth.json 或环境变量读取 API Key。

    优先使用当前 provider 声明的 env_key 环境变量，其次回退到
    OPENAI_API_KEY / CODEX_API_KEY，最后读取 ~/.codex/auth.json。
    """
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

    跟随 config.toml 顶层的 model_provider：使用 OpenAI（含 timicc.com
    中转）时走 model_providers.OpenAI.base_url；使用 deepseek 时走
    model_providers.deepseek.base_url（https://api.deepseek.com）。
    """
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
    """读取 Codex 默认模型。"""
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


def call_llm(messages, model=None, temperature=0.3, max_tokens=4000, json_mode=False):
    """
    调用 OpenAI Responses API。

    Args:
        messages: list of {"role": "system"/"user", "content": str}
        model: 模型名，默认从 Codex 配置读取
        temperature: 0-1
        max_tokens: 最大输出 token
        json_mode: 是否强制输出 JSON

    Returns:
        str: LLM 的文本输出
    """
    _ensure_auth()
    model = model or os.environ.get('LLM_MODEL') or DEFAULT_MODEL or 'gpt-4.1-mini'

    # Responses API 的 input 格式
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

    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(
        f"{API_BASE}/responses",
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        try:
            timeout = max(1.0, float(os.environ.get("LLM_API_TIMEOUT_SECONDS", "120")))
        except ValueError:
            timeout = 120.0
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"LLM API HTTP {e.code}: {body[:500]}")

    if res.get('error'):
        raise RuntimeError(f"LLM API Error: {res['error']}")

    # 从 Responses API 输出中提取文本
    try:
        for output in res.get('output', []):
            if output.get('type') == 'message':
                for content in output.get('content', []):
                    if content.get('type') == 'output_text':
                        return content['text'].strip()
        # 兜底
        return res.get('output_text', '').strip()
    except Exception as e:
        raise RuntimeError(f"解析 LLM 响应失败: {e}, response={res}")


def extract_json_block(text):
    """从 LLM 输出中提取 JSON 代码块或原始 JSON。"""
    match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()
