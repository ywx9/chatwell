# CHATWELL v0.4.0

複数キャラクターの会話を生成する、ローカル・信頼できるネットワーク内向けのHTTP APIです。Python 3.10以上の標準ライブラリだけで動きます。

## 起動

別途LLMサーバーを起動して、次を実行します。

```bash
python tools/run.py
```

`python` はPython 3.10以上のコマンドを想定しています。環境によっては `python3` に置き換えてください。

既定ではLLMに `http://127.0.0.1:8080` で接続し、Chatwellは `http://127.0.0.1:8090` で待ち受けます。設定ファイル・認証トークン・パッケージのインストールは不要です。

LLMの接続先を変える場合は `.env` に記載するか、環境変数を指定します。

```dotenv
LLM_ENDPOINT=http://127.0.0.1:8080
```

信頼できるネットワーク内の他の端末から使う場合：

```bash
python tools/run.py --host 0.0.0.0
```

起動後はQueueへの登録を受け付け、1件ずつ順次処理します。Queueが空なら待機し、会話が完了しても常駐します。

**Ctrl+Cで受付と生成を中断し、アプリ全体を終了します。** 未着手の依頼は次回起動後に処理し、中断した依頼はFailedとして保持します。締めだけの中断では、完了した会話本体をEndedのまま保持します。

## API

Chatwell側の認証はありません。

```bash
curl http://127.0.0.1:8090/v1/sessions \
  -H 'Content-Type: application/json' \
  --data-binary @samples/input.json
```

起動中のAPIへJSONファイルを投入する補助コマンドも使えます。起動とは別の端末から実行してください。

```bash
python3 tools/enqueue.py --input samples/input.json
```

接続先はリポジトリの `.env` の `CHATWELL_HOST`・`CHATWELL_PORT` から取得します。`0.0.0.0` は接続時に `127.0.0.1` として扱います。別の設定ファイルは `--settings PATH` で指定できます。入力パスはコマンド実行時のディレクトリ基準です。成功時はAPI応答JSONを表示し、失敗時は終了コード1を返します。APIの起動や自動再送は行いません。

202応答の `id` と `url` で進捗・結果を取得します。同じ入力でも、POSTするたびに別の依頼になります。

| API | 用途 |
|---|---|
| `POST /v1/sessions` | 会話をQueueに登録 |
| `GET /v1/sessions/{id}` | 状態と進捗 |
| `GET /v1/sessions/{id}/result` | 会話の結果 |
| `GET /v1/sessions/{id}/turns/{index}` | 指定ターンだけを取得（0始まり） |
| `GET /healthz` | アプリの稼働確認 |

ターン取得は確定済みなら200、生成中で未確定なら202、終了後に存在しなければ404です。取得後も生成は続きます。

目標は `config.goal` に共通の文字列、または `{"人物名": "その人物の目標"}` を指定できます。個別目標は本人とCoordinatorだけへ渡し、指定された全員の達成で終了します。

思考予算は入力JSONの `config.llm_config.reasoning_budget_tokens` で指定できます（例：`2048`）。各LLM呼び出しにそのまま送信します。

入力例は [samples/input.json](samples/input.json)、会話本文は [samples/output.txt](samples/output.txt)、ログの形式例は [samples/output.log](samples/output.log) です。入力例はそのまま登録でき、コピーして人物・状況・生成設定をカスタマイズできます。本文・ログの例は [samples/output.json](samples/output.json) の保存記録から変換したものです。ログの例は最終状態を一括で記載していますが、実行時は処理の進行に合わせて変更内容を追記します。

詳細は [HTTP API仕様](docs/http-api.md)、[入出力仕様](docs/input-output.md) を参照してください。

## 任意の設定

`.env` は `KEY=VALUE` 形式です。環境変数で上書きできます。シェル・変数展開は行いません。設定変更は再起動後に反映します。

| 設定 | 既定値・用途 |
|---|---|
| `LLM_ENDPOINT` | `http://127.0.0.1:8080`。末尾 `/v1` は任意 |
| `LLM_MODEL` | 任意。LLMサーバー側のモデルID |
| `LLM_API_KEY` | 任意。LLMサーバーが認証を要求する場合だけ指定 |
| `LLM_CONTEXT_SIZE` | `16384`。圧縮判定用。LLMサーバーのコンテキストサイズと合わせる |
| `LLM_REQUEST_TIMEOUT` | `3600`秒。1回のLLM応答待ち |
| `LLM_MAX_TOKENS` | `6144`。入力の `llm_config.max_tokens` が優先 |
| `LLM_ENABLE_THINKING` | `true` |
| `LLM_REASONING_FORMAT` | `auto`。`none/deepseek/deepseek-legacy` も指定可能 |
| `LLM_REASONING_IN_CONTENT` | `false`。構造化JSONのためtrueは拒否 |
| `CHATWELL_HOST` | `127.0.0.1` |
| `CHATWELL_PORT` | `8090` |
| `CHATWELL_DATA_DIR` | `data`。相対パスは `.env` のあるディレクトリ基準 |

LLMには `/v1/chat/completions` と `response_format.type=json_schema` の対応が必要です。LLMの起動・停止はChatwellの外で行います。

```bash
python tools/run.py --settings /path/to/settings.env --data-dir /path/to/data --port 8091
python tools/run.py --help
```

会話は `data/sessions/` に、同じ名前の `.log`（実行ログ）と `.txt`（会話本文）で保存します。どちらも追記のみで、生成済みの内容を書き直しません。API参照用の内部JSONも同じ場所に保持します。内部Queueは `data/.runtime/queue/`、生のLLM応答は `data/debug/` に保存します。同じ保存先の二重起動は拒否します。

旧設定の `CHATWELL_ENDPOINT` は `LLM_ENDPOINT` に、ほかのLLM関連の `CHATWELL_*` は `LLM_*` に変更してください。Chatwell側の認証設定、旧CLIと起動管理用tools、別プロセスworker、LLM起動補助は廃止しています。既存の保存先を使う場合は `--data-dir .` などで指定してください。

## 履歴圧縮

確定ターンごとに、Character・Coordinatorの実測 `prompt_tokens` の大きい方が `LLM_CONTEXT_SIZE - max_tokens` を超えたら、未圧縮部分の古い半分を累積要約へ統合します。事前のトークン計数や直近ターン保持数の下限は設けません。実測値が返らない場合は圧縮しません。

最初は開始状況と冒頭描写も統合し、以後は前の要約を引き継ぎます。公開された出来事と人物別の内心を別々に要約し、人物には本人の内心だけを渡します。元のセッションの全ターンは保持し、LLMへの入力だけを要約＋未圧縮履歴に切り替えます。

`LLM_CONTEXT_SIZE` はChatwellの判定値で、LLMサーバーの容量を変更する設定ではありません。サーバーの実際のコンテキストサイズと合わせてください。要約処理には追加のLLM呼び出しが発生します。詳細は [入出力仕様](docs/input-output.md#履歴圧縮) を参照してください。

## 開発

```bash
python3 -m unittest discover -s tests -v
python tools/run.py --mock
```

`--mock` は実LLMを使わない固定応答です。テストではHTTP API、Queue・保存・復旧、常駐、中断と再起動を検証します。Ctrl+Cによる実プロセスの終了テストはLinux/macOSが対象です。Pythonによる起動入口とAPIのテストはCIでLinux・macOS・Windowsを対象にします。

変更点と旧版からの移行は [v0.4.0](docs/releases/v0.4.0.md) を参照してください。`docs/releases/` と `docs/validation/` の旧版資料は過去の実装・検証の記録です。
