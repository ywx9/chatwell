import math


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")


def keys(value, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError("Expected an object")
    if set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError(f"Invalid keys: expected {required}, optional {optional}")


def request(config, characters):
    keys(config, ('language', 'situation'), ('goal', 'llm_config', 'coordinator', 'max_turns'))
    for field in ('language', 'situation'):
        text(config[field], field)
    if 'goal' in config:
        text(config['goal'], 'goal')
    if 'max_turns' in config:
        value = config['max_turns']
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError('config.max_turns must be a positive integer')
    coordinator = config.get('coordinator', {})
    keys(coordinator, (), ('narration_style',))
    if 'narration_style' in coordinator:
        text(coordinator['narration_style'], 'coordinator.narration_style')
    options = config.get('llm_config', {})
    if not isinstance(options, dict):
        raise ValueError('llm_config must be an object')
    # Transport, roles and output constraints belong to the application.
    allowed = {'temperature', 'max_tokens', 'top_p', 'top_k', 'min_p', 'seed', 'repeat_penalty', 'reasoning_budget_tokens'}
    if options.keys() - allowed:
        raise ValueError(f'Unsupported llm_config keys: {options.keys() - allowed}')
    for key, value in options.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key} must be a finite number')
        if key in ('max_tokens', 'top_k', 'seed', 'reasoning_budget_tokens') and not isinstance(value, int):
            raise ValueError(f'{key} must be an integer')
        if key == 'reasoning_budget_tokens' and value < -1:
            raise ValueError('reasoning_budget_tokens must be -1 or a nonnegative integer')
        if key == 'max_tokens' and value <= 0:
            raise ValueError('max_tokens must be positive')
    if not isinstance(characters, list) or not characters:
        raise ValueError('At least one character is required')
    names = []
    for character in characters:
        keys(character, ('name', 'profile', 'persona', 'speech', 'relationships'))
        for field in ('name', 'profile', 'persona'):
            text(character[field], field)
        keys(character['speech'], ('first_person', 'second_person', 'default_address', 'style'))
        for value in character['speech'].values():
            text(value, 'speech setting')
        if not isinstance(character['relationships'], dict):
            raise ValueError('relationships must be an object')
        for name, relationship in character['relationships'].items():
            text(name, 'relationship name')
            text(relationship, 'relationship')
        names.append(character['name'])
    if len(set(names)) != len(names):
        raise ValueError('Character names must be unique')


def initialization(value):
    keys(value, ('situation', 'goal', 'non_participants', 'beginning_narration'))
    text(value['situation'], 'situation')
    if not isinstance(value['goal'], str):
        raise ValueError('goal must be a string (empty if unspecified)')
    text(value['beginning_narration'], 'beginning_narration')
    if not isinstance(value['non_participants'], list):
        raise ValueError('non_participants must be an array')
    for name in value['non_participants']:
        text(name, 'non_participant')


def turn(value):
    keys(value, ('speech', 'thought', 'narration', 'expression'))
    for field, content in value.items():
        if not isinstance(content, str):
            raise ValueError(f'{field} must be a string')


def update(value):
    from .prompts import PROGRESS
    keys(value, ('narration', 'current_situation', 'progress', 'progress_reason'), ('speaker_recommendation',))
    if not isinstance(value['narration'], str):
        raise ValueError('narration must be a string')
    text(value['current_situation'], 'current_situation')
    text(value['progress_reason'], 'progress_reason')
    if value['progress'] not in PROGRESS:
        raise ValueError('Invalid progress enum')
    recommendation = value.get('speaker_recommendation', {})
    if not isinstance(recommendation, dict):
        raise ValueError('speaker_recommendation must be an object')
    keys(recommendation, (), ('name', 'confidence'))
    if 'name' in recommendation and not isinstance(recommendation['name'], str):
        raise ValueError('Recommended name must be a string')
    confidence = recommendation.get('confidence', 0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError('confidence must be a finite number in [0, 1]')


def ending(value):
    keys(value, ('ending_narration',))
    text(value['ending_narration'], 'ending_narration')
