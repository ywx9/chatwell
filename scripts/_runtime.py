"""Local Docker lifecycle shared by the beginner-facing commands."""
import argparse
import json
import os
import shutil
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _jobs import start_worker, stop_worker, summary

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.env'
EXAMPLE = ROOT / '.env.example'
NAME = 'chatwell-llama'
LABEL = 'io.chatwell.root'
IMAGE = (ROOT / 'runtime' / 'llama-image.txt').read_text(encoding='utf-8').strip()
FIELDS = {'model': 'CHATWELL_MODEL', 'port': 'CHATWELL_PORT',
          'context_size': 'CHATWELL_CONTEXT_SIZE', 'gpu_layers': 'CHATWELL_GPU_LAYERS',
          'timeout': 'CHATWELL_START_TIMEOUT',
          'enable_thinking': 'CHATWELL_ENABLE_THINKING', 'reasoning_format': 'CHATWELL_REASONING_FORMAT',
          'reasoning_in_content': 'CHATWELL_REASONING_IN_CONTENT', 'max_tokens': 'CHATWELL_MAX_TOKENS',
          'request_timeout': 'CHATWELL_REQUEST_TIMEOUT', 'reasoning_budget': 'CHATWELL_REASONING_BUDGET'}


def read_env(path):
    """Read literal KEY=VALUE lines; never evaluate shell code or interpolate."""
    values = {}
    if not path.exists():
        return values
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not sep or key not in FIELDS.values() or key in values:
            raise ValueError(f'{path.name}:{number}: 設定名または書式が不正です。')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'{path.name}:{number}: 引用符が閉じていません。')
            value = value[1:-1]
        values[key] = value
    return values



def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def load(args=None):
    values = read_env(EXAMPLE)
    values.update(read_env(CONFIG))
    values.update({key: os.environ[key] for key in FIELDS.values() if key in os.environ})
    for field, key in FIELDS.items():
        value = getattr(args, field, None)
        if value is not None:
            values[key] = str(value)
    model = values.get('CHATWELL_MODEL', '').strip()
    if not model:
        raise ValueError('.env の CHATWELL_MODEL にGGUFのパスを設定してください。')
    path = Path(model).expanduser()
    config = {'model': str((ROOT / path).resolve()), 'image': IMAGE}
    for field in ('port', 'context_size', 'gpu_layers', 'timeout', 'max_tokens', 'request_timeout', 'reasoning_budget'):
        try:
            config[field] = int(values[FIELDS[field]])
        except (ValueError, KeyError) as error:
            raise ValueError(f'{FIELDS[field]} には整数を指定してください。') from error
    if not 1 <= config['port'] <= 65535 or config['context_size'] < 512 or config['gpu_layers'] < 0 or config['timeout'] <= 0:
        raise ValueError('port は1〜65535、context-size は512以上、gpu-layers は0以上、timeout は正数が必要です。')
    for field in ('enable_thinking', 'reasoning_in_content'):
        value = values.get(FIELDS[field], '').lower()
        if value not in ('true', 'false'):
            raise ValueError(f'{FIELDS[field]} は true / false を指定してください。')
        config[field] = value == 'true'
    config['reasoning_format'] = values.get('CHATWELL_REASONING_FORMAT')
    if config['reasoning_format'] not in ('auto', 'none', 'deepseek', 'deepseek-legacy'):
        raise ValueError('CHATWELL_REASONING_FORMAT が不正です。')
    if config['max_tokens'] <= 0 or config['request_timeout'] <= 0:
        raise ValueError('max_tokens / request_timeout は正数を指定してください。')
    if config['reasoning_budget'] < -1:
        raise ValueError('CHATWELL_REASONING_BUDGET は -1（無制限）または0以上の整数を指定してください。')
    if config['enable_thinking'] and (config['reasoning_in_content'] or config['reasoning_format'] in ('none', 'deepseek-legacy')):
        raise ValueError('構造化JSONのため、thinking有効時は reasoning_in_content=false、reasoning_format=auto または deepseek を指定してください。')
    return config


def inspect():
    names = docker('container', 'ls', '-a', '--format', '{{.Names}}').splitlines()
    if NAME not in names:
        return None
    info = json.loads(docker('inspect', NAME))[0]
    if info['Config'].get('Labels', {}).get(LABEL) != str(ROOT):
        raise RuntimeError(f'{NAME} はこの Chatwell の管理対象ではありません。操作を中止します。')
    return info


def endpoint(config):
    return f"http://127.0.0.1:{config['port']}"


def health(config):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(endpoint(config) + '/health', timeout=2) as response:
            return json.load(response).get('status') == 'ok'
    except (OSError, ValueError):
        return False


def setup(args):
    if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('この初期設定は本機の Linux ARM64 / NVIDIA GPU 環境向けです。')
    if not CONFIG.exists():
        shutil.copyfile(EXAMPLE, CONFIG)
        print(f'設定ファイルを作成しました: {CONFIG}')
    if not any((getattr(args, 'model', None), os.environ.get('CHATWELL_MODEL'), read_env(CONFIG).get('CHATWELL_MODEL'))):
        if sys.stdin.isatty():
            model_input = input('使用するGGUFのパスを入力してください: ').strip()
            if model_input:
                text = CONFIG.read_text(encoding='utf-8')
                CONFIG.write_text(text.replace('CHATWELL_MODEL=', 'CHATWELL_MODEL=' + model_input, 1), encoding='utf-8')
    config = load(args)
    model = Path(config['model'])
    if not model.is_file():
        raise ValueError(f'モデルが見つかりません: {model}')
    with model.open('rb') as stream:
        if stream.read(4) != b'GGUF':
            raise ValueError('指定ファイルは GGUF ではありません。')
    docker('info', '--format', '{{.ServerVersion}}')
    # Preserve an existing installation; only pull when the pinned image is absent.
    image_check = subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True, text=True)
    if image_check.returncode:
        print('推論用の固定 Docker イメージを取得しています。', flush=True)
        docker('pull', IMAGE)
    if inspect() is not None:
        raise RuntimeError('既存の Chatwell コンテナがあります。設定変更前に python scripts/stop.py を実行してください。')
    print(f'初期設定を確認しました: {CONFIG}\nモデル: {model}\n接続先: {endpoint(config)}\n次: python scripts/start.py')


def start(args):
    config = load(args)
    if not Path(config['model']).is_file():
        raise RuntimeError('モデルが見つかりません。setup.py で設定を確認してください。')
    info = inspect()
    if info is not None:
        if info['State']['Running']:
            print('Chatwell サーバーは既に起動しています。')
            actual = json.loads(info['Config']['Labels']['io.chatwell.settings'])
            if not health(actual):
                raise RuntimeError('既存サーバーが準備完了ではありません。status.py を確認してください。')
            start_worker(actual)
            status(args)
            return
        raise RuntimeError('停止済みコンテナがあります。python scripts/stop.py で片付けてから再実行してください。')
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', config['port']))
        except OSError as error:
            raise RuntimeError(f"ポート {config['port']} は使用中です。setup.py --port で別のポートを設定してください。") from error
    docker('run', '-d', '--name', NAME, '--label', f'{LABEL}={ROOT}',
           '--label', 'io.chatwell.settings=' + json.dumps(config),
           '--device', 'nvidia.com/gpu=all', '--ipc', 'host',
           '-p', f"127.0.0.1:{config['port']}:8080",
           '--mount', f"type=bind,source={config['model']},target=/models/model.gguf,readonly",
           config['image'], '--model', '/models/model.gguf', '--alias', 'chatwell',
           '--host', '0.0.0.0', '--port', '8080', '--n-gpu-layers', str(config['gpu_layers']),
           '--ctx-size', str(config['context_size']), '--parallel', '1', '--jinja', '--reasoning', 'on' if config['enable_thinking'] else 'off',
           '--reasoning-format', config['reasoning_format'],
           '--reasoning-budget', str(config['reasoning_budget']),
           '--chat-template-kwargs', json.dumps({'enable_thinking': config['enable_thinking']}))
    print('モデルを読み込んでいます。', flush=True)
    try:
        deadline = time.monotonic() + config['timeout']
        while time.monotonic() < deadline:
            info = inspect()
            if not info or not info['State']['Running']:
                raise RuntimeError('サーバーが終了しました。')
            if health(config):
                start_worker(config)
                print(f'起動完了: {endpoint(config)}\n状態確認: python scripts/status.py\n終了: python scripts/stop.py')
                return
            time.sleep(1)
        raise RuntimeError('起動待機がタイムアウトしました。')
    except (Exception, KeyboardInterrupt):
        subprocess.run(['docker', 'logs', '--tail', '30', NAME], check=False)
        print('起動処理を中断しました。後片付け: python scripts/stop.py', file=sys.stderr)
        raise


def stop(args):
    stop_worker()
    info = inspect()
    if info is None:
        print('Chatwell サーバーは停止済みです。')
        return
    if info['State']['Running']:
        docker('stop', '--time', '30', NAME)
    docker('rm', NAME)
    print('Chatwell サーバーを停止し、コンテナを削除しました。モデルと設定は保持しています。')


def status(args):
    print(summary())
    info = inspect()
    if info:
        raw = info['Config'].get('Labels', {}).get('io.chatwell.settings')
        if not raw:
            raise RuntimeError('旧形式のコンテナです。stop.py の後に start.py を実行してください。')
        config = json.loads(raw)
        source = '起動時の実設定'
    else:
        if not CONFIG.exists() and not os.environ.get('CHATWELL_MODEL'):
            print('未設定です。python scripts/setup.py を実行してください。')
            return
        config = load(args)
        source = str(CONFIG)
    state = '停止中'
    if info and info['State']['Running']:
        state = '準備完了' if health(config) else '起動中、または異常（API 未応答）'
    elif info:
        state = f"終了済み（終了コード {info['State']['ExitCode']}）"
    print(f"状態: {state}\nモデル: {config['model']}\n接続先: {endpoint(config)}\nコンテキスト: {config['context_size']}\n思考予算: {config.get('reasoning_budget', '未記録')} / 生成上限: {config.get('max_tokens', '未記録')}\n設定元: {source}")


def main(command):
    parser = argparse.ArgumentParser(description={'setup': 'Chatwell の初期設定', 'start': 'Chatwell 推論サーバーの起動', 'stop': 'Chatwell 推論サーバーの停止', 'status': 'Chatwell の状態確認'}[command])
    if command in ('setup', 'start', 'status'):
        parser.add_argument('--model')
        parser.add_argument('--port', type=int)
        parser.add_argument('--context-size', type=int)
        parser.add_argument('--gpu-layers', type=int)
        parser.add_argument('--timeout', type=int)
    args = parser.parse_args()
    try:
        globals()[command](args)
        return 0
    except KeyboardInterrupt:
        print('中断しました。', file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f'エラー: {error}', file=sys.stderr)
        return 1
