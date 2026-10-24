# 本機のサーバー管理スクリプト検証

この文書は旧版の検証記録です。現行版の操作・検証結果は [README](../../README.md) と [v0.4.0](../releases/v0.4.0.md) を参照してください。

実施日: 2026-10-23（日本時間）。Linux ARM64、NVIDIA GB10、Python 3.12.3、既存 Docker Engine / NVIDIA CDI 環境で確認。

## 実装

- `scripts/setup.py`: Docker とモデルの存在・GGUF識別子を確認し、固定イメージを準備して `.runtime/server.json` を保存。
- `scripts/start.py`: 専用コンテナを作成し、ループバックHTTPのヘルス確認で準備完了まで待機。
- `scripts/status.py`: 未設定・停止・起動中/API未応答・準備完了・異常終了を表示。
- `scripts/stop.py`: 所有ラベルを確認したコンテナのみ停止・削除。設定とモデルは保持。
- `scripts/_runtime.py`: 共通実装。モデルの取得やコピー、システムのDocker/GPU環境のインストールは行わない。

会話実行用スクリプトは今回の対象外。

## 自動テスト

`python3 -m unittest discover -s tests -v`: **27件成功**（既存21件、新規6件）。

追加検証: 他用途コンテナの操作拒否、停止の繰り返し、管理対象のみの停止・削除、ポート競合時の起動拒否、二重起動防止、起動失敗時のログ表示。

`git diff --check`: 成功。

## 実機検証

1. 別用途の `melody1437-api-1` と `melody1437-llama-1` を停止。削除は行っていない。
2. `python3 scripts/status.py`: 未設定を表示。
3. `python3 scripts/setup.py`: 指定の既存GGUFを確認し、設定保存。
4. `python3 scripts/start.py`: `chatwell-llama` を起動し、モデル読み込みと `/health` の応答を確認。
5. `python3 scripts/status.py`: 準備完了、モデルパス、8080番ポート、コンテキスト16384を表示。
6. `python3 scripts/start.py`: 二重起動せず既存状態を表示。
7. Chatwell の `LocalClient` を使い、`response_format.type=json_schema`、必須文字列 `status`、enum `ok` の短い構造化出力を要求。実モデルから `{"status":"ok"}` を受信。初回推論には数十秒かかった。
8. `python3 scripts/stop.py`: 停止・コンテナ削除に成功。
9. `python3 scripts/stop.py`: 再実行でも成功。
10. `python3 scripts/status.py`: 停止中を表示。
11. `python3 scripts/setup.py`: 既存の固定イメージを取得せず再設定に成功。

モデル: `<USER_HOME>/melody1437/gguf/Melody1437-26B-A4B.i1-Q4_K_M.gguf`。読み取り専用で参照。

検証終了時はChatwell・旧Melodyサービスとも停止中。VAICサービスは継続稼働。会話全体の生成品質、他OSでの動作、Docker/GPU環境がないマシンへの自動導入は未検証・対象外。公開・pushは行っていない。

## .env への設定移行

同日、ユーザー設定を `.runtime/server.json` から `.env` に移行。旧ローカルJSONは削除し、実際のモデルパスを `.env` に引き継いだ。Python内の既定モデルパスは廃止した。

- `.env.example`: 必須モデル欄は空。設定例・既定値をGit管理。
- `.env`: 本機の設定を保存、Git対象外。
- `runtime/llama-image.txt`: 動作確認した固定推論イメージをGit管理。
- 優先順位: CLI引数 → 環境変数 → `.env` → `.env.example`。
- `setup.py` は既存 `.env` を上書きしない。初回のみ雛形を作り、対話端末ではモデルパス入力を案内する。
- 起動時の実設定をコンテナのラベルに保存し、起動中のstatusは編集後の設定ではなく実設定を参照する。stopは設定読み込みに依存しない。

自動テスト **31件成功**。追加4件は設定優先順位と相対パス、モデル未指定・不正数値、設定変更後の実設定表示、シェル構文を実行しないことを検証。

移行後の実機検証: `.env` を使った setup/start に成功。ヘルス確認で準備完了。起動後に環境変数 `CHATWELL_PORT=99999`（不正値）を指定しても、statusは起動時の実接続先8080を表示し、stopは正常に停止・削除した。最終状態は停止中。
