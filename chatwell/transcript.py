"""Append-only human-readable session logs and validated conversation text."""
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def append(path, text):
    with Path(path).open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def field(name, value):
    text = 'null' if value is None else str(value)
    return f'{name}: ' + text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\n  ') + '\n'


def changes(value, previous, prefix=''):
    """Render changed fields, retaining their full path and event order."""
    if value == previous:
        return ''
    if isinstance(value, dict):
        old = previous if isinstance(previous, dict) else {}
        result = ''.join(changes(item, old.get(key, _MISSING), f'{prefix}.{key}' if prefix else key)
                         for key, item in value.items())
        result += ''.join(field(f'{prefix}.{key}', '(removed)') for key in old if key not in value)
        return result or field(prefix, '{}')
    if isinstance(value, list):
        old = previous if isinstance(previous, list) else []
        result = ''.join(changes(item, old[index] if index < len(old) else _MISSING,
                                 f'{prefix}[{index}]') for index, item in enumerate(value))
        result += ''.join(field(f'{prefix}[{index}]', '(removed)') for index in range(len(value), len(old)))
        return result or field(prefix, '[]')
    return field(prefix, value)


_MISSING = object()


def log_event(session_path, title, values):
    stamp = datetime.now(timezone.utc).isoformat()
    append(Path(session_path).with_suffix('.log'),
           f'\n[{stamp}] {title}\n' + changes(values, _MISSING))


class Transcript:
    def __init__(self, session_path, config, characters):
        self.path = Path(session_path)
        self.previous = {}
        # Input data is JSON only in this header; subsequent events are text.
        append(self.path.with_suffix('.log'), 'Input JSON:\n' + json.dumps(
            {'config': config, 'characters': characters}, ensure_ascii=False, indent=2,
            allow_nan=False) + '\n')
        append(self.path.with_suffix('.txt'), '')

    def record(self, session):
        current = {key: value for key, value in session.items() if key not in ('config', 'characters')}
        delta = changes(current, self.previous)
        if delta:
            stamp = session['timestamps']['updated']
            append(self.path.with_suffix('.log'), f'\n[{stamp}] Session update\n' + delta)
            self.previous = copy.deepcopy(current)

    def opening(self, text):
        append(self.path.with_suffix('.txt'), field('Opening', text) + '\n')

    def turn(self, character, narration):
        text = ''.join(field(label, character[key]) for label, key in (
            ('Name', 'name'), ('Expression', 'expression'), ('Speech', 'speech'), ('Thought', 'thought')))
        append(self.path.with_suffix('.txt'), text + field('Narration', narration) + '\n')

    def ending(self, text):
        append(self.path.with_suffix('.txt'), field('Ending', text))
