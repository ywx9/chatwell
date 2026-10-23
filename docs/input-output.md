# 入出力仕様（v0.3.0）

`samples/input.json` が直接実行・キュー登録の共通入力です。トップレベルは `config` と `characters` のみです。文字列の必須項目は空白だけにできません。未知のキーは拒否します。

## config

| キー | 内容 |
|---|---|
| language | 必須。生成言語 |
| situation | 必須。開始時の状況 |
| goal | 任意。会話の目標。Coordinatorだけに渡す |
| max_turns | 任意。1以上の整数。既定12。CLIで上書き可能 |
| llm_config | 任意。以下の生成設定 |
| coordinator | 任意。`narration_style` で描写方針を指定 |

`llm_config` の対応項目は `temperature/max_tokens/top_p/top_k/min_p/seed/repeat_penalty` です。temperatureの既定は0.8、max_tokensの既定は共通設定の6144です。入力でのmax_tokens指定は共通設定より優先します。

## characters

1人以上の人物を配列で指定します。先頭が最初に発言します。人物名は重複できません。

| キー | 内容 |
|---|---|
| name | 人物名 |
| profile | 公開プロフィール |
| persona | 本人にのみ渡す内面設定 |
| speech | `first_person/second_person/default_address/style` を持つオブジェクト |
| relationships | 相手の名前をキー、関係の説明を値とするオブジェクト。空でも可 |

## セッションJSON

トップレベルは `id/status/timestamps/config/characters/conversation` です。状態は `Queued → Starting → Running → Ended` と進み、本体の失敗・中断は `Failed` になります。保存は一時ファイルへの書込・fsync後のatomic replaceです。

`conversation` には開始状況、目標、非参加者、`beginning_narration`、`turns`、`ending_narration`、`stop_reason` を保存します。各ターンにはCharacter生成の `speech/thought/narration/expression` と、Coordinatorの確定した地の文・最新状況・進捗・次話者推薦を保存します。

通常、Nターンで初期化1回 + Character/Coordinator各N回 + 締め1回の `2N + 2` 回生成します。目標達成時は `goal_achieved`、上限時は `max_turns_reached` です。締めだけ失敗した場合は会話本体を `Ended` のまま保持し、`ending_error` を追加します。不正な受信結果や未完了のターンは `pending_ending` / `pending_turn` 等で保持する場合があります。

キャラクターには他者のthought・persona、goal、進捗評価、次話者推薦を渡しません。自分のthoughtは保持し、過去の地の文はCoordinatorの確定版を使います。自由文経由の意味上の漏洩を完全に検出する仕組みではありません。

次話者推薦は有効な名前かつconfidence 0.7以上で採用し、それ以外は確定済み発言回数が少ない人物、同数なら入力配列順です。既存のセッションは移行・書換えを行いません。
