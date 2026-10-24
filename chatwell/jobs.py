"""Persistent FIFO queue for the API and sequential generation loop."""
import time
from contextlib import nullcontext
import uuid
from pathlib import Path
from datetime import datetime, timezone
from . import validation
from .speakers import DEFAULT_MAX_TURNS
from .storage import read_json, save
from .records import new_session

ROOT = Path.cwd()
QUEUE = ROOT / '.runtime' / 'queue'


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def enqueue(path, max_turns=None):
    return enqueue_data(read_json(path), max_turns, input_path=str(Path(path).resolve()))


def enqueue_data(data, max_turns=None, *, directory=None, input_path=None):
    root = Path(directory).resolve() if directory is not None else ROOT
    queue = root / '.runtime' / 'queue' if directory is not None else QUEUE
    validation.keys(data, ('config', 'characters'))
    validation.request(data['config'], data['characters'])
    if max_turns is None:
        max_turns = data['config'].get('max_turns', DEFAULT_MAX_TURNS)
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
        raise ValueError('max-turns は1以上の整数を指定してください。')
    job_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-') + uuid.uuid4().hex
    job = {'id': job_id, 'status': 'Queued', 'queued': timestamp(), 'started': None,
           'ended': None, 'input_path': input_path, 'input': data,
           'max_turns': max_turns, 'session_path': None, 'error': None}
    job['session_path'] = str(new_session(data['config'], data['characters'], root / 'sessions', max_turns).resolve())
    target = queue / f'{job_id}.json'
    try:
        save(target, job)
    except Exception:
        Path(job['session_path']).unlink(missing_ok=True)
        raise
    return target



def process_job(path, client, settings, runner=None):
    from chatwell.session import run
    runner = runner or run
    job = read_json(path)
    job.update(status='Running', started=timestamp(), runtime=settings)
    save(path, job)
    if hasattr(client, 'debug_dir'):
        client.debug_dir = ROOT / 'debug' / job['id']
        client.count = 0

    def observe(session_path):
        job['session_path'] = str(session_path.resolve())
        save(path, job)

    try:
        result = runner(job['input']['config'], job['input']['characters'], client,
                        ROOT / 'sessions', observer=observe, max_turns=job['max_turns'], session_path=job.get('session_path'))
        job.update(status='Ended', session_path=str(result.resolve()))
    except (Exception, KeyboardInterrupt) as error:
        job.update(status='Failed', error='処理を中断しました。' if isinstance(error, KeyboardInterrupt) else f'{type(error).__name__}: {error}')
        if isinstance(error, KeyboardInterrupt):
            # A stop during closing prose leaves the conversation itself complete.
            if job.get('session_path') and read_json(job['session_path'])['status'] == 'Ended':
                job.update(status='Ended', error=None)
            raise
    finally:
        job['ended'] = timestamp()
        if job.get('session_path'):
            session = read_json(job['session_path'])
            if session['status'] not in ('Ended', 'Failed'):
                session['status'] = 'Failed'
                session['timestamps'].update(ended=job['ended'], updated=job['ended'])
                session['progress'] = {'phase': 'failed', 'completed_turns': len(session['conversation']['turns'])}
                session['conversation']['stop_reason'] = job.get('error') or 'worker_interrupted'
                save(job['session_path'], session)
        save(path, job)


def recover():
    # Never silently repeat a partially generated conversation after a crash.
    for path in QUEUE.glob('*.json'):
        job = read_json(path)
        if job['status'] == 'Running':
            job.update(status='Failed', ended=timestamp(), error='前回の処理が中断されました。再実行は新しく登録してください。')
            if job.get('session_path'):
                session_path = Path(job['session_path'])
                if session_path.exists():
                    session = read_json(session_path)
                    if session['status'] not in ('Ended', 'Failed'):
                        conversation = session['conversation']
                        body_complete = (conversation.get('stop_reason') in ('goal_achieved', 'max_turns_reached')
                                         and bool(conversation.get('turns')) and 'pending_turn' not in conversation)
                        session['status'] = 'Ended' if body_complete else 'Failed'
                        session['timestamps']['ended'] = job['ended']
                        if 'progress' in session:
                            session['timestamps']['updated'] = job['ended']
                            session['progress']['phase'] = session['status'].lower()
                        if body_complete:
                            conversation['ending_error'] = 'worker_interrupted'
                            job.update(status='Ended', error=None)
                        else:
                            conversation['stop_reason'] = 'worker_interrupted'
                        save(session_path, session)
                    elif session['status'] == 'Ended':
                        # The session may have committed just before the worker crashed.
                        job.update(status='Ended', error=None)
            save(path, job)



def configure(directory):
    global ROOT, QUEUE
    ROOT = Path(directory).resolve()
    QUEUE = ROOT / '.runtime' / 'queue'


def summary():
    counts = {}
    for path in sorted(QUEUE.glob('*.json')):
        status = read_json(path)['status']
        counts[status] = counts.get(status, 0) + 1
    return counts


def work(client, settings, once=False, ready=None, should_stop=None, *, acquire_lock=True):
    from .locking import worker_lock
    with worker_lock(ROOT / '.runtime' / 'worker.lock') if acquire_lock else nullcontext():
        try:
            recover()
            if ready:
                ready()
            while True:
                for path in sorted(QUEUE.glob('*.json')):
                    if should_stop and should_stop():
                        return
                    if read_json(path)['status'] == 'Queued':
                        process_job(path, client, settings)
                if once or (should_stop and should_stop()):
                    return
                time.sleep(.5)
        except KeyboardInterrupt:
            pass
        finally:
            recover()
