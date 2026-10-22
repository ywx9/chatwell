"""Register a conversation request and exit without waiting for inference."""
import argparse
import sys
from _jobs import enqueue, worker_state


def main():
    parser = argparse.ArgumentParser(description='会話の生成依頼をキューに登録します。')
    parser.add_argument('--input', required=True, help='config と characters を含むJSONファイル')
    parser.add_argument('--max-turns', type=int, help='入力config.max_turnsを一時的に上書き')
    args = parser.parse_args()
    try:
        path = enqueue(args.input, args.max_turns)
        print(f'登録しました: {path.stem}\n依頼と結果の参照先: {path}')
        if not worker_state():
            print('処理担当は停止中です。python scripts/start.py で処理を開始します。')
        return 0
    except (OSError, ValueError) as error:
        print(f'エラー: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
