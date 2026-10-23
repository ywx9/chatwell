"""Shared settings: CLI > environment > explicitly selected file > defaults."""
import math
import os
from pathlib import Path
from .local_config import LOCAL_DEFAULTS, read_values

DEFAULTS = {
    'CHATWELL_ENDPOINT': 'http://127.0.0.1:8080',
    'CHATWELL_REQUEST_TIMEOUT': '3600',
    'CHATWELL_MAX_TOKENS': '6144',
    'CHATWELL_ENABLE_THINKING': 'true',
    'CHATWELL_REASONING_FORMAT': 'auto',
    'CHATWELL_REASONING_IN_CONTENT': 'false',
}


def load(args):
    values = dict(DEFAULTS)
    if args.settings:
        configured = read_values(args.settings)
        unknown = configured.keys() - {*DEFAULTS, *LOCAL_DEFAULTS, 'CHATWELL_MODEL', 'CHATWELL_API_KEY'}
        if unknown:
            raise ValueError('未対応の設定: ' + ', '.join(sorted(unknown)))
        values.update({key: value for key, value in configured.items() if key not in LOCAL_DEFAULTS})
    for key in {*DEFAULTS, 'CHATWELL_MODEL', 'CHATWELL_API_KEY'}:
        if key in os.environ:
            values[key] = os.environ[key]
    for field, key in [('endpoint', 'CHATWELL_ENDPOINT'), ('timeout', 'CHATWELL_REQUEST_TIMEOUT'),
                       ('model', 'CHATWELL_MODEL'), ('max_tokens', 'CHATWELL_MAX_TOKENS')]:
        value = getattr(args, field, None)
        if value is not None:
            values[key] = value
    timeout = float(values['CHATWELL_REQUEST_TIMEOUT'])
    tokens = int(values['CHATWELL_MAX_TOKENS'])
    if not math.isfinite(timeout) or timeout <= 0 or tokens < 1:
        raise ValueError('timeoutとmax-tokensは正の値を指定してください。')
    def boolean(key):
        value = values[key].lower()
        if value not in ('true', 'false'):
            raise ValueError(f'{key} は true / false を指定してください。')
        return value == 'true'
    reasoning_format = values['CHATWELL_REASONING_FORMAT']
    if reasoning_format not in ('auto', 'none', 'deepseek', 'deepseek-legacy'):
        raise ValueError('CHATWELL_REASONING_FORMAT が不正です。')
    in_content = boolean('CHATWELL_REASONING_IN_CONTENT')
    if in_content:
        raise ValueError('構造化JSONのためCHATWELL_REASONING_IN_CONTENT=falseが必要です。')
    return dict(endpoint=values['CHATWELL_ENDPOINT'], timeout=timeout,
                model=values.get('CHATWELL_MODEL') or None, api_key=values.get('CHATWELL_API_KEY') or None,
                default_max_tokens=tokens,
                inference_options={'chat_template_kwargs': {'enable_thinking': boolean('CHATWELL_ENABLE_THINKING')},
                                   'reasoning_format': reasoning_format, 'reasoning_in_content': False})
