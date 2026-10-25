# 入出力仕様（v0.4.0）

HTTP APIの受付・指定ターン取得については [HTTP API仕様](http-api.md) を参照してください。API応答は公開用の投影で、保存JSON全体とは異なります。

## 入力

`samples/input.json` が共通入力です。トップレベルは `config` と `characters`。未知のキーは拒否します。

`config` は必須の `language/situation` と、任意の `goal/max_turns/llm_config/coordinator` を持ちます。max_turnsは1以上の整数で、入力で省略した場合は12です。goalは共通目標の文字列、または参加者名→個別目標のオブジェクトです。共通目標はCoordinatorだけに渡し、個別目標は本人とCoordinatorにだけ渡します。未知の参加者名・空の目標文字列は拒否します。部分的な指定も可能で、空オブジェクトは目標なしとして扱います。

`llm_config` は `temperature/max_tokens/top_p/top_k/min_p/seed/repeat_penalty/reasoning_budget_tokens`。temperatureの既定は0.8、max_tokensの既定は共通設定の6144で、入力指定が優先します。`coordinator.narration_style` は描写方針です。

`reasoning_budget_tokens` はllama.cppと同じ名前で、各LLMリクエストのトップレベルにそのまま送ります。初期化・各ターンのcharacter/coordinator・締めのすべてに適用します。省略時は送信せず、`-1` はllama.cppのサーバー設定を使用、`0` は即時に思考を終了、正の整数は思考のトークン予算です。真偽値・小数・`-2`以下は拒否します。`max_tokens` は別途、生成全体の上限として送ります。本文のための独立したトークン枠を予約する設定ではありません。

```json
{
  "llm_config": {
    "max_tokens": 6144,
    "reasoning_budget_tokens": 2048
  }
}
```

対応するllama.cppと、思考終了タグを認識できるモデル・テンプレートが必要です。非対応サーバーが未知のパラメータを無視するかどうかをChatwellから確実に検出する機構はありません。指定値が実際に適用されるかは接続先で確認してください。[llama.cppのリクエスト処理](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/server-common.cpp)に合わせています。

`characters` は1人以上で、各人物は `name/profile/persona/speech/relationships` を持ちます。speechは `first_person/second_person/default_address/style`、relationshipsは人物名→関係の説明です。人物名は重複できず、先頭の人物が最初に話します。

## セッションJSON（schema_version: 1.0）

- `schema_version`: 保存形式のバージョン。Chatwellのバージョンとは別管理。
- `chatwell_version`: 生成に使ったアプリのバージョン。
- `id/status/timestamps`: 識別子、状態、queued/started/updated/ended時刻。時刻はUTCオフセット付きISO 8601。
- `progress`: phaseとcompleted_turns。phaseはqueued/initializing/generating_character/updating_coordinator/generating_ending/compressing_history/ended/failed。
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

initialization.outputは `situation/goal/non_participants/beginning_narration`。非参加者は関係設定のキーから抽出した名前の一覧でも補完します。初期化LLMに関係説明本文は渡しません。個別目標の場合、保存時のgoalは入力オブジェクトをそのまま保持します。

character.outputは `name/speech/thought/narration/expression`。nameはアプリが補います。speechは言葉だけでなく、本人が発する吐息・ため息・笑い声・キスの音なども含み、音声だけの出力も許可します。音自体はspeech、動作の説明はnarration、声に出さない内心はthoughtに記します。音声は状況に自然に伴う場合だけ使い、他者の行動・反応は確定しません。

coordinator.outputは `narration/current_situation/progress/progress_reason/speaker_recommendation`。共通目標ではprogressとprogress_reasonは文字列、個別目標では指定された参加者名をキーとするオブジェクトです。個別目標は指定された全員のprogressがAchievedになった時に終了します。空の目標オブジェクトではターン上限まで続きます。省略された推薦は空名・confidence 0に補完します。複数参加者では直前話者を推薦スキーマと選択処理の両方で候補から除外します。有効な推薦がconfidence 0.75以上なら採用します。それ以外は候補内の最少発言回数をm、各人物の回数をnとして、重み1/(1+n-m)の抽選を行います。参加者が一人なら同じ人物が続けます。

ending.outputは `ending_narration`。圧縮が発生しない場合、正常終了時には通常Nターンで `2N+2` 回の生成を行います。圧縮時は公開要約と更新のある人物の内心要約の呼び出しが追加されます。

状態はQueued → Starting → Running → Ended、本体の失敗・中断はFailedです。目標達成時はgoal_achieved、上限時はmax_turns_reached。締めだけの失敗はEndedとending_errorを残します。不正な受信結果や未完了処理はpending_initialization/pending_turn/pending_ending等に保存します。pending_turnもindexと各呼出しのoutput/metricsを持ちます。締めが未成功の場合、ending.outputに不正な受信結果が残っていることがあるため、statusだけでなくending_errorも確認してください。

保存は一時ファイルへの書込・fsync後のatomic replaceです。ファイル名はUTCの `yyyymmdd-hhmmss-ミリ秒Z-ランダムID.json`。旧セッションを自動移行・書換えしません。旧ファイルには新しい進捗・実効設定・メトリクスがない場合があり、旧形式のAPI参照は保証しません。

モデル情報は要求aliasとサーバーが返すIDを区別します。LLMのプロセス管理・GGUFのハッシュ取得は行いません。外部サーバーの任意の/propsが返すbuild_infoをバージョンとして取得し、未対応・取得不能ならnullにします。取得元はserver_propsです。共有メタデータに認証情報・内部URL・モデルの絶対パスは保存しません。

キャラクターには他者のthought/persona/個別goal、共通goal、進捗、話者推薦を渡しません。開始状況の内部situationとCoordinatorのcurrent_situationも渡さず、公開beginning_narration・履歴・公開要約から状況を判断させます。自分のthoughtは保持し、履歴の地の文はCoordinatorの確定版を使います。自由文経由の意味上の漏洩を完全検出する仕組みではありません。

## 指示文と会話の進行

初期化は最初の発言直前の状態に留め、依頼・了承などの会話を地の文で済ませないよう指示します。人物のspeechのうち言葉による発言は原則1〜2文で、必要な説明などの場合だけ3文以上を許します。短い非言語的な音声はこの文数の目安に含めません。機械的な文数切り捨ては行いません。

profileは他者に渡す公開説明、personaは本人の自覚・振舞いです。自分のprofileはsystemとuserのどちらにも直接渡しません。参加者一覧には自分の名前だけを含め、他者にはnameとprofileを含めます。自分のpersona・口調・関係性などの人物設定はsystemに配置し、状況・他者の公開紹介・履歴はuserメッセージの構造化JSONに保持します。人物設定はアプリの出力形式・場面制約を上書きしません。

Coordinatorは確定済みの発言・行動だけから地の文と現在状況を更新し、次の人物の返答・提案・決定を追加しないよう指示します。Coordinatorにとってgoalは評価専用で、出来事の生成を誘導する根拠にしません。個別目標を持つ人物は自分の目標を行動の動機にできます。話者推薦はspeaker_contextにある候補、公開された直近履歴、直前の人物出力だけを根拠とします。明確な呼びかけ・質問先を高く評価し、単なる言及や全体への問いは低く評価します。これらの意味上の制約はプロンプトによるもので、生成内容の完全な自動検証は行いません。

## 履歴圧縮

ターンiのCharacter・Coordinator両方が完了した後に、両呼び出しの実測prompt_tokensの最大値が `context - max_tokens` を超えているかを判定します。contextは環境設定 `LLM_CONTEXT_SIZE`（既定16384）、max_tokensはその依頼の実効設定です。max_tokensはcontextより小さい値が必要です。比較は厳密な `>` で、事前計数、安全余裕の追加、直近ターン保持数の下限、同一ターン内の圧縮ループは設けません。usageが返らない場合は判定できないため圧縮しません。

既に0〜jを要約済みとし、`k = j + floor((i-j)/2)` までを新しい要約範囲にします。初回のjは-1です。既存要約とターンj+1〜kを統合し、k+1〜iは原文のまま次の呼び出しへ渡します。追加範囲が0ターンならその回は圧縮しません。以後も各ターン終了時に同じ判定を行います。

初回は公開beginning_narrationを公開要約に含めます。内部situationは公開要約に含めません。公開要約には確定した発言・観察可能な出来事だけを時系列の簡潔な箇条書きとして保持します。人物の内心は公開情報を含まない別リクエストで人物ごとに要約し、他者の内心と混ぜません。新しい内心がない人物の要約はそのまま引き継ぎます。goal、進捗評価、人物設定は要約元に含めません。

圧縮後の人物入力は公開要約 `memory`、本人の `private_memory`、未圧縮の履歴です。Coordinatorには公開要約と未圧縮の公開履歴だけを渡し、persona・thought・private_memory・関係説明本文は渡しません。締めには公開要約と未圧縮の公開履歴だけを渡します。開始状況・冒頭描写を重複して渡しません。current_situationはCoordinator・締めの内部情報として扱い、人物には渡しません。話者選択の発言回数は圧縮前を含む全ターンから数えます。

保存JSONの原文initializationとturnsは削除・書換えしません。圧縮実施後はconversationに以下が追加されます。

- `compression`: 最新の `through_turn`、公開要約 `public_facts`、人物名別の内心要約 `private_thoughts`。
- `compressions`: 要約呼び出しごとのoutput/metrics/model/error、およびfrom_turn/through_turn/scope/trigger_prompt_tokens。内心要約にはcharacterも記録します。

要約出力は `{"facts": ["..."]}` 形式です。公開要約と必要な人物の内心要約がすべて検証に通ってから、新しい圧縮境界を反映します。失敗・中断時は依頼をFailedとして保存し、直前の有効な要約と確定済みの原文ターンを保持します。

要約は通常の生成と同じLLM・生成設定を使用します。圧縮用リクエストの分割や、lengthで失敗した呼び出しの自動再試行は行いません。意味上の正確さと実際の短縮量はモデルの出力に依存します。
