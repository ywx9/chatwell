import copy
import logging
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import prompts, validation
from .history import character_input
from .speakers import DEFAULT_MAX_TURNS, RECOMMENDATION_THRESHOLD, select_speaker
from .storage import save


def now():
    return datetime.now(timezone.utc).isoformat()


def run(config, characters, client, output_dir, observer=None, *,
        max_turns=DEFAULT_MAX_TURNS, recommendation_threshold=RECOMMENDATION_THRESHOLD):
    validation.request(config, characters)
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
        raise ValueError('max_turns must be a positive integer')
    if (isinstance(recommendation_threshold, bool) or not isinstance(recommendation_threshold, (int, float))
            or not math.isfinite(recommendation_threshold) or not 0 <= recommendation_threshold <= 1):
        raise ValueError('recommendation_threshold must be in [0, 1]')
    config, characters = copy.deepcopy(config), copy.deepcopy(characters)
    conversation = {'situation': '', 'goal': '', 'non_participants': [], 'beginning_narration': '',
                    'turns': [], 'ending_narration': '', 'stop_reason': ''}
    session = {
        'id': str(uuid.uuid4()), 'status': 'Queued',
        'timestamps': {'queued': now(), 'started': None, 'ended': None},
        'config': config, 'characters': characters, 'conversation': conversation,
    }
    path = Path(output_dir) / (session['id'] + '.json')

    def persist(status):
        session['status'] = status
        save(path, session)
        logging.info('%s turns=%d %s', status, len(conversation['turns']), path)
        if observer:
            observer(path)

    persist('Queued')
    try:
        session['timestamps']['started'] = now()
        persist('Starting')
        options = {'temperature': 0.8, 'max_tokens': 512, **config.get('llm_config', {})}
        names = [c['name'] for c in characters]
        data = {'situation': config['situation'], 'goal': config.get('goal'), 'language': config['language'],
                'characters': [{k: c[k] for k in ('name', 'profile', 'relationships')} for c in characters]}
        initial = client.complete(prompts.messages('coordinator', data), options, prompts.INITIAL_SCHEMA)
        conversation['pending_initialization'] = copy.deepcopy(initial)
        persist('Starting')
        validation.initialization(initial)
        if set(names).intersection(initial['non_participants']):
            raise ValueError('Coordinator classified a participant as absent')
        if config.get('goal'):
            validation.text(initial['goal'], 'refined goal')
        elif initial['goal']:
            raise ValueError('Coordinator invented an unspecified goal')
        conversation.update(copy.deepcopy(initial))
        conversation['non_participants'] = sorted(set(initial['non_participants']) |
                                                ({n for c in characters for n in c['relationships']} - set(names)))
        del conversation['pending_initialization']
        persist('Running')
        speaker = characters[0]
        schema = prompts.update_schema(names)
        for _ in range(max_turns):
            data = character_input(conversation, speaker, characters, config['language'])
            generated = client.complete(prompts.messages('character', data), options, prompts.TURN_SCHEMA)
            # Persist received output before validation or the next API call.
            conversation['pending_turn'] = {'name': speaker['name'], 'character': copy.deepcopy(generated)}
            persist('Running')
            validation.turn(generated)
            original = {'name': speaker['name'], **copy.deepcopy(generated)}
            data = {key: copy.deepcopy(conversation[key]) for key in
                    ('situation', 'goal', 'beginning_narration', 'non_participants')}
            data.update(language=config['language'], characters=characters,
                        history=copy.deepcopy(conversation['turns']), character=original,
                        current_situation=conversation['turns'][-1]['current_situation'] if conversation['turns'] else conversation['situation'])
            revised = client.complete(prompts.messages('update', data), options, schema)
            conversation['pending_turn']['coordinator'] = copy.deepcopy(revised)
            persist('Running')
            validation.update(revised)
            # Missing recommendation fields use neutral fallback values.
            revised = copy.deepcopy(revised)
            recommendation = revised.setdefault('speaker_recommendation', {})
            recommendation.setdefault('name', '')
            recommendation.setdefault('confidence', 0.0)
            conversation['turns'].append({'character': original, **revised})
            del conversation['pending_turn']
            persist('Running')
            logging.info('Turn %d speaker=%s progress=%s', len(conversation['turns']), speaker['name'], revised['progress'])
            if config.get('goal') and revised['progress'] == 'Achieved':
                conversation['stop_reason'] = 'goal_achieved'
                break
            if len(conversation['turns']) < max_turns:
                speaker = select_speaker(characters, conversation['turns'], recommendation, recommendation_threshold)
        else:
            conversation['stop_reason'] = 'max_turns_reached'
        session['timestamps']['ended'] = now()
        persist('Ended')
    except (Exception, KeyboardInterrupt) as error:
        conversation['stop_reason'] = 'user_interrupted' if isinstance(error, KeyboardInterrupt) else f'error: {type(error).__name__}: {error}'
        session['timestamps']['ended'] = now()
        persist('Failed')
        raise
    return path
