# 開発

Python 3.10以上を使い、リポジトリ直下で実行します。

```bash
python3 -m venv .venv
# OSに応じて仮想環境を有効化
python -m pip install -e .
python -m unittest discover -s tests -v
python -m chatwell run --input samples/input.json --mock --max-turns 3
```

新しい振る舞いには失敗も検出できるテストを追加してください。実モデルは通常のテストに必須とせず、HTTP境界はテストサーバーで検証します。OS固有の処理は境界を分け、Linux・macOS・WindowsのCIを確認してください。

## リリース

1. `chatwell/__init__.py` と `pyproject.toml` のバージョンを一致させる。
2. README、入出力仕様、リリースノートと移行手順を更新する。
3. CI、クリーンな仮想環境でのパッケージ導入、インストール後のCLI・モック実行を確認する。
4. 実モデル検証を行う場合はモデル名・SHA-256、llama.cppのバージョン、起動設定、実行コマンドを記録する。未実施の場合はその旨を明記する。
5. コミットをpushし、CI結果を確認してからバージョンタグを付ける。モデル・認証情報・実行中のログはコミットしない。公開用の出力例は内容を確認してから追加する。
