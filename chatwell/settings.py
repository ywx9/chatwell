"""Read plain KEY=VALUE configuration; no installation or shell expansion."""
import math
import os
from pathlib import Path


DEFAULTS = {
    'LLM_ENDPOINT': 'http://127.0.0.1:8080',
    'LLM_REQUEST_TIMEOUT': '3600',
    'LLM_CONTEXT_SIZE': '16384',
    'LLM_MAX_TOKENS': '6144',
    'LLM_ENABLE_THINKING': 'true',
    'LLM_REASONING_FORMAT': 'auto',
    'LLM_REASONING_IN_CONTENT': 'false',
}

APP_DEFAULTS = {
    'CHATWELL_HOST': '127.0.0.1',
    'CHATWELL_PORT': '8090',
    'CHATWELL_DATA_DIR': 'data',
}

def read_values(path):
    path = Path(path).resolve()
    values = {}
    if path.exists():
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, sep, value = line.partition('=')
            if not sep:
                raise ValueError(f'{path.name}:{number}: KEY=VALUE形式が必要です。')
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key.strip()] = value
    return values


def read_settings(path):
    configured = read_values(path) if path else {}
    known = {*DEFAULTS, *APP_DEFAULTS, 'LLM_MODEL', 'LLM_API_KEY'}
    unknown = configured.keys() - known
    if unknown:
        raise ValueError('未対応の設定: ' + ', '.join(sorted(unknown)))
    result = {**DEFAULTS, **APP_DEFAULTS, **{k: v for k, v in configured.items() if k in known}}
    result.update({key: os.environ[key] for key in known if key in os.environ})
    return result


def inference_settings(values):
    timeout = float(values['LLM_REQUEST_TIMEOUT'])
    tokens = int(values['LLM_MAX_TOKENS'])
    context_size = int(values['LLM_CONTEXT_SIZE'])
    if not math.isfinite(timeout) or timeout <= 0 or tokens < 1 or context_size < 1:
        raise ValueError('timeout・max-tokens・context-sizeは正の値を指定してください。')
    def boolean(key):
        value = values[key].lower()
        if value not in ('true', 'false'):
            raise ValueError(f'{key} は true / false を指定してください。')
        return value == 'true'
    reasoning_format = values['LLM_REASONING_FORMAT']
    if reasoning_format not in ('auto', 'none', 'deepseek', 'deepseek-legacy'):
        raise ValueError('LLM_REASONING_FORMAT が不正です。')
    in_content = boolean('LLM_REASONING_IN_CONTENT')
    if in_content:
        raise ValueError('構造化JSONのためLLM_REASONING_IN_CONTENT=falseが必要です。')
    return dict(endpoint=values['LLM_ENDPOINT'], timeout=timeout, context_size=context_size,
                model=values.get('LLM_MODEL') or None, api_key=values.get('LLM_API_KEY') or None,
                default_max_tokens=tokens,
                inference_options={'chat_template_kwargs': {'enable_thinking': boolean('LLM_ENABLE_THINKING')},
                                   'reasoning_format': reasoning_format, 'reasoning_in_content': False})
