# CHATWELL

ローカルGGUFとllama.cppで、複数のキャラクターによる会話を生成するPythonプロトタイプです。現在のバージョンは **v0.2.0**。単一JSONをキューへ登録し、Coordinatorによる調整とともに会話を生成・保存します。

## 必要な環境

サーバー管理スクリプトは、本機の **Linux ARM64 / NVIDIA GB10** 向けです。

- Python 3.10以上（追加Pythonパッケージ不要）
- 利用可能なDocker EngineとNVIDIA CDI GPU設定
- ローカルの対応GGUFモデル

Docker・GPUドライバーのインストールとモデルのダウンロードは自動化していません。既存モデルを読み取り専用で参照します。動作確認したllama.cppイメージは [`runtime/llama-image.txt`](runtime/llama-image.txt) にdigestで固定し、ローカルにない場合のみ `setup.py` が取得します。

## 初期設定から生成まで

リポジトリ直下で実行します。`python` がPython 3を指していれば、`python3` の代わりに使用できます。

```bash
python3 scripts/setup.py
python3 scripts/start.py
python3 scripts/queue.py --input samples/input.json
python3 scripts/status.py
# 生成が終わり、サービスを終了するとき
python3 scripts/stop.py
```

| コマンド | 処理 |
|---|---|
| `setup.py` | `.env`の作成・確認、GGUF識別子とDockerの確認、固定イメージの準備 |
| `start.py` | 推論サーバーの準備完了まで待ち、バックグラウンドのキュー処理担当を起動 |
| `queue.py --input <path>` | 入力を検証・コピーして生成依頼を登録し、待たずに終了 |
| `status.py` | サーバー・処理担当の状態、モデル、実接続先、思考予算、キュー件数を表示 |
| `stop.py` | 処理担当を終了し、管理対象の推論コンテナを停止・削除 |

停止中でも依頼を登録できます。次の `start.py` で登録順に1件ずつ処理します。同じ入力の再登録は別の依頼です。端末を閉じてもサービスは稼働し、ホスト再起動後の自動起動はありません。

## 本機の設定 `.env`

初回の `setup.py` は [`.env.example`](.env.example) をコピーし、対話端末ではGGUFのパス入力を案内します。既存の `.env` は上書きしません。モデルは必須で、相対パスはChatwellのルート基準です。

```dotenv
CHATWELL_MODEL=../melody1437/gguf/Melody1437-26B-A4B.i1-Q4_K_M.gguf
CHATWELL_PORT=8080
CHATWELL_CONTEXT_SIZE=16384
CHATWELL_GPU_LAYERS=99
CHATWELL_START_TIMEOUT=180
CHATWELL_ENABLE_THINKING=true
CHATWELL_REASONING_BUDGET=4096
CHATWELL_REASONING_FORMAT=auto
CHATWELL_REASONING_IN_CONTENT=false
CHATWELL_MAX_TOKENS=6144
CHATWELL_REQUEST_TIMEOUT=600
```

モデルや推論条件の変更は `.env` を編集し、`stop.py` → `start.py` で反映します。`.env` はGit管理しません。起動中の `status.py` は編集後の設定ではなく、コンテナに記録した起動時の実設定を表示します。`stop.py` は `.env` の読込に依存しません。

設定の優先順位は **対応するCLI引数 → 環境変数 → `.env` → `.env.example` の既定値**です。CLI・環境変数の変更は `.env` に保存しません。

```bash
python3 scripts/start.py --port 8081 --timeout 240
```

`.env` は `KEY=VALUE` 形式で、値の外側の引用符は使用できます。シェル・変数展開や行末コメントには対応せず、コメントは独立した行に記述します。`reasoning_effort` は明示指定しません。

思考予算4096は `--reasoning-budget` に渡し、生成全体の上限6144と別に管理します。思考予算を使い切っても本文に約2048トークンの余地が残る設定であり、本文の上限自体を2048に固定するものではありません。入力の `llm_config.max_tokens` で生成全体の上限を上書きできます。

thinking有効時は構造化JSONの本文に思考を混ぜないため、`reasoning_format=auto/deepseek`、`reasoning_in_content=false` を使用します。

## 会話の入力

[`samples/input.json`](samples/input.json) が現在の実行用サンプルです。入力項目は今後も拡張する予定で、未対応の項目は黙って無視せず登録時に拒否します。

```json
{
  "config": {
    "language": "日本語",
    "situation": "会話開始時の状況",
    "goal": "任意の会話目標",
    "max_turns": 20,
    "llm_config": {"top_p": 0.95, "min_p": 0.03},
    "coordinator": {"narration_style": "淡々と客観的な状況を描写する"}
  },
  "characters": []
}
```

上記の `characters` は構造説明用です。実行には、サンプルのように人物を1人以上指定してください。人物は `name/profile/persona/speech/relationships` を持ち、配列の先頭が最初に発言します。

- `goal` は省略可能。Coordinator専用の進行・終了判定情報で、キャラクター入力から除外します。人物自身の意図は `situation` や `persona` に書きます。
- `max_turns` は1以上の整数。上限はCLIの `--max-turns` → 入力値 → 既定12の順に決まります。目標達成判定では上限より前に終了します。
- `llm_config` は `temperature/max_tokens/top_p/top_k/min_p/seed/repeat_penalty` に対応。temperatureの既定は0.8、max_tokensはキュー運用では `.env` の値です。
- `coordinator.narration_style` は初期化・毎ターン更新・終了時のCoordinatorに渡す描写方針です。

```bash
python3 scripts/queue.py --input samples/input.json --max-turns 2
```

登録時の入力と実効ターン上限は依頼JSONに保存します。元の入力を編集しても、登録済みの依頼は変わりません。

## 推論と会話の保存

各ターンは **Character生成1回 + Coordinator更新1回**。開始時の初期化1回、正常終了時の締め1回を加え、Nターン完了時は通常 `2N + 2` 回です。

Characterは `speech/thought/narration/expression` を生成します。speechとthoughtは、フィールド全体を「」や（）で囲まないよう共通プロンプトで指示しています。本文中に必要な引用・補足の括弧は許可し、自動削除は行いません。

Coordinatorは地の文と最新状況を調整し、`Approaching/Unchanged/Diverging/Achieved` の進捗と次話者を推薦します。有効な推薦名・confidenceが閾値0.7以上なら採用し、それ以外は確定済み発言回数の少ない人物、同数なら入力の配列順を使います。

キャラクターには、他者のthought・persona、goal、進捗評価、次話者推薦を渡しません。自分のthoughtは保持し、過去の地の文にはCoordinatorの確定版を使います。構造的な入力制限であり、自由文を通じた意味上の漏洩の完全検出ではありません。

| 保存先 | 内容 |
|---|---|
| `.runtime/queue/<依頼ID>.json` | 入力のコピー、実効上限、状態、エラー、`session_path` |
| `sessions/<セッションID>.json` | 入力、人物、会話の逐次保存 |
| `debug/<依頼ID>/response-*.json` | 生API応答 |
| `.runtime/worker.log` | 処理担当のログ |

依頼は `Queued → Running → Ended`、会話は `Queued → Starting → Running → Ended` と進みます。目標達成時は `goal_achieved`、上限時は `max_turns_reached` です。JSONは一時ファイルへの書込・fsync後、atomic replaceで更新します。時刻はUTCオフセット付きISO 8601です。

正常終了時は最後のターンを変更せず、終了専用Coordinatorが `conversation.ending_narration` を生成します。未成立の約束や新しい台詞を追加せず、上限終了でも無理に人物を別れさせず場面を閉じる指示です。

会話本体の失敗・中断は `Failed` とし、確定済みターンと `pending_turn` 等を保持します。正常終了後の締めだけが失敗・中断した場合は、会話を `Ended` のまま保持し、`ending_error` に記録します。不正な締めの受信結果は `pending_ending` に残します。

`stop.py` は処理担当の中断・結果保存を待ってからサーバーを停止します。待機中の依頼は保持します。異常終了後も処理済みの会話を自動で重複生成せず、未完了の会話をFailedとして復旧します。締め処理中の異常終了は、本体の完了状態を保持します。

専用コンテナ `chatwell-llama` はリポジトリの絶対パスを所有ラベルで照合して管理します。他のコンテナは操作せず、使用中ポートや二重起動を確認します。起動失敗時はログ末尾を表示し、調査用コンテナを `stop.py` で片付けられます。停止しても設定・モデル・生成結果は削除しません。

## 開発と検証

```bash
python3 -m unittest discover -s tests -v
python3 -m chatwell --version
# モデル不要の固定応答による確認
python3 -m chatwell --mock --max-turns 3 --output-dir sessions/mock
```

`python -m chatwell` は従来の分割入力による直接実行用です。`examples/` はその実入力とテスト用、`templates/` は従来の基本構造の説明用で、最新の統合入力は `samples/input.json` を参照してください。`.env` の推論設定は `start.py` が起動するキュー処理担当で使用します。直接実行時のmax_tokens既定は512です。

主要コードは `chatwell/`、操作入口は `scripts/`、実行基盤の固定情報は `runtime/` にあります。実モデルを使うテストは自動テストから分離しています。

- [v0.2.0リリースノート](docs/releases/v0.2.0.md)
- [サーバー管理](docs/validation/runtime.md)、[キュー](docs/validation/queue.md)、[思考予算と10ターン実行](docs/validation/reasoning-budget.md)の検証記録
- [v0.1.0](docs/releases/v0.1.0.md)、[初回](docs/validation/initial.md)、[複数ターン](docs/validation/multiturn.md)の過去の検証記録
- [過去の実装指示書](docs/archive/README.md)

GUI、履歴要約・圧縮、途中再開、自動再試行、依頼キャンセルは未実装です。goal未指定時の自然終了も未実装で、上限まで生成します。長い会話ではコンテキスト不足が起こり得ます。生成品質・言語・人物設定の忠実さとJSONの構造検証は別で、今回の単一実行の結果が常に再現されるわけではありません。

`.env`、モデル、セッション、生応答、ログ、実行時状態はGitに含めません。過去のセッションは一括移行せず保持します。v0.1.0用のWindows導入・起動・成果物パッケージ作成スクリプトは削除しました。当時のソースは `v0.1.0` タグで参照できます。
