# CHATWELL

**v0.3.0** — 任意のllama.cppサーバーに接続し、複数キャラクターの会話を生成・保存するPython CLIです。

推奨モデルは **Gemma 4系の対応GGUF** です。ChatwellはOS・CPUアーキテクチャ・GPU・Dockerを前提にせず、llama.cppの導入、モデルの用意、推論サーバーの起動・停止はユーザーが管理します。モデルを実行できるメモリ・性能は、選んだモデルと推論環境に依存します。

## まずモックで試す

Python **3.10以上** が必要です。実行時の追加Pythonパッケージはありません。以下はリポジトリ直下で実行してください。Windowsで `python3` が使えない場合は `python` に置き換えてください。

```bash
python3 -m venv .venv
# Linux / macOS
. .venv/bin/activate
# Windows PowerShellでは上記の代わりに:
# .venv\Scripts\Activate.ps1
python -m pip install .
chatwell --version
chatwell run --input samples/input.json --mock --max-turns 3 --output-dir sessions/mock
```

最後に出力されたパスがセッションJSONです。モックは固定応答であり、モデルの生成品質や接続を検証するものではありません。インストールせず、リポジトリ直下から `python3 -m chatwell` でも同じコマンドを使えます。開発時のインストールは `python -m pip install -e .` を使ってください。

## llama.cppに接続する

1. [llama.cpp](https://github.com/ggml-org/llama.cpp) の公式手順に従い、使用環境向けの `llama-server` を用意します。
2. Gemma 4系に対応するバージョンとGGUFを用意し、サーバーを起動します。別のPCやリモートホストのサーバーも利用できます。
3. `doctor` で確認し、サンプルを実行します。

サーバー起動の最小例です。実行ファイル名・パスは環境に合わせてください。Windowsでは `llama-server.exe` などになります。

```bash
llama-server -m /path/to/gemma-4-e2b-it-q4_k_m.gguf --host 127.0.0.1 --port 8080 -c 16384
```

GPUオフロード、コンテキスト長、思考予算はllama.cpp側で設定します。上のコンテキスト長は開始用の例で、必要メモリや長い会話での収容を保証しません。使用するバージョンの `llama-server --help` を確認してください。リモート公開時はサーバー側の認証・アクセス制限も設定してください。

```bash
chatwell doctor --endpoint http://127.0.0.1:8080
chatwell run --input samples/input.json --endpoint http://127.0.0.1:8080 --max-turns 2
```

`doctor` は実際に短い生成を要求し、接続・認証・モデルの応答・構造化JSONを確認します。成功しても、会話品質や長い会話での成功を保証するものではありません。

必要なAPIは `/v1/chat/completions`、`response_format.type=json_schema` と本文のJSON応答です。接続先は `http://` または `https://` のベースURLで、末尾の `/v1` は省略できます。モデルを指定する場合の `--model` はサーバー側のモデルID／aliasです。GGUFファイルのパスではありません。

認証が必要な場合は `CHATWELL_API_KEY` を環境変数に設定します。Bearer認証で送信し、キューの実行設定には保存しません。リモート接続はHTTPSを推奨します。別ホストへの認証情報の転送を避けるため、HTTPリダイレクトは拒否します。

**検証状況:** v0.3.0はLinux x86_64 / Python 3.12で、モック・HTTPテストサーバーを使って検証しています。Windows・macOSを含むCIを用意していますが、ローカル検証済みとは区別します。Gemma 4の実モデル検証は今回のクラウド環境では未実施です。以前の手元での検証には `gemma-4-e2b-it-q4_k_m.gguf` が使われていますが、v0.3.0での再検証は別途行います。llama.cppやモデルの特定バージョンでの互換性を一律には保証しません。

## 共通設定

`run`・`doctor`・`worker` は同じ接続・生成設定を使います。

優先順位は **CLI引数 → 環境変数 → `--settings` で明示したファイル → 既定値** です。`.env` の自動読込はありません。[.env.example](.env.example) を参考に新しい設定ファイルを作成してください。既存ファイルを上書きしないでください。

```bash
chatwell doctor --settings .env
chatwell run --input samples/input.json --settings .env
```

| 環境変数 | 既定値・用途 |
|---|---|
| `CHATWELL_ENDPOINT` | `http://127.0.0.1:8080` |
| `CHATWELL_MODEL` | 未指定。サーバー側モデルID／alias |
| `CHATWELL_API_KEY` | 未指定。Bearer認証用。環境変数での指定を推奨 |
| `CHATWELL_REQUEST_TIMEOUT` | `600` 秒 |
| `CHATWELL_MAX_TOKENS` | `6144`。入力に `llm_config.max_tokens` がある場合は入力を優先 |
| `CHATWELL_ENABLE_THINKING` | `true` |
| `CHATWELL_REASONING_FORMAT` | `auto`。`none/deepseek/deepseek-legacy` も指定可能 |
| `CHATWELL_REASONING_IN_CONTENT` | `false`。構造化JSONのため `true` は拒否 |

CLIには `--endpoint`・`--model`・`--timeout`・`--max-tokens` があります。`--max-tokens` は入力側に指定がない場合の既定値を変えます。`reasoning_effort` は送信しません。思考設定の対応はllama.cppとモデルの組み合わせに依存します。

設定ファイルは `KEY=VALUE` 形式で、外側の引用符を使えます。シェル・変数展開や行末コメントは扱いません。コメントは独立した行に書きます。未知のキーはエラーにします。

## 入力と出力

公開する入力例は [samples/input.json](samples/input.json) です。直接実行とキュー実行の両方で同じ形式を使います。

- `config`: 言語、開始状況、任意の目標、最大ターン数、生成設定、Coordinatorの描写方針。
- `characters`: 1人以上の人物。各人物に `name/profile/persona/speech/relationships` を指定。
- `max_turns`: CLIの `--max-turns` → 入力値 → 既定12の順。目標達成によって先に終了する場合があります。
- `llm_config`: `temperature/max_tokens/top_p/top_k/min_p/seed/repeat_penalty` に対応。未対応項目は拒否します。

詳細は [入出力仕様](docs/input-output.md) を参照してください。実行後に表示するJSONパスが保存結果です。会話の各ターン、開始・終了の地の文、終了理由などを含みます。`samples/output.json` はv0.3.0を手元で実行した結果から追加する予定で、今回のリリースには含めません。

## キューを使う

依頼を登録し、workerを別端末または同じ端末で実行します。同じキューを使うコマンドには同じ `--data-dir` を指定してください。既定は現在のディレクトリです。

```bash
chatwell enqueue --input samples/input.json --max-turns 2 --data-dir .
chatwell status --data-dir .
chatwell worker --endpoint http://127.0.0.1:8080 --data-dir .
```

workerはフォアグラウンドで動き、**Ctrl+C** で停止します。サーバーは停止しません。登録済みの依頼だけ処理して終了するには `--once` を付けます。モデル不要の確認には `--mock --once` を使えます。

- 依頼は登録順に1件ずつ処理し、同じデータディレクトリのworkerはOSのファイルロックで二重起動を防ぎます。ローカルファイルシステムで使用してください。
- `status` は保存された依頼の状態別件数を表示します。workerの稼働判定は行いません。
- 停止中にも登録可能です。入力は登録時にコピーされ、元ファイルの変更は反映されません。
- 通常の失敗は記録して次の依頼へ進みます。`--once` の終了コード0はworkerの正常終了であり、全依頼の成功を意味しません。件数と依頼JSONを確認してください。
- 強制終了で残った `Running` は次のworker起動時に復旧します。自動で重複生成せず、未完了の会話は失敗として保持します。
- 締め処理だけの失敗は会話本体を `Ended` のまま保持し、`ending_error` に記録します。

| 保存先（data-dir基準） | 内容 |
|---|---|
| `.runtime/queue/<id>.json` | 入力のコピー、状態、エラー、セッションパス |
| `sessions/<id>.json` | 会話の逐次保存 |
| `debug/<id>/response-*.json` | workerの生API応答 |

直接実行の生応答は `--debug-dir` を指定した場合のみ保存します。デバッグ情報には会話内容が含まれます。入力・結果・認証設定の取り扱いに注意してください。

## 困ったとき

| 症状 | 確認すること |
|---|---|
| 接続不能・タイムアウト | サーバー起動、ホスト・ポート、ネットワーク、モデル読込時間、`--timeout` |
| HTTP 401 / 403 | `CHATWELL_API_KEY` とサーバー側の認証・アクセス許可 |
| HTTP 404 / 400 | ベースURL、モデルID、llama.cppのバージョンと構造化出力対応 |
| JSON不正・応答打切り | 思考抽出設定、モデル対応、`max_tokens`、サーバー側ログ |
| workerの二重起動エラー | 同じdata-dirで既に動いているworkerの端末を確認 |

## 開発

```bash
python3 -m unittest discover -s tests -v
python3 -m chatwell run --input samples/input.json --mock --max-turns 3
```

主要コードは `chatwell/`、公開入力例は `samples/`、テスト専用データは `tests/fixtures/` です。CIはLinux・macOS・WindowsのPython 3.10/3.13でテストとインストール・モック実行を行います。実モデルの品質検証は自動テストから分離しています。

- [v0.3.0リリースノート・移行手順](docs/releases/v0.3.0.md)
- [開発・リリース手順](CONTRIBUTING.md)
- [v0.2.0](docs/releases/v0.2.0.md)以前の記録は過去の環境での結果です。

GUI、履歴要約・圧縮、途中再開、自動再試行、依頼キャンセルは未実装です。goal未指定時はターン上限まで生成します。長い会話ではコンテキスト不足が起こり得ます。
