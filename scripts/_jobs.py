"""Disk-backed FIFO queue; a single Linux worker owns the processing lock."""
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chatwell import validation
from chatwell.speakers import DEFAULT_MAX_TURNS
from chatwell.storage import read_json, save

QUEUE = ROOT / '.runtime' / 'queue'
STATE = ROOT / '.runtime' / 'worker.json'
WORKER = ROOT / 'scripts' / '_worker.py'


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def enqueue(path, max_turns=None):
    data = read_json(path)
    validation.keys(data, ('config', 'characters'))
    validation.request(data['config'], data['characters'])
    if max_turns is None:
        max_turns = data['config'].get('max_turns', DEFAULT_MAX_TURNS)
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
        raise ValueError('max-turns は1以上の整数を指定してください。')
    job_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-') + uuid.uuid4().hex
    job = {'id': job_id, 'status': 'Queued', 'queued': timestamp(), 'started': None,
           'ended': None, 'input_path': str(Path(path).resolve()), 'input': data,
           'max_turns': max_turns, 'session_path': None, 'error': None}
    target = QUEUE / f'{job_id}.json'
    save(target, job)
    return target


def identity(pid):
    try:
        # Verify both process birth time and the exact worker script argument.
        proc = Path('/proc') / str(pid)
        if str(WORKER).encode() not in (proc / 'cmdline').read_bytes().split(b'\0'):
            return None
        return (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def worker_state():
    try:
        state = read_json(STATE)
        birth = identity(state['pid'])
        return state if birth is not None and birth == state['birth'] else None
    except (OSError, ValueError, KeyError):
        return None


def start_worker(config):
    if worker_state():
        return
    STATE.parent.mkdir(parents=True, exist_ok=True)
    settings = STATE.parent / 'worker-settings.json'
    save(settings, config)
    with (STATE.parent / 'worker.log').open('ab') as log:
        process = subprocess.Popen([sys.executable, str(WORKER), '--settings', str(settings)],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if worker_state():
            return
        if process.poll() is not None:
            raise RuntimeError('キュー処理担当を起動できませんでした。.runtime/worker.log を確認してください。')
        time.sleep(.1)
    raise RuntimeError('キュー処理担当の起動確認がタイムアウトしました。stop.py を実行してください。')


def stop_worker():
    state = worker_state()
    if not state:
        return
    # pidfd keeps this signal attached to the same process even if a PID is reused.
    try:
        fd = os.pidfd_open(state['pid'])
    except ProcessLookupError:
        return
    try:
        if identity(state['pid']) != state['birth']:
            return
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        deadline = time.monotonic() + 30
        while worker_state() and time.monotonic() < deadline:
            time.sleep(.1)
        if worker_state():
            raise RuntimeError('キュー処理担当が終了しませんでした。サーバー停止を中止します。')
    finally:
        os.close(fd)


def summary():
    counts = {}
    for path in sorted(QUEUE.glob('*.json')):
        job = read_json(path)
        counts[job['status']] = counts.get(job['status'], 0) + 1
    state = worker_state()
    return f"キュー処理: {'稼働中' if state else '停止中'} / 依頼: {counts or 'なし'}"


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
                        ROOT / 'sessions', observer=observe, max_turns=job['max_turns'])
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


def work(settings):
    from chatwell.client import LocalClient
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with (STATE.parent / 'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('キュー処理担当は既に起動しています。')
        QUEUE.mkdir(parents=True, exist_ok=True)
        # Ignore further termination signals while writing the final failure state.
        def terminate(signum, frame):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, terminate)
        client = LocalClient(f"http://127.0.0.1:{settings['port']}", timeout=settings['request_timeout'],
                             inference_options={'chat_template_kwargs': {'enable_thinking': settings['enable_thinking']},
                                                'reasoning_format': settings['reasoning_format'],
                                                'reasoning_in_content': settings['reasoning_in_content']},
                             default_max_tokens=settings['max_tokens'])
        try:
            recover()
            save(STATE, {'pid': os.getpid(), 'birth': identity(os.getpid())})
            while True:
                for path in sorted(QUEUE.glob('*.json')):
                    if read_json(path)['status'] == 'Queued':
                        process_job(path, client, settings)
                time.sleep(.5)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                recover()
            finally:
                STATE.unlink(missing_ok=True)
