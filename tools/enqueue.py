#!/usr/bin/env python3
"""Submit a JSON file to the running Chatwell API."""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chatwell.settings import read_settings
from chatwell.storage import read_json


def main(argv=None):
    parser = argparse.ArgumentParser(description='JSONファイルを起動中のChatwell APIへ登録')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--settings', type=Path, default=ROOT / '.env')
    args = parser.parse_args(argv)
    try:
        settings = read_settings(args.settings)
        host = settings['CHATWELL_HOST']
        if host == '0.0.0.0':
            host = '127.0.0.1'
        port = int(settings['CHATWELL_PORT'])
        if not 1 <= port <= 65535:
            raise ValueError('CHATWELL_PORTは1〜65535で指定してください。')
        body = json.dumps(read_json(args.input), ensure_ascii=False, allow_nan=False).encode('utf-8')
        request = urllib.request.Request(
            f'http://{host}:{port}/v1/sessions', body,
            {'Content-Type': 'application/json'})
        # Local/trusted-network requests go directly to the configured API.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            result = json.load(response)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except urllib.error.HTTPError as error:
        print(f'API登録失敗: HTTP {error.code}', file=sys.stderr)
    except urllib.error.URLError:
        print('APIに接続できません。起動状態と.envのCHATWELL_HOST/CHATWELL_PORTを確認してください。', file=sys.stderr)
    except (OSError, ValueError) as error:
        print(f'登録失敗: {error}', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
