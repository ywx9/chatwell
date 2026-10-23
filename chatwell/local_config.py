"""Explicit local helper configuration, relative to the settings file."""
import os
from pathlib import Path

LOCAL_DEFAULTS = {'SELF_MANAGED_LLAMA': 'false', 'LOCAL_GGUF_PATH': '',
                  'LOCAL_LLAMA_CONTEXT': '16384', 'LOCAL_LLAMA_GPU_LAYERS': 'auto',
                  'LOCAL_LLAMA_START_TIMEOUT': '3600'}


def read_values(path, required=True):
    path = Path(path).resolve()
    if required and not path.is_file():
        raise ValueError(f'{path.name} がありません。.env.example またはcheck-self-managed-llama.pyで作成してください。')
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


def local_values(path):
    values = {**LOCAL_DEFAULTS, **read_values(path)}
    for key in LOCAL_DEFAULTS:
        if key in os.environ:
            values[key] = os.environ[key]
    enabled = values['SELF_MANAGED_LLAMA'].lower()
    if enabled not in ('true', 'false'):
        raise ValueError('SELF_MANAGED_LLAMA は true / false が必要です。')
    values['SELF_MANAGED_LLAMA'] = enabled == 'true'
    return values
