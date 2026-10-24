"""Versioned session records; credentials and host paths are never provenance."""
import copy
import uuid
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from . import __version__
from .storage import save

SCHEMA_VERSION = '1.0'


def model_id(value):
    if not isinstance(value, str):
        return None
    if Path(value).is_absolute():
        return Path(value).name
    if PureWindowsPath(value).is_absolute():
        return PureWindowsPath(value).name
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def new_session(config, characters, output_dir, max_turns):
    stamp = datetime.now(timezone.utc)
    identity = uuid.uuid4().hex
    path = Path(output_dir) / (stamp.strftime('%Y%m%d-%H%M%S-') + f'{stamp.microsecond // 1000:03d}Z-{identity[:12]}.json')
    session = {
        'schema_version': SCHEMA_VERSION, 'chatwell_version': __version__,
        'id': identity, 'status': 'Queued',
        'timestamps': {'queued': now(), 'started': None, 'updated': now(), 'ended': None},
        'progress': {'phase': 'queued', 'completed_turns': 0},
        'config': copy.deepcopy(config), 'characters': copy.deepcopy(characters),
        'runtime': {'max_turns': max_turns},
        'conversation': {'initialization': None, 'turns': [], 'ending': None, 'stop_reason': ''},
    }
    save(path, session)
    return path


def encode(session, calls):
    """Project the private conversation state into the public storage schema."""
    result = copy.deepcopy(session)
    source = session['conversation']
    target = {'initialization': copy.deepcopy(calls.get('initializing')),
              'turns': [], 'ending': copy.deepcopy(calls.get('generating_ending')),
              'stop_reason': source['stop_reason']}
    for index, turn in enumerate(source['turns']):
        target['turns'].append({'index': index,
            'character': copy.deepcopy(calls[f'character:{index}']),
            'coordinator': copy.deepcopy(calls[f'coordinator:{index}'])})
    for key in ('pending_turn', 'pending_initialization', 'pending_ending', 'ending_error'):
        if key in source:
            target[key] = copy.deepcopy(source[key])
    if 'compression' in source:
        target['compression'] = copy.deepcopy(source['compression'])
    compressions = [copy.deepcopy(call) for kind, call in calls.items() if kind.startswith('compressing_history:')]
    if compressions:
        target['compressions'] = compressions
    index = len(source['turns'])
    if f'character:{index}' in calls:
        target['pending_turn'] = {'index': index, 'character': copy.deepcopy(calls[f'character:{index}'])}
        if f'coordinator:{index}' in calls:
            target['pending_turn']['coordinator'] = copy.deepcopy(calls[f'coordinator:{index}'])
    result['conversation'] = target
    return result
