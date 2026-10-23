# CHATWELL

**v0.3.1** — 任意のllama.cppサーバーを使い、複数キャラクターの会話を生成・保存するPython CLIです。推奨モデルは **Gemma 4系の対応GGUF**。Python 3.10以上が必要で、実行時の追加Pythonパッケージはありません。

既に使えるllama.cppサーバーがある場合は接続先を設定するだけです。未導入の場合は、任意の **self-managed-llama** 起動補助を使えます。Chatwellの中核はOS・CPU・GPU・Dockerに依存しません。実際に推論できる性能やメモリは、モデルと環境に依存します。

以下はリポジトリ直下で実行します。Windowsで `python3` が使えない場合は `python` に置き換えてください。

## 既存のllama.cppサーバーを使う

`.env.example` を参考に `.env` を作成します。既存ファイルを上書きしないでください。

```dotenv
SELF_MANAGED_LLAMA=false
CHATWELL_ENDPOINT=http://127.0.0.1:8080
```

サーバーを起動済み、または自分で起動できる場合はこの方式を使ってください。別ホストのHTTP(S)サーバーにも接続できます。認証が必要なら `CHATWELL_API_KEY` を環境変数に設定します。リモート接続はHTTPSを推奨します。

```bash
python3 -m chatwell doctor --settings .env
python3 tools/enqueue.py samples/input.json
```

接続先はベースURLで、末尾の `/v1` は省略可能です。必要なAPIは `/v1/chat/completions` と `response_format.type=json_schema`。`doctor` は実際に短い生成を要求して構造化JSONを確認します。Gemma 4系でも、llama.cppのバージョンとモデルに応じた対応が必要です。

## self-managed-llamaを用意する

**self-managed-llama** は、`llama/` に置いたllama.cppをChatwellの補助スクリプトが起動・停止する方式です。

1. [llama.cppの公式リリース](https://github.com/ggml-org/llama.cpp/releases)から、OS・アーキテクチャ・利用するバックエンドに合う配布物をダウンロードします。ZIPなどを展開し、必要なライブラリも含めて `llama/` に配置してください。適切な配布物がない場合は公式のビルド手順を使えます。
2. `llama/llama-server`、Windowsでは `llama/llama-server.exe` が存在する状態にします。Linux/macOSでは実行権限も必要です。
3. Gemma 4系など、使用するllama.cppが対応するGGUFを `llm/` に配置します。
4. 次の確認コマンドを実行します。

```bash
python3 tools/check-self-managed-llama.py
```

`.env` がなければ生成し、`SELF_MANAGED_LLAMA=true` を設定します。既存の設定値は尊重します。未設定のモデルについて `llm/*.gguf` を探索し、1件なら `LOCAL_GGUF_PATH` に記録、0件・複数件なら空欄にして案内します。複数の場合は候補を表示します。

```dotenv
SELF_MANAGED_LLAMA=true
LOCAL_GGUF_PATH=llm/gemma-4-e2b-it-q4_k_m.gguf
CHATWELL_ENDPOINT=http://127.0.0.1:8080
LOCAL_LLAMA_CONTEXT=16384
LOCAL_LLAMA_GPU_LAYERS=auto
LOCAL_LLAMA_START_TIMEOUT=3600
```

`LOCAL_GGUF_PATH` は `llm/` 以外のパスでも構いません。相対パスは `.env` のあるディレクトリ基準です。確認コマンドはモデルが設定されていれば、起動・ヘルスチェック・構造化JSON応答まで確認します。モデル未設定や確認失敗の場合は非0で終了します。

```bash
python3 tools/start-self-managed-llama.py
python3 tools/stop-self-managed-llama.py
```

開始時は指定GGUFを使います。管理プロセスが起動した子プロセスだけを停止し、他のサーバーやコンテナは停止しません。設定変更後はworkerと管理サーバーを停止してから再実行してください。既に応答している手動起動サーバーを利用した場合、そのサーバーを停止することはありません。

`llama/`・`llm/`・`.env` はGit管理しません。モデルのダウンロードや推論基盤の一括自動導入は行いません。

## 会話を登録する

```bash
python3 tools/enqueue.py samples/input.json
# 短く試す場合
python3 tools/enqueue.py samples/input.json --max-turns 3
```

`.env` がない場合はエラーになります。環境変数で値を上書きできます。`SELF_MANAGED_LLAMA=true` でサーバー未起動なら起動を待ち、workerが動いていなければバックグラウンドで起動します。

登録時点で `Queued` のセッションJSONを作成し、そのパスを表示します。同じパスで完了まで追跡できます。ファイル名は **`yyyymmdd-hhmmss-ミリ秒Z-ランダムID.json`**。日時はUTCです。同じ入力の再登録も別セッションになります。

```bash
python3 tools/status.py sessions/20261023-042030-864Z-012345abcdef.json
```

status、処理段階、完了ターン数、最終更新時刻、締めのエラーを表示します。`Running` や最終更新時刻だけで処理が停止したとは断定しません。依頼は順番に処理し、同じデータディレクトリのworkerはロックで二重起動を防ぎます。ローカルファイルシステムで使用してください。

```bash
python3 tools/stop-worker.py
```

現在の依頼が終わった後にworkerを停止する依頼を送ります。停止完了を即時に保証するコマンドではありません。サーバーを止める場合はworkerの処理完了を確認してから停止してください。設定変更後に古いworkerが残っている場合、新しい依頼の登録はエラーにし、意図しない設定で処理しないようにします。環境変数だけを変更した場合もworkerの再起動が必要です。

保存先は既定で `.env` のあるディレクトリです。`--settings PATH` と `--data-dir DIR` で変更できます。すべての操作で同じdata-dirを使ってください。

| 保存先 | 内容 |
|---|---|
| `sessions/*.json` | 登録時の入力、状態、実効設定、会話、処理時間等 |
| `.runtime/queue/*.json` | 内部キュー。利用者の状態確認はセッションJSONを参照 |
| `.runtime/worker.log` | workerログ |
| `.runtime/server.log` | self-managed-llamaの起動・実行ログ |
| `debug/<依頼ID>/response-*.json` | workerの生API応答 |

強制終了で残った処理は次のworker起動時に復旧します。未完了の会話を自動で重複生成せず、失敗として保持します。締めだけの失敗は会話本体を `Ended` のまま残して `ending_error` に記録します。

## 実モデルで動作確認する

```bash
python3 tools/test.py
# 待機上限を変える場合（秒）
python3 tools/test.py --wait-timeout 7200
```

`samples/input.json --max-turns 3` 相当を登録し、10秒ごとにstatusと経過時間を表示します。目標達成で3ターン未満に終了しても正常です。正常終了・保存結果・終了理由・締めの成功を確認して「テスト成功」と表示します。

待機上限は既定 **1時間**。超過時は生成失敗と断定せず「完了を確認できなかった」として終了コード2を返します。生成処理は停止しません。Ctrl+Cも監視だけを終了します。APIリクエスト単位のタイムアウトは別設定で、既定3600秒です。

## 設定

優先順位は **CLI引数 → 環境変数 → 設定ファイル → 既定値**。`tools/` は既定でリポジトリの `.env` を使います。`python -m chatwell` の直接実行では環境変数のみでも使え、ファイルは `--settings` で指定します。

| 設定 | 既定値・用途 |
|---|---|
| `CHATWELL_ENDPOINT` | `http://127.0.0.1:8080` |
| `CHATWELL_MODEL` | 未指定。サーバー側モデルID／alias。GGUFパスではない |
| `CHATWELL_API_KEY` | 未指定。Bearer認証。環境変数での指定を推奨 |
| `CHATWELL_REQUEST_TIMEOUT` | 3600秒。1回のAPI応答待機 |
| `CHATWELL_MAX_TOKENS` | 6144。入力の `llm_config.max_tokens` があれば入力を優先 |
| `CHATWELL_ENABLE_THINKING` | `true` |
| `CHATWELL_REASONING_FORMAT` | `auto`。`none/deepseek/deepseek-legacy` も指定可能 |
| `CHATWELL_REASONING_IN_CONTENT` | `false`。構造化JSONのためtrueは拒否 |
| `SELF_MANAGED_LLAMA` | `false`。ローカル起動補助を使うか |
| `LOCAL_GGUF_PATH` | 空。使用GGUFのパス |
| `LOCAL_LLAMA_CONTEXT` | 16384 |
| `LOCAL_LLAMA_GPU_LAYERS` | `auto`。CPU実行などでは0も指定可能 |
| `LOCAL_LLAMA_START_TIMEOUT` | 3600秒。サーバー準備待ち。超過しても強制停止しない |

設定ファイルは `KEY=VALUE` 形式。外側の引用符は使えますが、シェル・変数展開や行末コメントは扱いません。未知のキーはエラーです。認証情報・内部ホスト名・モデルの絶対パスは共有用セッションの実行メタデータに含めません。生応答やログには会話内容や実行環境の情報が含まれます。

## 入出力

入力例は [samples/input.json](samples/input.json)。詳細は [入出力仕様](docs/input-output.md) に記載しています。

v0.3.1では `schema_version` とChatwellのバージョン、実効設定、取得可能なモデル識別情報、llama.cppのバージョンを保存します。各ターンは `index`（0始まり）、`character`、`coordinator` を持ち、それぞれに `output` と `metrics` を置きます。初期化・締めにも同じ形式を使います。

外部サーバーのモデルIDは生成応答から、バージョンは任意の `/props` が `build_info` を返す場合に取得します。取得できない情報は推測せず `null` にします。self-managed-llamaはGGUF名・SHA-256・実行ファイルのバージョンを記録し、ハッシュはファイルサイズと更新時刻が変わらない限り再利用します。同じ条件での完全な生成再現を保証するものではありません。

既存のセッションは書き換えません。今回添付されたv0.3.0の実行結果も旧形式として扱い、v0.3.1の出力例を捏造・自動変換しません。新形式の `samples/output.json` は実モデルでの実行後に追加する想定です。

## 開発

通常の利用者がモデルなしテストを実行する必要はありません。開発・CIでは入力検証、保存、復旧、認証、プロセス管理をモデルの応答に左右されず検証します。

```bash
python3 -m unittest discover -s tests -v
python3 -m chatwell run --input samples/input.json --mock --max-turns 3
```

ソースからそのまま実行できます。パッケージとして使う場合は仮想環境で `python -m pip install .`、開発時は `python -m pip install -e .` を使います。`chatwell run/doctor/enqueue/worker/status` も利用できます。`enqueue` は `.env` または `--settings` を必要とし、自動起動を行います。`worker` はフォアグラウンド実行、Ctrl+Cで停止します。

CIはLinux・macOS・WindowsのPython 3.10/3.13を対象にします。今回のクラウド環境ではLinux x86_64/Python 3.12で検証しています。実GGUFでの推論と配布された各OS用バイナリの動作は未検証です。起動補助のテストはHTTPサーバーの代用品を使用しています。

- [v0.3.1の変更・移行手順](docs/releases/v0.3.1.md)
- [開発・リリース手順](CONTRIBUTING.md)
- [v0.3.0](docs/releases/v0.3.0.md)以前の記録は過去のバージョンの結果です。

GUI、履歴要約・圧縮、途中再開、自動再試行、依頼キャンセルは未実装です。goal未指定時はターン上限まで生成します。長い会話ではコンテキスト不足が起こり得ます。
