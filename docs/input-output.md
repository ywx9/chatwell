# 入出力仕様（v0.3.1）

## 入力

`samples/input.json` が共通入力です。トップレベルは `config` と `characters`。未知のキーは拒否します。

`config` は必須の `language/situation` と、任意の `goal/max_turns/llm_config/coordinator` を持ちます。max_turnsは1以上の整数で、CLI → 入力 → 既定12の順です。goalはCoordinatorだけに渡します。

`llm_config` は `temperature/max_tokens/top_p/top_k/min_p/seed/repeat_penalty`。temperatureの既定は0.8、max_tokensの既定は共通設定の6144で、入力指定が優先します。`coordinator.narration_style` は描写方針です。

`characters` は1人以上で、各人物は `name/profile/persona/speech/relationships` を持ちます。speechは `first_person/second_person/default_address/style`、relationshipsは人物名→関係の説明です。人物名は重複できず、先頭の人物が最初に話します。

## セッションJSON（schema_version: 1.0）

- `schema_version`: 保存形式のバージョン。Chatwellのバージョンとは別管理。
- `chatwell_version`: 生成に使ったアプリのバージョン。
- `id/status/timestamps`: 識別子、状態、queued/started/updated/ended時刻。時刻はUTCオフセット付きISO 8601。
- `progress`: phaseとcompleted_turns。phaseはqueued/initializing/generating_character/updating_coordinator/generating_ending/ended/failed。
- `config/characters`: 登録時の入力を保持。
- `runtime`: 実効max_turns、生成・思考設定、リクエスト待機上限、話者選択閾値、実モデル／モックの別、要求モデルID、応答モデルID、取得可能なサーバー・モデルの識別情報。
- `conversation`: 以下の構造。

```json
{
  "initialization": {"output": {}, "metrics": {}},
  "turns": [
    {
      "index": 0,
      "character": {"output": {}, "metrics": {}},
      "coordinator": {"output": {}, "metrics": {}}
    }
  ],
  "ending": {"output": {}, "metrics": {}},
  "stop_reason": "max_turns_reached"
}
```

未実行のinitialization/endingはnull。実行中のoutputはnull。各呼出しのmetricsは `started/ended/duration_ms/prompt_tokens/completion_tokens/finish_reason`。所要時間は単調時計で計測し、サーバーから得られないトークン数等はnullにします。各呼出しは応答のモデルIDが取得できれば `model` も記録します。API失敗時も時間とエラー種別を残します。

initialization.outputは `situation/goal/non_participants/beginning_narration`。非参加者は入力の関係情報も使って補完します。

character.outputは `name/speech/thought/narration/expression`。nameはアプリが補います。

coordinator.outputは `narration/current_situation/progress/progress_reason/speaker_recommendation`。省略された推薦は空名・confidence 0に補完します。推薦が有効かつconfidence 0.7以上なら採用し、それ以外は発言回数が少ない人物、同数なら入力順です。

ending.outputは `ending_narration`。正常終了時には通常Nターンで `2N+2` 回の生成を行います。

状態はQueued → Starting → Running → Ended、本体の失敗・中断はFailedです。目標達成時はgoal_achieved、上限時はmax_turns_reached。締めだけの失敗はEndedとending_errorを残します。不正な受信結果や未完了処理はpending_initialization/pending_turn/pending_ending等に保存します。pending_turnもindexと各呼出しのoutput/metricsを持ちます。締めが未成功の場合、ending.outputに不正な受信結果が残っていることがあるため、statusだけでなくending_errorも確認してください。

保存は一時ファイルへの書込・fsync後のatomic replaceです。ファイル名はUTCの `yyyymmdd-hhmmss-ミリ秒Z-ランダムID.json`。旧セッションを自動移行・書換えしません。statusツールでは旧形式の状態確認も可能ですが、旧ファイルには新しい進捗・実効設定・メトリクスがありません。

モデル情報は要求aliasとサーバーが返すIDを区別します。self-managed-llamaではGGUFのbasename、SHA-256、実行ファイルのversionを取得し、取得元を記録します。外部サーバーでは任意の/propsが返すbuild_infoをバージョンとして取得し、未対応・取得不能ならnullにします。取得元はserver_propsです。共有メタデータに認証情報・内部URL・モデルの絶対パスは保存しません。

キャラクターには他者のthought/persona、goal、進捗、話者推薦を渡しません。自分のthoughtは保持し、履歴の地の文はCoordinatorの確定版を使います。自由文経由の意味上の漏洩を完全検出する仕組みではありません。
