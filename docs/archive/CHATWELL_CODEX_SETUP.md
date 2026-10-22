> v0.1.0開発時の履歴資料です。現在の実行手順は[README](../../README.md)を参照してください。以下は当時の原文です。

# CHATWELL — Codex向け環境構築・初回テスト仕様

## この文書の位置づけ

VS CodeのCodexに渡す、CHATWELLの初期実装と動作確認のための資料。

- **確定事項**と**初回テスト用の暫定方針**を区別する。
- 不明点を過度に一般化・拡張しない。まず最小限の動く実装を作る。
- ローカルで実行し、外部LLM APIは使わない。
- GitHubへの公開、push、自動同期は**今回の対象外**。
- この文書中のJSONに書かれた説明文は**値の仕様説明用プレースホルダー**であり、実データではない。

## 1. プロジェクトの目的と用語【確定】

**CHATWELL** は、テキストで定義したキャラクター同士の会話をLLMに生成させるシステム。名称には「井戸端会議（CHat Around The WELL）」「上手く会話する」「会話が湧き出す場所」の意味が含まれる。

- **Character**: JSONで定義するキャラクター。
- **Conversation / session**: 参加キャラクター、状況、生成設定などを与えて開始し、停止するまで継続したひとまとまりの会話。**複数の発言を含んでも1会話**。
- **Coordinator（調整者）**: キャラクターとは別の、会話生成を支援するシステム側の役割。会話の登場人物ではない。
- **Turn**: 一人のキャラクターが担当する、発言・内心・地の文・表情を含み得る1ターン。

**1セッションにつき1つのJSONファイル**に開始時設定、キャラクター定義のスナップショット、生成された会話をまとめて保存する。進行中は同一JSONファイルを更新し、停止時に完成させる。`config.json` / `character.json` / `conversation.json` はそれぞれ構造を説明するための呼び名であり、セッション成果物を3ファイルに分割するという意味ではない。

## 2. ローカル実行環境【確定】

- 想定モデル: **Gemma 4 26B A4B（instruction-tuned / it系を優先）の量子化GGUF**。
- 推論エンジン: **llama.cpp**。
- 計算はローカルで行う。
- Coordinatorは原則として英語で作業し、キャラクターが話す言語は設定で別途指定する（最初のテストは日本語を想定）。
- llama.cppのローカルサーバーが提供するOpenAI互換 `/v1/chat/completions` を利用する構成を優先する（これは実装上の提案）。
- Gemma 4のネイティブチャットテンプレートとsystem/userロールを尊重する。モデルが返すテキストとJSONの制約処理が正常に動作するか実機で検証する。

### 環境構築に関するCodexへの指示

1. OS、CPU、メモリ、GPU、VRAM、Python、CMake、既存のllama.cpp、GGUFファイルの有無を調べる。
2. 動作可能なllama.cppの導入・ビルド方法を選び、コマンドをREADMEに記載する。既存環境を壊さない。
3. 適切なGemma 4 26B A4Bの**instruct GGUF**を探し、量子化形式・推定RAM/VRAM・取得元・ライセンスを明記する。未取得モデルについて、サイズやライセンス条件を確認せず大容量ダウンロードを始めない。
4. 既存のGGUFがあればそれを優先する。26Bが現環境で実行不可能なら、その事実を記録し、**LLM接続部分をmockでテスト**できるようにする。実モデルで試していないことを成功と報告しない。
5. 必要に応じてモデルのコンテキスト長、GPU offload、サーバーポート等を設定可能にする。モデルの最大公称コンテキスト長を初回から確保する必要はない。

参考（実装時点の最新仕様を再確認）:
- Gemma 4公式: https://gemma.ai/models/gemma-4/
- Gemma 4 / llama.cpp公式ガイド: https://gemma.ai/deployment/llamacpp/
- llama.cpp: https://github.com/ggml-org/llama.cpp
- llama.cpp API: https://llama.app/docs/api

## 3. キャラクター定義【確定】

キャラクターの入力JSONに期待するフィールド（値は説明文）。

```json
{
  "name": "Their name",
  "profile": "How others perceive them",
  "persona": "How they see themself",
  "speech": {
    "first_person": "How they refer to themself",
    "second_person": "How they address people whose names they don't know",
    "default_address": "How they address people whose names they know",
    "style": "Their general speaking style"
  },
  "relationships": {
    "name_of_first_one": "Their relationship with this person and how they address them"
  }
}
```

注意:
- **単数theyは意図的な表現**。修正する必要はない。
- `profile` は他人から見た人物像、`persona` は本人の自己認識。区別する。
- `relationships` のキーは相手の名前、値は関係と呼び方をまとめた自由記述。
- キャラクター定義は会話開始時のスナップショットをセッションファイルへ格納する。

## 4. 開始時設定【概念は確定、配置は一部暫定】

開始前に与える情報:

- `characters`: **発言者として参加するキャラクターJSONの配列**。配列順は意味を持ち、**先頭のキャラクターが最初に生成する**。
- `situation`: 会話開始時のシチュエーション。
- `goal`: 会話終了時に期待する状況（任意）。**即時停止条件ではない**。
- `config`: LLM設定（例: `temperature`, 1回の出力に対する`max_tokens`）と出力言語の設定。

初回の入力例（仮に`request.json`とする。ファイル名は未確定）:

```json
{
  "characters": [
    {
      "name": "Alice",
      "profile": "A reserved student who seems calm to others.",
      "persona": "She is curious and somewhat shy.",
      "speech": {
        "first_person": "私",
        "second_person": "あなた",
        "default_address": "Name + さん",
        "style": "Polite and gentle Japanese"
      },
      "relationships": {"Bob": "A classmate whom Alice respects; calls him Bob-san."}
    },
    {
      "name": "Bob",
      "profile": "A friendly and talkative classmate.",
      "persona": "He enjoys meeting people and puts others at ease.",
      "speech": {
        "first_person": "僕",
        "second_person": "君",
        "default_address": "Usually uses given names",
        "style": "Casual but considerate Japanese"
      },
      "relationships": {"Alice": "A classmate; calls her Alice-san.", "Charlie": "An older friend who is not present."}
    }
  ],
  "situation": "Alice and Bob have met in a quiet cafe after class.",
  "goal": "They feel more comfortable talking to each other.",
  "config": {
    "language": {
      "coordinator": "English",
      "conversation": "Japanese"
    },
    "llm": {
      "temperature": 0.8,
      "max_tokens": 512
    }
  }
}
```

**留意:** `config.language` と `config.llm` への階層化は以前の提案に基づく**初回テスト用の暫定案**。LLM設定を `config` 直下に置くかどうかは正式決定していない。既存構造を無断で破壊するような複雑な抽象化は不要。

## 5. 出力会話オブジェクト【確定】

```json
{
  "beginning_narration": "Narration describing the situation at the beginning of the conversation",
  "turns": [
    {
      "name": "The name of the character taking this turn",
      "speech": "What the character says aloud",
      "thought": "What the character thinks but does not say aloud",
      "narration": "Narration describing the character's actions, events, or the surrounding situation",
      "expression": "The character's facial expression"
    }
  ],
  "ending_narration": "Narration describing the situation at the end of the conversation",
  "stop_reason": "The reason why the conversation generation stopped"
}
```

- `speech` / `thought` / `narration` / `expression` は、内容がない場合には**省略可能**とする。
- `name` は参加キャラクターに対応する。呼び出し側で付与してもよい。
- `beginning_narration` は開始時の地の文、`ending_narration` は終了時の地の文。
- `stop_reason` は**システム上の生成停止理由**（ユーザー停止、エラーなど）。物語の結末と混同しない。
- JSONの構文が正しいことと、キャラクター設定が守られていることは別途検査する。

## 6. セッションファイル【確定】

各会話セッションは以下の形の**単一JSON**に保存する。以下の文字列値はフィールドの説明。

```json
{
  "id": "Unique identifier of the conversation",
  "status": "Queued / Starting / Running / Completed / Failed",
  "timestamps": {
    "queued": "The date and time when the conversation was queued",
    "started": "The date and time when the conversation started",
    "ended": "The date and time when the conversation ended"
  },
  "config": "The configuration used to generate the conversation",
  "characters": "The character definitions used in the conversation",
  "conversation": "The generated conversation, including narration, turns, and the stop reason"
}
```

実データでは`config`はオブジェクト、`characters`は配列、`conversation`は上記5節のオブジェクト。`situation`/`goal`はセッションの`config`に含める（入力の`config`と異なるスコープなので、変換処理を明示する）。キャラクターを重複格納しない。

状態遷移の想定:

```text
Queued -> Starting -> Running -> Completed
   \          \          \
    +----------+-----------> Failed
```

- `Starting`: 重い前処理を行っている状態。**明示的に区別することが必須**。
- `timestamps.started`: `Starting` に遷移した時刻。
- `timestamps.queued`: キューに入った時刻。
- `timestamps.ended`: 完了／失敗など終了時の時刻。進行中は `null` とする案を初回テストでは採用する。
- ISO 8601形式でUTCオフセットを含む日時文字列を用いる。
- **同じJSONファイルを会話中に随時更新する。** JSONL形式に変更したり、発言ごとに別ファイルへ分割したりしない。
- 更新途中に壊れたJSONが残りにくいよう、一時ファイルへ全体を書いてからatomic replaceする方式を初回実装の候補とする。JSONオブジェクトの末尾に生テキストを追記して不正なJSONにしないこと。

## 7. 会話生成フロー【今回確定した範囲】

### 7.1 調整者による初期化

入力:
- 開始時の`situation`と任意の`goal`
- 会話参加キャラクター全員の`name`, `profile`, `relationships`（必要に応じその他の補足情報）

調整者への指示（原則英語）:
1. 与えられた開始状況・`profile`・`relationships`を踏まえて、会話開始時点の**現状（current situation）**を文章で整理する。
2. キャラクター定義と矛盾する情報を創作しない。場面を自然にする細部の補完は許容する。
3. `relationships`などから言及されている**非登場人物**を列挙する。会話の参加者は含めない。
4. 非登場人物は**話題に出すことはできるが、発言者になったり直接会話に参加したりしてはならない**。
5. 現状をもとに、`beginning_narration`（会話開始時の状況を描く地の文）を生成する。

`relationships`のキーとして明示された非参加者は、プログラム側でも集合差分により確定できる。テキスト中にだけ登場する人物は調整者による抽出の対象になる。非登場人物の判定や抽出結果は、少なくとも初回テストで確認する。

`current_situation` は**内部の生成用情報**、`beginning_narration` は**セッションJSONに保存する物語本文**として区別する。内部情報を永続化するかは未決定。最初はメモリ保持でよい。

### 7.2 最初のキャラクターの1ターン生成

`characters` 配列の**先頭**のキャラクターに対して、少なくとも以下を渡す:
- 調整者がまとめた `current_situation`
- 当該キャラクターの `persona`
- 当該キャラクターの `speech`（一人称・呼び方・話し方）
- 会話の登場人物（参加者）の一覧
- 非登場人物の一覧と、彼らを会話させない制約
- `config.language.conversation` で指定された**発言言語**（例: Japanese）
- そのキャラクターとして次の1ターンを生成する指示

**プロンプト方針:**
- **規則と指示は自然言語**（英語を基本）。
- **キャラクター、状況、人物一覧など変数的なデータはJSON**として渡してよい。
- JSONで渡してもよいが、JSON化そのものは指示遵守を保証しない。比較しやすいよう、プロンプトテンプレートは分離して保存する。
- ロールの扱いはGemma 4のチャットテンプレートに合わせる。独自の生テンプレート文字列を手書きするよりAPIにrole付きmessagesを送ることを優先する。

調整者出力とターン出力は、llama.cppの**JSON Schemaによる制約付き生成**（`response_format`）が実際に使用できるか試す。対応形式は利用バージョンのサーバー仕様に従う。JSON Schemaのサブセット制限があれば簡単なものに落とす。

ターン例（Japanese出力）:

```json
{
  "speech": "今日は静かですね。",
  "thought": "少し緊張するな。",
  "narration": "Aliceは窓の外に目を向けた。",
  "expression": "控えめな笑顔"
}
```

呼び出し側が`name: "Alice"`を付与してセッションの`conversation.turns`に追加する。

### 7.3 その先は未決定

- 2ターン目以降の話者決定方法
- 会話履歴をどの範囲で次のキャラクターに渡すか
- 調整者が状況をいつ更新するか
- `goal`の達成判定方法
- 自動停止条件
- 長期的なトークン予算の管理
- 会話終了時の`ending_narration`の具体的な生成タイミング

**これらの本格実装は今回の対象外。** 初回の単一ターン実行を正常終了させるための`ending_narration`と`stop_reason`は、**テスト用の仮の終了処理**で埋めてよい。その場合、正式な会話終了ロジックと混同しないようREADMEに明記する。

## 8. 初回実装で作ってほしいもの【Codexへの作業指示】

### 8.1 技術方針

- 初回プロトタイプは**Python推奨**（C++化・高速化・GUI化は今回不要）。
- 標準ライブラリを優先し、必要な依存を明記する。
- ローカルllama.cppサーバーへの接続先をCLIまたは環境変数で変更できること。
- APIの呼び出し、プロンプトの構築、JSON検証、ファイル保存を分離し、最小限のユニットテストを付ける。
- 進行状況がCLIログで分かるようにする（Queued / Starting / Running / Completed / Failed）。
- デバッグ時にはLLMからの生レスポンスを確認できるようにし、公開用成果物に不用意に混在させない。

### 8.2 段階的なテスト

1. **環境確認:** バージョン、デバイス資源、モデル候補、llama.cppサーバーの起動手順を報告。
2. **接続テスト:** ローカルエンドポイントで英語の短いテキストを生成。
3. **構造化出力テスト:** JSON Schemaに従う応答を受け取り、解析・検証できることを確認。
4. **調整者テスト:** テスト用2人のキャラクターから`current_situation`、`non_participants`、`beginning_narration`を生成し内容を確認。
5. **キャラクターテスト:** 配列先頭のキャラクターから日本語の1ターンを生成し、`speech`などをJSONとして取得。
6. **保存テスト:** セッションJSONが`Queued`→`Starting`→`Running`→`Completed`と更新され、途中でも妥当なJSONとして読めることを確認。
7. **異常系テスト:** モデル応答のタイムアウト、構造不正、サーバー未起動の場合に`Failed`へ遷移し、`ended`が記録されることを確認。
8. **mockテスト:** 実モデルが利用できなくても、定義済みのLLM応答で2〜7のうちLLM以外のロジックを検証可能にする。

### 8.3 完了条件

- 実機で実行できた段階と、mockのみの段階が明確に区別されている。
- キャラクター設定・会話設定を読み込める。
- 調整者が初期状況、非登場人物、開始時の地の文を生成できる（少なくともmockで確認）。
- 先頭キャラクターの1ターンが指定言語で出力される（少なくともmockで確認）。
- **完成した単一セッションJSON**がパース可能で、上記の確定スキーマに沿う。
- 手動実行方法、設定方法、実行上の制約がREADMEに書かれている。
- 通信先はローカルであり、外部の有料推論APIに依存しない。

## 9. 今回やらないこと

- `chatwell-history`へのpushや公開方式の実装。
- 履歴本文の暗号化・難読化の仕様決定。
- 本格的な複数ターン・長時間自律会話。
- Web UI、配信、バックエンドの大規模化。
- 会話の記憶、履歴要約、キャラクターごとの認識状態の管理。
- 未承認のスキーマ変更。必要な提案は実装仕様とは分けて記録すること。

## 10. Codexに対する最終依頼

現在のワークスペースを調査し、上記の初回プロトタイプを実装・テストしてください。最初に環境を検査して実行可能なプランを決め、実モデルでのテストが難しければmockテストを先に完成させてください。作成したファイル、実行したコマンド、各テストの成否、未実施事項、仕様上の暫定判断を最後に簡潔に報告してください。**GitHubへのpushや公開は行わないでください。**
