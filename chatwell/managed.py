"""Optional llama.cpp lifecycle. Only the supervisor's own child is stopped."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit
import uuid
from .local_config import read_values, local_values
from .locking import worker_lock, wait_lock
from .storage import read_json, save


_children = []


def reap():
    _children[:] = [process for process in _children if process.poll() is None]


def detached():
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
    return {'start_new_session': True}


def runtime(settings_path):
    return Path(settings_path).resolve().parent / '.runtime'


def health(endpoint, key=None):
    url = endpoint.rstrip('/')
    if url.endswith('/v1'):
        url = url[:-3]
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    from .client import NoRedirect
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(url + '/health', headers=headers), timeout=2) as response:
            return json.load(response).get('status') == 'ok'
    except (OSError, ValueError):
        return False


def launch(mode, settings_path, data_dir=None, mock=False):
    directory = runtime(settings_path) if mode == 'server' else Path(data_dir) / '.runtime'
    directory.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, '-m', 'chatwell.service', mode, '--settings', str(Path(settings_path).resolve())]
    if data_dir:
        args += ['--data-dir', str(Path(data_dir).resolve())]
    if mock:
        args += ['--mock']
    # Source checkouts work without installation, including from another working directory.
    environment = dict(os.environ)
    source = str(Path(__file__).resolve().parents[1])
    environment['PYTHONPATH'] = source + os.pathsep + environment.get('PYTHONPATH', '')
    with (directory / f'{mode}.log').open('ab') as log:
        reap()
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   cwd=Path(settings_path).resolve().parent, env=environment, **detached())
        _children.append(process)
        return process


def server_command(settings_path, settings):
    values = local_values(settings_path)
    base = Path(settings_path).resolve().parent
    executable = base / 'llama' / ('llama-server.exe' if os.name == 'nt' else 'llama-server')
    if not executable.is_file():
        raise ValueError(f'{executable.name} がllama/にありません。公式リリースを展開してください。')
    raw_model = values['LOCAL_GGUF_PATH']
    if not raw_model:
        raise ValueError('LOCAL_GGUF_PATH が空です。使用するGGUFを.envに設定してください。')
    model = Path(raw_model).expanduser()
    if not model.is_absolute():
        model = base / model
    model = model.resolve()
    with model.open('rb') as stream:
        if stream.read(4) != b'GGUF':
            raise ValueError('LOCAL_GGUF_PATH はGGUFではありません。')
    parsed = urlsplit(settings['endpoint'])
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.path not in ('', '/', '/v1', '/v1/'):
        raise ValueError('self-managed-llamaの接続先はループバックHTTPのベースURLが必要です。')
    context = int(values['LOCAL_LLAMA_CONTEXT'])
    if context < 1:
        raise ValueError('LOCAL_LLAMA_CONTEXT は正の整数が必要です。')
    layers = values['LOCAL_LLAMA_GPU_LAYERS']
    if layers != 'auto' and int(layers) < 0:
        raise ValueError('LOCAL_LLAMA_GPU_LAYERS はautoまたは0以上の整数が必要です。')
    command = [str(executable), '-m', str(model), '--host', parsed.hostname,
               '--port', str(parsed.port or 80), '-c', str(context), '-ngl', layers]
    if settings.get('model'):
        command += ['--alias', settings['model']]
    # llama.cpp reads LLAMA_API_KEY from its environment; no credentials in process arguments.
    return command, executable, model


def identity(executable, model, cache_path):
    stat = model.stat()
    signature = [str(model), stat.st_size, stat.st_mtime_ns]
    try:
        cached = read_json(cache_path)
    except (OSError, ValueError):
        cached = {}
    if cached.get('signature') != signature:
        digest = hashlib.sha256()
        with model.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        cached = {'signature': signature, 'sha256': digest.hexdigest()}
        save(cache_path, cached)
    try:
        version = subprocess.run([str(executable), '--version'], capture_output=True, text=True, timeout=30)
        version = (version.stdout or version.stderr).strip()[:1000] if version.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return {'management': 'self-managed', 'model_file': model.name, 'model_sha256': cached['sha256'],
            'llama_cpp_version': version, 'source': 'local_file_and_executable'}


def server_signature(settings_path, command, model):
    stat = Path(settings_path).stat()
    model_stat = model.stat()
    return {'command': command, 'settings_mtime_ns': stat.st_mtime_ns, 'settings_size': stat.st_size,
            'model_mtime_ns': model_stat.st_mtime_ns, 'model_size': model_stat.st_size}


def ensure_server(settings_path, settings):
    if not local_values(settings_path)['SELF_MANAGED_LLAMA']:
        return
    command, executable, model = server_command(settings_path, settings)
    directory = runtime(settings_path)
    directory.mkdir(parents=True, exist_ok=True)
    timeout = float(local_values(settings_path)['LOCAL_LLAMA_START_TIMEOUT'])
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('LOCAL_LLAMA_START_TIMEOUT は正の有限値が必要です。')
    deadline = time.monotonic() + timeout
    with wait_lock(directory / 'server-launch.lock', timeout):
        if health(settings['endpoint'], settings.get('api_key')):
            try:
                with worker_lock(directory / 'server.lock'):
                    managed = False
            except RuntimeError:
                managed = True
            if managed:
                state = read_json(directory / 'server.json')
                if state.get('signature') != server_signature(settings_path, command, model):
                    raise RuntimeError('起動中のself-managed-llamaと設定が異なります。停止してから起動してください。')
            return
        # A lifetime lock, not a stale status or PID, proves supervisor ownership.
        try:
            with worker_lock(directory / 'server.lock'):
                active = False
        except RuntimeError:
            active = True
        process = None if active else launch('server', settings_path)
        while time.monotonic() < deadline:
            if health(settings['endpoint'], settings.get('api_key')):
                return
            if process is not None and process.poll() is not None:
                raise RuntimeError('llama-serverの起動に失敗しました。.runtime/server.logを確認してください。')
            time.sleep(.5)
        raise RuntimeError('サーバー準備の待機上限に達しました。プロセスは停止していません。server.logを確認してください。')


def stop_server(settings_path):
    directory = runtime(settings_path)
    try:
        with worker_lock(directory / 'server.lock'):
            return False
    except RuntimeError:
        pass
    state = read_json(directory / 'server.json')
    save(directory / 'server-stop.json', {'instance': state['instance']})
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with worker_lock(directory / 'server.lock'):
                reap()
                return True
        except RuntimeError:
            time.sleep(.1)
    raise RuntimeError('停止を確認できません。server.logを確認してください。')


def supervise_server(settings_path, settings):
    directory = runtime(settings_path)
    with worker_lock(directory / 'server.lock'):
        command, executable, model = server_command(settings_path, settings)
        metadata = identity(executable, model, directory / 'model-cache.json')
        instance = uuid.uuid4().hex
        save(directory / 'server.json', {'instance': instance, 'provenance': metadata,
                                          'signature': server_signature(settings_path, command, model)})
        environment = dict(os.environ)
        if settings.get('api_key'):
            environment['LLAMA_API_KEY'] = settings['api_key']
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, env=environment)
        try:
            while process.poll() is None:
                try:
                    if read_json(directory / 'server-stop.json').get('instance') == instance:
                        return
                except (OSError, ValueError):
                    pass
                time.sleep(.2)
            if process.returncode:
                raise RuntimeError(f'llama-server exited: {process.returncode}')
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def attach_provenance(client, settings_path):
    client.provenance = {'management': 'external', 'llama_cpp_version': None,
                         'model_file': None, 'model_sha256': None, 'source': 'unavailable'}
    if local_values(settings_path)['SELF_MANAGED_LLAMA']:
        try:
            with worker_lock(runtime(settings_path) / 'server.lock'):
                return
        except RuntimeError:
            client.provenance = read_json(runtime(settings_path) / 'server.json')['provenance']
