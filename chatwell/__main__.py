import argparse
import json
import logging
import urllib.error

from . import __version__, jobs, validation
from .client import LlamaClient, MockClient
from .session import run
from .settings import load
from .storage import read_json


def parser():
    root = argparse.ArgumentParser(description='CHATWELL: llama.cpp conversation CLI')
    root.add_argument('--version', action='version', version=f'CHATWELL {__version__}')
    commands = root.add_subparsers(dest='command', required=True)
    for name in ('run', 'doctor', 'enqueue', 'worker', 'status'):
        p = commands.add_parser(name)
        if name in ('run', 'doctor', 'worker'):
            p.add_argument('--settings', help='Optional KEY=VALUE settings file; not shell code')
            p.add_argument('--endpoint', help='llama.cpp HTTP(S) base URL, optionally ending in /v1')
            p.add_argument('--timeout', type=float)
            p.add_argument('--model', help='Server-side model ID/alias, not a GGUF file path')
            p.add_argument('--max-tokens', type=int)
            p.add_argument('--mock', action='store_true', help='Fixed fixtures; no model inference')
        if name in ('run', 'enqueue'):
            p.add_argument('--input', required=True, help='Integrated config/characters JSON')
            p.add_argument('--max-turns', type=int)
        if name == 'run':
            p.add_argument('--output-dir', default='sessions')
            p.add_argument('--debug-dir')
        if name in ('enqueue', 'worker', 'status'):
            p.add_argument('--data-dir', default='.', help='Shared queue, sessions and debug root')
        if name == 'worker':
            p.add_argument('--once', action='store_true', help='Process queued requests, then exit')
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    try:
        if args.command in ('enqueue', 'worker', 'status'):
            jobs.configure(args.data_dir)
        if args.command == 'enqueue':
            print(jobs.enqueue(args.input, args.max_turns))
            return 0
        if args.command == 'status':
            print(json.dumps(jobs.summary(), ensure_ascii=False))
            return 0
        settings = load(args)
        client = MockClient() if args.mock else LlamaClient(**settings, debug_dir=getattr(args, 'debug_dir', None))
        if args.mock:
            logging.warning('MOCK: fixed fixtures, not model output')
        if args.command == 'doctor':
            schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                      'required': ['ok'], 'additionalProperties': False}
            if args.mock:
                print('MOCK: no server connection was checked')
            else:
                result = client.complete([{'role': 'user', 'content': 'Return {"ok":true} as JSON.'}], {}, schema)
                if result != {'ok': True}:
                    raise ValueError('構造化出力の確認に失敗しました。llama.cppとモデルの対応を確認してください。')
                print('接続・認証・モデル応答・構造化JSON: OK')
        elif args.command == 'run':
            data = read_json(args.input)
            validation.keys(data, ('config', 'characters'))
            print(run(data['config'], data['characters'], client, args.output_dir, max_turns=args.max_turns))
        elif args.command == 'worker':
            # Authentication stays in memory; never persist API keys in queue metadata.
            public_settings = {key: value for key, value in settings.items() if key != 'api_key'}
            jobs.work(client, public_settings, args.once)
        return 0
    except urllib.error.HTTPError as error:
        logging.error('HTTP %s: 接続先・認証・モデル・llama.cppのAPI対応を確認してください。', error.code)
    except urllib.error.URLError:
        logging.error('接続できません。サーバーの起動、接続先、ネットワーク設定を確認してください。')
    except KeyboardInterrupt:
        logging.error('処理を中断しました。保存済みの結果は保持されます。')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        logging.error('%s: %s', type(error).__name__, error)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
