# HTTP API

ローカル・信頼できるネットワーク内向けの、認証なしのAPIです。`python tools/run.py` で起動します。既定の接続先は `http://127.0.0.1:8090`。他の端末から使う場合は `python tools/run.py --host 0.0.0.0` で起動します。

## 会話の登録

```bash
curl http://127.0.0.1:8090/v1/sessions \
  -H 'Content-Type: application/json' \
  --data-binary @samples/input.json
```

入力は `config` と `characters` を持つJSONです。詳細は [入出力仕様](input-output.md) を参照してください。`config.max_turns` が生成上限で、省略時は12ターンです。goalを達成すると早く終了することがあります。

応答は202。`id`、`url`、`status`、`timestamps`、`progress`、`stop_reason`、`ending_error` を返し、Locationヘッダーにも参照先を含めます。POSTするたびに別の依頼を作成します。再送による重複の管理は行いません。

## 状態・結果・指定ターン

| メソッド・パス | 応答 |
|---|---|
| `GET /v1/sessions/{id}` | 200：状態と進捗 |
| `GET /v1/sessions/{id}/result` | 202：処理中。200：終了・失敗時の保存済み結果 |
| `GET /v1/sessions/{id}/turns/{index}` | 200：指定ターン。202：未確定。404：終了後に存在しないターン |
| `GET /healthz` | 200：受付可能。503：準備中・終了処理中 |

`healthz` はアプリの状態を確認します。LLMの接続確認や生成は行いません。

ターン番号は0始まりです。characterとcoordinatorの両方が確定した段階で取得できます。会話全体が生成中でも取得でき、取得後も生成は続きます。

```bash
curl http://127.0.0.1:8090/v1/sessions/SESSION_ID/turns/0
```

ターンの200応答：

```json
{
  "index": 0,
  "character": {"output": {}, "metrics": {}, "model": null},
  "coordinator": {"output": {}, "metrics": {}, "model": null}
}
```

未確定の場合は状態概要と `Retry-After: 1`、Locationを返すので、間隔をあけて再取得してください。Failedになったセッションでも、それ以前に確定したターンは取得できます。

resultの200応答は状態概要と `conversation.initialization/turns/ending` を含みます。Failedの場合も保存済みの結果を返すため、`status` を確認してください。締めだけが失敗した場合はEndedを維持して `ending_error: true` になります。未確定の出力や内部の例外詳細は含めません。

## エラー

エラー応答は `{"error": "code"}`。終了後の不存在ターンには状態概要も含めます。

| HTTPコード | 意味 |
|---|---|
| 400 | 不正なJSON・HTTP本文 |
| 404 | 未知のパス・ID、終了後の不存在ターン |
| 411 | Content-Lengthなし |
| 413 | 本文が1 MiB超 |
| 415 | Content-Typeがapplication/json以外 |
| 422 | 入力・ターン番号が不正 |
| 500 | 内部処理・保存の失敗 |
| 503 | 起動準備中・終了処理中 |

## 実行と終了

単一プロセス内でHTTP受付とQueueの順次処理を続けます。Queueが空でも待機を続け、Ctrl+Cで受付と実行中の生成を中断して終了します。LLMの応答待ち中でも会話完了を待ちません。送信済みリクエストのLLMサーバー側の計算停止までは保証しません。

未着手の依頼はQueuedとして残し、次回起動後に処理します。中断した依頼はFailedとして保持し、自動再生成しません。締めだけの中断では会話本体をEndedのまま保持します。

保存先は既定で `.env` のあるディレクトリ配下の `data/`。ローカルファイルシステムを使用し、同じ保存先の二重起動は拒否します。セッションとキューを跨ぐトランザクションではないため、登録途中の異常終了ではキュー未登録のQueuedセッションが残る場合があります。
