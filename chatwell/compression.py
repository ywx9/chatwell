"""Cumulative history summaries; original session turns are never deleted."""
import copy
from . import prompts, validation
from .history import character_history

FACTS_SCHEMA = {
    'type': 'object',
    'properties': {'facts': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}}},
    'required': ['facts'], 'additionalProperties': False,
}


def facts(value):
    validation.keys(value, ('facts',))
    if not isinstance(value['facts'], list):
        raise ValueError('Compression facts must be an array')
    for fact in value['facts']:
        validation.text(fact, 'compression fact')
    return value['facts']


def compress(conversation, characters, complete, index, prompt_tokens):
    previous = conversation.get('compression', {})
    j = previous.get('through_turn', -1)
    k = j + (index - j) // 2
    if k <= j:
        return False  # The older half contains no additional whole turn yet.
    turns = conversation['turns'][j + 1:k + 1]
    public = character_history(turns, None)
    public = [{'index': j + 1 + offset, **turn} for offset, turn in enumerate(public)]
    data = {'previous_facts': previous.get('public_facts', []), 'turns': public}
    if j == -1:
        data['initial_scene'] = {key: conversation[key] for key in ('situation', 'beginning_narration')}
    metadata = {'from_turn': j + 1, 'through_turn': k, 'trigger_prompt_tokens': prompt_tokens}
    output = complete(f'compressing_history:{index}:public',
                      prompts.messages('compress_public', data), FACTS_SCHEMA,
                      details={**metadata, 'scope': 'public'})
    public_facts = facts(output)
    private = {}
    for character in characters:
        name = character['name']
        thoughts = [{'index': j + 1 + offset, 'thought': turn['character']['thought']}
                    for offset, turn in enumerate(turns)
                    if turn['character']['name'] == name and turn['character']['thought'].strip()]
        prior = previous.get('private_thoughts', {}).get(name, [])
        if not thoughts:
            private[name] = list(prior)
            continue
        data = {'name': name, 'previous_facts': prior, 'thoughts': thoughts}
        output = complete(f'compressing_history:{index}:private:{name}',
                          prompts.messages('compress_private', data), FACTS_SCHEMA,
                          details={**metadata, 'scope': 'private', 'character': name})
        private[name] = facts(output)
    # Commit the new boundary only after every public/private summary is valid.
    conversation['compression'] = copy.deepcopy({
        'through_turn': k, 'public_facts': public_facts, 'private_thoughts': private})
    return True
