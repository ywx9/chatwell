import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'prompts'

INITIAL_SCHEMA = {
    'type': 'object',
    'properties': {
        'situation': {'type': 'string'},
        'non_participants': {'type': 'array', 'items': {'type': 'string'}},
        'beginning_narration': {'type': 'string'},
    },
    'required': ['situation', 'non_participants', 'beginning_narration'],
    'additionalProperties': False,
}
ENDING_SCHEMA = {
    'type': 'object',
    'properties': {'ending_narration': {'type': 'string', 'minLength': 1}},
    'required': ['ending_narration'],
    'additionalProperties': False,
}
TURN_SCHEMA = {
    'type': 'object',
    'properties': {key: {'type': 'string'} for key in ('speech', 'thought', 'narration', 'expression')},
    'required': ['speech', 'thought', 'narration', 'expression'],
    'additionalProperties': False,
}


PROGRESS = ('Approaching', 'Unchanged', 'Diverging', 'Achieved')


def update_schema(names, goal_names=()):
    goal_keys = list(goal_names)
    schema = {
        'type': 'object',
        'properties': {
            'narration': {'type': 'string'},
            'current_situation': {'type': 'string'},
            'progress': {
                'type': 'object',
                'properties': {key: {'type': 'string', 'enum': list(PROGRESS)} for key in goal_keys},
                'required': goal_keys, 'additionalProperties': False,
            },
            'progress_reason': {
                'type': 'object',
                'properties': {key: {'type': 'string', 'minLength': 1} for key in goal_keys},
                'required': goal_keys, 'additionalProperties': False,
            },
            'speaker_recommendation': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string', 'enum': list(names)},
                    'confidence': {'type': 'number', 'minimum': 0, 'maximum': 1},
                },
                'required': ['name', 'confidence'],
                'additionalProperties': False,
            },
        },
        'required': ['narration', 'current_situation', 'progress', 'progress_reason', 'speaker_recommendation'],
        'additionalProperties': False,
    }
    return schema


def messages(stage, data):
    instructions = (ROOT / f'{stage}.txt').read_text(encoding='utf-8')
    if stage == 'character':
        if 'memory' in data:
            phase = 'compressed'
        elif not data['history']:
            phase = 'initial'
        else:
            phase = 'continuing'
        instructions += '\n' + (ROOT / f'character_{phase}.txt').read_text(encoding='utf-8')
        # Only the acting character moves into system; history remains structured data.
        instructions += '\nCharacter definition (JSON data):\n' + json.dumps(data['character'], ensure_ascii=False)
        data = {key: value for key, value in data.items() if key != 'character'}
    elif stage == 'update':
        history_mode = 'compressed' if 'memory' in data else 'uncompressed'
        instructions += '\n' + (ROOT / f'update_{history_mode}.txt').read_text(encoding='utf-8')
        evaluation = 'update_evaluation' if data['goals'] else 'update_no_goals'
        instructions += '\n' + (ROOT / f'{evaluation}.txt').read_text(encoding='utf-8')
    return [
        {'role': 'system', 'content': instructions},
        {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)},
    ]
