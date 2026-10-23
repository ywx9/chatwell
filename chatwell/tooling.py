"""User-facing scripts share settings, startup and status logic."""
import argparse
import json
import logging
import math
import os
from pathlib import Path
import time
from . import jobs
from .client import LlamaClient
from .local_config import local_values, read_values
from .managed import ensure_server, stop_server, launch, runtime, reap
from .locking import worker_lock, wait_lock
from .settings import load
from .storage import read_json, save

ROOT = Path(__file__).resolve().parents[1]


def check(settings_path):
    path = Path(settings_path).resolve()
    base = path.parent
    values = read_values(path, required=False)
    additions = {}
    if 'SELF_MANAGED_LLAMA' not in values:
        additions['SELF_MANAGED_LLAMA'] = 'true'
    if not values.get('LOCAL_GGUF_PATH'):
        candidates = sorted((base / 'llm').glob('*.gguf'))
        additions['LOCAL_GGUF_PATH'] = candidates[0].relative_to(base).as_posix() if len(candidates) == 1 else ''
        if len(candidates) > 1:
            print('複数のGGUFがあります。LOCAL_GGUF_PATHを指定してください:')
            for candidate in candidates:
                print(candidate.relative_to(base))
    if not path.exists():
        path.write_text((ROOT / '.env.example').read_text(encoding='utf-8'), encoding='utf-8')
        # Populate fields in the new template without overwriting user files.
        text = path.read_text(encoding='utf-8')
        for key, value in additions.items():
            lines = text.splitlines()
            lines = [f'{key}={value}' if line.startswith(key + '=') else line for line in lines]
            text = '\n'.join(lines) + '\n'
        path.write_text(text, encoding='utf-8')
    else:
        # Preserve all existing lines and settings. Only fill an empty model field.
        text = path.read_text(encoding='utf-8')
        if 'LOCAL_GGUF_PATH' in values and not values['LOCAL_GGUF_PATH']:
            text = '\n'.join(f'LOCAL_GGUF_PATH={additions.get("LOCAL_GGUF_PATH", "")}'
                             if line.strip().startswith('LOCAL_GGUF_PATH=') else line for line in text.splitlines()) + '\n'
        for key, value in additions.items():
            if key not in values:
                text += f'\n{key}={value}\n'
        if path.read_text(encoding='utf-8') != text:
            path.write_text(text, encoding='utf-8')
    settings = load(argparse.Namespace(settings=str(path)))
    if not local_values(path)['LOCAL_GGUF_PATH']:
        print('LOCAL_GGUF_PATHは空です。GGUFを指定後に再実行してください。')
        return 1
    ensure_server(path, settings)
    print('self-managed-llama 起動・ヘルス確認: OK')
    # A functional structured-output check, not just a listening port.
    from .__main__ import main
    return main(['doctor', '--settings', str(path)])


def worker_signature(settings_path, mock):
    stat = Path(settings_path).stat()
    return {'settings': str(Path(settings_path).resolve()), 'mtime_ns': stat.st_mtime_ns,
            'size': stat.st_size, 'mock': mock,
            'effective': {key: value for key, value in load(argparse.Namespace(settings=str(settings_path))).items() if key != 'api_key'}}


def check_worker(settings_path, data_dir, mock):
    directory = Path(data_dir) / '.runtime'
    try:
        with worker_lock(directory / 'worker.lock'):
            return
    except RuntimeError:
        try:
            state = read_json(directory / 'worker.json')
        except (OSError, ValueError):
            raise RuntimeError('既存workerの設定を確認できません。workerを停止してから再実行してください。') from None
        if state.get('signature') != worker_signature(settings_path, mock):
            raise RuntimeError('既存workerの設定が異なります。tools/stop-worker.pyで停止後に再実行してください。')


def ensure_worker(settings_path, data_dir, mock=False):
    directory = Path(data_dir) / '.runtime'
    directory.mkdir(parents=True, exist_ok=True)
    with wait_lock(directory / 'worker-launch.lock'):
        try:
            with worker_lock(directory / 'worker.lock'):
                active = False
        except RuntimeError:
            active = True
        if active:
            return
        process = launch('worker', settings_path, data_dir, mock)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('worker起動失敗。.runtime/worker.logを確認してください。登録したセッションは保持されています。')
            try:
                with worker_lock(directory / 'worker.lock'):
                    pass
            except RuntimeError:
                try:
                    if read_json(directory / 'worker.json').get('pid') == process.pid:
                        return
                except (OSError, ValueError):
                    pass
            time.sleep(.1)
        raise RuntimeError('workerの起動確認が完了しません。セッションは保持されています。')


def enqueue(input_path, settings_path, data_dir, max_turns=None, mock=False):
    read_values(settings_path)  # Missing settings must not create an orphan request.
    settings = load(argparse.Namespace(settings=str(settings_path)))
    from . import validation
    data = read_json(input_path)
    validation.keys(data, ('config', 'characters'))
    validation.request(data['config'], data['characters'])
    if max_turns is not None and (isinstance(max_turns, bool) or max_turns < 1):
        raise ValueError('max-turnsは正の整数が必要です。')
    check_worker(settings_path, data_dir, mock)
    if not mock:
        ensure_server(settings_path, settings)
    with wait_lock(Path(data_dir) / '.runtime' / 'submission.lock'):
        check_worker(settings_path, data_dir, mock)
        jobs.configure(data_dir)
        job_path = jobs.enqueue(input_path, max_turns)
        session_path = Path(read_json(job_path)['session_path'])
        print(session_path, flush=True)
        ensure_worker(settings_path, data_dir, mock)
        return session_path



def status(path):
    record = read_json(path)
    if record.get('status') not in ('Queued', 'Starting', 'Running', 'Ended', 'Failed'):
        raise ValueError('不正なセッションstatusです。')
    if not isinstance(record.get('conversation', {}).get('turns'), list):
        raise ValueError('セッションにturnsがありません。')
    progress = record.get('progress', {})
    result = {'status': record['status'], 'phase': progress.get('phase'),
              'completed_turns': len(record['conversation']['turns']),
              'updated': record['timestamps'].get('updated'),
              'ending_error': record['conversation'].get('ending_error')}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return record


def monitor(path, timeout=3600, interval=10):
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('待機上限は正の有限値が必要です。')
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        print(f'elapsed={elapsed:.1f}s', end=' ', flush=True)
        record = status(path)
        if record['status'] == 'Failed':
            print('テスト失敗: セッションがFailedで終了しました。')
            return 1
        if record['status'] == 'Ended':
            c = record['conversation']
            ending = c.get('ending', {}).get('output') if isinstance(c.get('ending'), dict) else None
            narration = ending.get('ending_narration') if ending else c.get('ending_narration')
            if c.get('ending_error') or not c['turns'] or not narration or c.get('stop_reason') not in ('goal_achieved', 'max_turns_reached'):
                print('テスト失敗: 結果または終了処理に問題があります。')
                return 1
            limit = record.get('runtime', {}).get('max_turns', record['config'].get('max_turns', 12))
            if len(c['turns']) > limit or (c['stop_reason'] == 'max_turns_reached' and len(c['turns']) != limit):
                print('テスト失敗: ターン数が実効上限と一致しません。')
                return 1
            print('テスト成功: 正常終了と保存結果を確認しました。')
            return 0
        if elapsed >= timeout:
            print('待機上限に達したため、完了を確認できませんでした。生成処理は停止していません。')
            return 2
        time.sleep(min(interval, timeout - elapsed))


def stop_worker(data_dir):
    directory = Path(data_dir) / '.runtime'
    try:
        with worker_lock(directory / 'worker.lock'):
            print('workerは停止済みです。')
            return
    except RuntimeError:
        state = read_json(directory / 'worker.json')
        save(directory / 'worker-stop.json', state)
        print('worker停止を依頼しました。現在の依頼の処理後に停止します。')
    reap()


def main(command, argv=None):
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', default=str(ROOT / '.env'))
    parser.add_argument('--data-dir')
    if command in ('enqueue', 'status'):
        parser.add_argument('path')
    if command in ('enqueue', 'test'):
        parser.add_argument('--max-turns', type=int, default=3 if command == 'test' else None)
        parser.add_argument('--mock', action='store_true', help='Development-only fixed responses')
    if command == 'test':
        parser.add_argument('--input', default=str(ROOT / 'samples/input.json'))
        parser.add_argument('--wait-timeout', type=float, default=3600)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    path = Path(args.settings).resolve()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else path.parent
    try:
        if command == 'check':
            return check(path)
        if command == 'status':
            status(args.path)
        elif command == 'start':
            ensure_server(path, load(argparse.Namespace(settings=str(path))))
            print('self-managed-llama: ready（SELF_MANAGED_LLAMA=falseの場合は管理対象外）')
        elif command == 'stop':
            print('停止しました。' if stop_server(path) else '管理対象のサーバーは停止済みです。')
        elif command == 'stop-worker':
            stop_worker(data_dir)
        else:
            if command == 'test' and (not math.isfinite(args.wait_timeout) or args.wait_timeout <= 0):
                raise ValueError('--wait-timeoutは正の有限値が必要です。')
            result = enqueue(args.path if command == 'enqueue' else args.input,
                             path, data_dir, args.max_turns, args.mock)
            if command == 'test':
                return monitor(result, args.wait_timeout)
        return 0
    except KeyboardInterrupt:
        print('監視を中断しました。バックグラウンド処理は停止していません。')
        return 130
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        logging.error('%s: %s', type(error).__name__, error)
        return 1
