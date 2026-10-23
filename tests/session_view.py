"""Conversation-only projection for existing generation behavior tests.

Storage schema and metadata are asserted separately in test_records.py.
"""
import copy
from chatwell.storage import read_json as read_record


def read_json(path):
    record = read_record(path)
    if record.get('schema_version') != '1.0':
        return record
    c = record['conversation']
    initial = c.get('initialization') or {}
    conversation = {'situation': '', 'goal': '', 'non_participants': [], 'beginning_narration': '',
                    **(initial.get('output') or {}), 'turns': [],
                    'ending_narration': ((c.get('ending') or {}).get('output') or {}).get('ending_narration', ''),
                    'stop_reason': c['stop_reason']}
    for turn in c['turns']:
        conversation['turns'].append({'character': turn['character']['output'], **turn['coordinator']['output']})
    for key in ('pending_turn', 'pending_initialization', 'pending_ending', 'ending_error'):
        if key in c:
            conversation[key] = copy.deepcopy(c[key])
    if c.get('ending_error'):
        conversation['ending_narration'] = ''
    pending = conversation.get('pending_turn')
    if pending and 'index' in pending:
        character = pending['character'].get('output') or {}
        conversation['pending_turn'] = {'name': character.get('name'),
                                        'character': {k: v for k, v in character.items() if k != 'name'}}
        if 'coordinator' in pending:
            conversation['pending_turn']['coordinator'] = pending['coordinator'].get('output')
    return {key: copy.deepcopy(record[key]) for key in ('id', 'status', 'timestamps', 'config', 'characters')} | {'conversation': conversation}
