"""Explicit views: coordinator-only fields never enter a character prompt."""
import copy


def character_history(turns, name):
    history = []
    for turn in turns:
        original = turn['character']
        visible = {key: original[key] for key in ('name', 'speech', 'expression')}
        visible['narration'] = turn['narration']
        if original['name'] == name:
            visible['thought'] = original['thought']
        history.append(visible)
    return history


def recent_turns(conversation):
    return conversation['turns'][conversation.get('compression', {}).get('through_turn', -1) + 1:]


def scene_context(conversation):
    memory = conversation.get('compression')
    if memory is None:
        return {'beginning_narration': conversation['beginning_narration']}
    return {'memory': {'through_turn': memory['through_turn'],
                       'facts': copy.deepcopy(memory['public_facts'])}}


def character_input(conversation, character, characters, language):
    data = {
        **character_context(conversation, character['name']),
        'character': {key: copy.deepcopy(value) for key, value in character.items() if key != 'profile'},
        'participants': [
            {'name': c['name']} if c['name'] == character['name']
            else {key: c[key] for key in ('name', 'profile')}
            for c in characters
        ],
        'non_participants': list(conversation['non_participants']),
        'language': language,
    }
    return data


def character_context(conversation, name):
    # Compression takes precedence: an empty remaining history is still a continuation.
    if conversation.get('compression') is not None:
        return {
            **scene_context(conversation),
            'private_memory': copy.deepcopy(conversation['compression']['private_thoughts'].get(name, [])),
            'history': character_history(recent_turns(conversation), name),
        }
    if not conversation['turns']:
        return {'beginning_narration': conversation['beginning_narration'], 'history': []}
    return {
        'beginning_narration': conversation['beginning_narration'],
        'history': character_history(conversation['turns'], name),
    }
