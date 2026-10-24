import copy
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from . import prompts, validation
from .history import character_input, character_history, recent_turns, scene_context
from .compression import compress
from .speakers import DEFAULT_MAX_TURNS, RECOMMENDATION_THRESHOLD, select_speaker, candidates
from .storage import save, read_json
from .records import new_session, encode, model_id
from .client import MockClient


def now():
    return datetime.now(timezone.utc).isoformat()


def run(config, characters, client, output_dir, observer=None, *,
        max_turns=None, recommendation_threshold=RECOMMENDATION_THRESHOLD, session_path=None):
    validation.request(config, characters)
    if max_turns is None:
        max_turns = config.get('max_turns', DEFAULT_MAX_TURNS)
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
        raise ValueError('max_turns must be a positive integer')
    if (isinstance(recommendation_threshold, bool) or not isinstance(recommendation_threshold, (int, float))
            or not math.isfinite(recommendation_threshold) or not 0 <= recommendation_threshold <= 1):
        raise ValueError('recommendation_threshold must be in [0, 1]')
    config, characters = copy.deepcopy(config), copy.deepcopy(characters)
    conversation = {'situation': '', 'goal': '', 'non_participants': [], 'beginning_narration': '',
                    'turns': [], 'ending_narration': '', 'stop_reason': ''}
    options = {'temperature': 0.8, 'max_tokens': getattr(client, 'default_max_tokens', 6144), **config.get('llm_config', {})}
    context_size = getattr(client, 'context_size', 16384)
    if options['max_tokens'] >= context_size:
        raise ValueError('max_tokens must be smaller than the configured context size')
    path = Path(session_path) if session_path else new_session(config, characters, output_dir, max_turns)
    session = read_json(path)
    if session['status'] != 'Queued':
        raise ValueError('Only a queued session can be started')
    session['conversation'] = conversation
    calls = {}
    session['runtime'] = {'context_size': context_size, 'max_turns': max_turns, 'recommendation_threshold': recommendation_threshold,
                          'llm_config': options, 'inference_options': getattr(client, 'inference_options', {}),
                          'request_timeout': getattr(client, 'timeout', None),
                          'mode': 'mock' if isinstance(client, MockClient) else 'model',
                          'server': copy.deepcopy(getattr(client, 'provenance', {'management': 'external', 'llama_cpp_version': None,
                                                                              'model_file': None, 'model_sha256': None})),
                          'requested_model': model_id(getattr(client, 'model', None)), 'response_model': None}

    def persist(status):
        session['status'] = status
        session['timestamps']['updated'] = now()
        session['progress']['completed_turns'] = len(conversation['turns'])
        if status in ('Ended', 'Failed'):
            session['progress']['phase'] = status.lower()
        save(path, encode(session, calls))
        logging.info('%s turns=%d %s', status, len(conversation['turns']), path)
        if observer:
            observer(path)

    def complete(kind, messages, schema, name=None, *, details=None):
        phase = kind.split(':')[0]
        session['progress']['phase'] = {'character': 'generating_character', 'coordinator': 'updating_coordinator'}.get(phase, phase)
        record = {'output': None, 'metrics': {'started': now(), 'ended': None, 'duration_ms': None,
                                             'prompt_tokens': None, 'completion_tokens': None, 'finish_reason': None}}
        record.update(details or {})
        calls[kind] = record
        persist('Starting' if kind == 'initializing' else 'Running')
        started = time.monotonic()
        client.last_metrics = {}
        client.response_model = None
        try:
            output = client.complete(messages, options, schema)
            record['output'] = {'name': name, **output} if name and isinstance(output, dict) else copy.deepcopy(output)
            return output
        except (Exception, KeyboardInterrupt) as error:
            record['error'] = 'interrupted' if isinstance(error, KeyboardInterrupt) else type(error).__name__
            raise
        finally:
            record['metrics'].update(getattr(client, 'last_metrics', {}))
            record['metrics'].update(ended=now(), duration_ms=round((time.monotonic() - started) * 1000, 3))
            record['model'] = model_id(getattr(client, 'response_model', None))
            session['runtime']['response_model'] = record['model']
            persist('Starting' if kind == 'initializing' else 'Running')

    persist('Queued')
    try:
        session['timestamps']['started'] = now()
        persist('Starting')
        if hasattr(client, 'probe_metadata'):
            session['runtime']['server'] = client.probe_metadata()
        names = [c['name'] for c in characters]
        data = {'situation': config['situation'], 'goal': config.get('goal'), 'language': config['language'],
                'characters': [{k: c[k] for k in ('name', 'profile', 'relationships')} for c in characters],
                'coordinator': copy.deepcopy(config.get('coordinator', {}))}
        initial = complete('initializing', prompts.messages('coordinator', data), prompts.INITIAL_SCHEMA)
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
        calls['initializing']['output'] = {key: copy.deepcopy(conversation[key]) for key in ('situation', 'goal', 'non_participants', 'beginning_narration')}
        del conversation['pending_initialization']
        persist('Running')
        speaker = characters[0]
        for index in range(max_turns):
            data = character_input(conversation, speaker, characters, config['language'])
            generated = complete(f'character:{index}', prompts.messages('character', data), prompts.TURN_SCHEMA, speaker['name'])
            # Persist received output before validation or the next API call.
            conversation['pending_turn'] = {'name': speaker['name'], 'character': copy.deepcopy(generated)}
            persist('Running')
            validation.turn(generated)
            original = {'name': speaker['name'], **copy.deepcopy(generated)}
            data = {**scene_context(conversation),
                    'goal': conversation['goal'], 'non_participants': list(conversation['non_participants'])}
            data.update(language=config['language'], coordinator=copy.deepcopy(config.get('coordinator', {})), characters=characters,
                        history=copy.deepcopy(recent_turns(conversation)), character=original,
                        completed_turns=len(conversation['turns']),
                        current_situation=conversation['turns'][-1]['current_situation'] if conversation['turns'] else conversation['situation'])
            if 'compression' in conversation:
                data['private_memory'] = copy.deepcopy(conversation['compression']['private_thoughts'])
            eligible = [c['name'] for c in candidates(characters, speaker['name'])]
            # Recommendation evidence contains only publicly observable conversation.
            public_history = character_history(recent_turns(conversation), None)
            data['speaker_context'] = {
                'candidates': eligible,
                'preceding_messages': public_history[-7:],
                'completed_message': {k: original[k] for k in ('name', 'speech', 'narration', 'expression')},
            }
            schema = prompts.update_schema(eligible)
            revised = complete(f'coordinator:{index}', prompts.messages('update', data), schema)
            conversation['pending_turn']['coordinator'] = copy.deepcopy(revised)
            persist('Running')
            validation.update(revised)
            # Missing recommendation fields use neutral fallback values.
            revised = copy.deepcopy(revised)
            recommendation = revised.setdefault('speaker_recommendation', {})
            recommendation.setdefault('name', '')
            recommendation.setdefault('confidence', 0.0)
            calls[f'coordinator:{index}']['output'] = copy.deepcopy(revised)
            conversation['turns'].append({'character': original, **revised})
            del conversation['pending_turn']
            persist('Running')
            logging.info('Turn %d speaker=%s progress=%s', len(conversation['turns']), speaker['name'], revised['progress'])
            measured = [calls[f'{stage}:{index}']['metrics'].get('prompt_tokens')
                        for stage in ('character', 'coordinator')]
            measured = [value for value in measured if isinstance(value, int) and not isinstance(value, bool)]
            if measured and max(measured) > context_size - options['max_tokens']:
                if compress(conversation, characters, complete, index, max(measured)):
                    persist('Running')
            if config.get('goal') and revised['progress'] == 'Achieved':
                conversation['stop_reason'] = 'goal_achieved'
                break
            if len(conversation['turns']) < max_turns:
                speaker = select_speaker(characters, conversation['turns'], recommendation, recommendation_threshold)
        else:
            conversation['stop_reason'] = 'max_turns_reached'
        # Record the completed conversation and its stop reason before closing generation.
        persist('Running')
    except (Exception, KeyboardInterrupt) as error:
        conversation['stop_reason'] = 'user_interrupted' if isinstance(error, KeyboardInterrupt) else f'error: {type(error).__name__}: {error}'
        session['timestamps']['ended'] = now()
        persist('Failed')
        raise
    # Closing prose is optional enrichment; failure must not discard a completed conversation.
    data = {
        'language': config['language'],
        'coordinator': copy.deepcopy(config.get('coordinator', {})),
        'characters': [{k: c[k] for k in ('name', 'profile')} for c in characters],
        **scene_context(conversation),
        'non_participants': copy.deepcopy(conversation['non_participants']),
        'history': [{'name': t['character']['name'], 'speech': t['character']['speech'],
                     'expression': t['character']['expression'], 'narration': t['narration']}
                    for t in recent_turns(conversation)],
        'current_situation': conversation['turns'][-1]['current_situation'],
        'stop_reason': conversation['stop_reason'],
    }
    try:
        closing = complete('generating_ending', prompts.messages('ending', data), prompts.ENDING_SCHEMA)
        conversation['pending_ending'] = copy.deepcopy(closing)
        persist('Running')
        validation.ending(closing)
        conversation['ending_narration'] = closing['ending_narration']
        del conversation['pending_ending']
    except (Exception, KeyboardInterrupt) as error:
        conversation['ending_error'] = ('user_interrupted' if isinstance(error, KeyboardInterrupt)
                                        else f'{type(error).__name__}: {error}')
        calls['generating_ending']['error'] = conversation['ending_error']
        logging.warning('Closing narration not generated: %s', conversation['ending_error'])
        if isinstance(error, KeyboardInterrupt):
            raise
    finally:
        session['timestamps']['ended'] = now()
        persist('Ended')
    return path
