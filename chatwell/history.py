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


def character_input(conversation, character, characters, language):
    return {
        'situation': conversation['situation'],
        'current_situation': conversation['turns'][-1]['current_situation'] if conversation['turns'] else conversation['situation'],
        'beginning_narration': conversation['beginning_narration'],
        'character': copy.deepcopy(character),
        'participants': [{key: c[key] for key in ('name', 'profile')} for c in characters],
        'non_participants': list(conversation['non_participants']),
        'language': language,
        'history': character_history(conversation['turns'], character['name']),
    }
