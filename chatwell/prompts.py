import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'prompts'

INITIAL_SCHEMA = {
    'type': 'object',
    'properties': {
        'situation': {'type': 'string'},
        'goal': {'type': 'string'},
        'non_participants': {'type': 'array', 'items': {'type': 'string'}},
        'beginning_narration': {'type': 'string'},
    },
    'required': ['situation', 'goal', 'non_participants', 'beginning_narration'],
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


def update_schema(names):
    return {
        'type': 'object',
        'properties': {
            'narration': {'type': 'string'},
            'current_situation': {'type': 'string'},
            'progress': {'type': 'string', 'enum': list(PROGRESS)},
            'progress_reason': {'type': 'string'},
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


def messages(stage, data):
    instructions = (ROOT / f'{stage}.txt').read_text(encoding='utf-8')
    if stage == 'character':
        # Only the acting character moves into system; history remains structured data.
        instructions += '\nCharacter definition (JSON data):\n' + json.dumps(data['character'], ensure_ascii=False)
        data = {key: value for key, value in data.items() if key != 'character'}
    return [
        {'role': 'system', 'content': instructions},
        {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)},
    ]
