> v0.1.0開発時の履歴資料です。現在の実行手順は[README](../../README.md)を参照してください。以下は当時の原文です。

# CHATWELL — Codex向け次段階実装指示（複数ターン・Coordinator）

## 0. この文書の位置づけ

既存のCHATWELLプロトタイプに対する**変更・拡張指示**。初回の環境構築と単一ターン検証は完了している。既存コード・テスト・`CHATWELL_CODEX_SETUP.md`・`TEST_REPORT.md`を先に読み、現在の実装に合わせて変更すること。既に動いているllama.cppの導入やモデル取得を不必要にやり直さない。

ここでいう「会話（Conversation）」は、開始設定に従って生成を始め、停止するまでの**一つのセッション**を指す。計算はローカルで行い、**セッション全体を一つのJSONファイル**として会話中に逐次更新し、終了時に完成させる。GitHubへの公開・pushは今回の対象外。

記述中の区分：
- **確定**：これまでの設計上の合意。実装に反映する。
- **実装上の選択**：必要な細部は既存コードとの整合性を見て最小限に決め、READMEまたは実装報告で明示する。
- **未確定／対象外**：独断で複雑な仕様を作らない。

## 1. 現在の前提

- 推論基盤：llama.cpp、ローカルGGUF。最終的に想定するモデルは量子化した **Gemma 4 26B A4B**。初回はそれより小さいモデルで検証済み。26BやGPUでの検証はまだ必須にしない。
- キャラクターとは別に、会話を調整する **Coordinator** を設ける。Coordinatorは会話の登場人物ではない。
- Coordinatorの内部処理は原則英語。キャラクターの会話言語は設定から指定する（現行の`config.language: "Japanese"`等を尊重）。出力する地の文などの言語も指定した会話言語に合わせる。
- `config.situation`、`config.goal`などの**ユーザーの入力は変更しない**。Coordinatorによる清書・調整結果は`conversation`以下に別途保存する。
- **`goal`はキャラクターには絶対に渡さない。** キャラクターが目標を自覚している設定が必要なら、それはユーザーが`situation`に記載する。`goal`はCoordinatorによる進行調整と終了判定に使う。
- 発言の内容、内心の判断、観察可能性などは基本的にモデルに判断させる。内心の漏洩対策や誘導強度の過剰な規則化は現時点で行わず、長い会話を実際に生成して観察する。

## 2. Coordinatorの呼び出し時点と責務【確定】

### 2.1 会話開始時：Initialization（一回）

参照するもの：
- 元の`situation`と任意の`goal`
- 参加キャラクターの`name`、`profile`、`relationships`
- 必要に応じてその他の既存初期化情報

生成・保存するもの：
- `conversation.situation`：入力の`situation`を清書し、必要な範囲で具体化した開始状況
- `conversation.goal`：入力の`goal`を清書・明確化したCoordinator専用目標（元の目的は勝手に変更しない）
- `conversation.beginning_narration`：開始時の地の文（会話言語）
- **非登場人物の一覧**：`relationships`等で言及されるが、その会話に参加しておらず、発言させたり登場させたりしない人物。言及自体は禁止しない。

非登場人物の抽出にLLMが必要か、キーから集合演算で確定できるかは実装に応じて判断してよい。リストは次ターン以降のプロンプト構築に利用する。セッションJSONに保存できるようにすること（格納先の細部は既存構造に合わせて決め、報告する）。

Coordinatorが知る`goal`を`beginning_narration`や`conversation.situation`に**既成事実やキャラクターの意思として混入させない**。`goal`が実際に登場人物の意識にある場合は、その根拠が`situation`等に必要。

### 2.2 各キャラクターターン直後：Update（毎ターン）

直前のキャラクターの出力を受け取り、Coordinatorは次の**5項目**を返す。

```json
{
  "narration": "Revised narration for the preceding character's turn",
  "current_situation": "The updated situation after the preceding turn",
  "progress": "Approaching",
  "progress_reason": "Explanation of the assessment of progress toward the goal",
  "speaker_recommendation": {
    "name": "Name of the recommended next speaker",
    "confidence": 0.85
  }
}
```

- `narration`：直前のキャラクターが書いた`narration`を修正した、**確定版の地の文**。キャラクター原文は消さない。状況や方向の調整もできるが、キャラクターの`speech`、`thought`、`expression`を勝手に書き換えない。
- `current_situation`：そのターンとCoordinatorの調整が完了した**時点の現状**。次のキャラクターへ渡す。更新が物語上の新しい出来事を含む場合は、必要に応じて確定版`narration`にも整合させる。
- `progress`、`progress_reason`：目標への進み方と、その理由。**目標達成を最終判定して停止を実行するのはPython側**。
- `speaker_recommendation`：次に発言すると自然な参加キャラクター名と、推薦度を示す`confidence`（0〜1）。スコアは厳密に校正された確率ではない。

Coordinatorは原則**軽い誘導**で会話がgoalへ自然に向かうことを目指す。`Diverging`時の強引な介入や介入強度の複雑な実装は今回不要。`progress`評価と調整内容の一貫性を優先する。

## 3. `progress` enum【確定】

数値進捗率ではなく、次の文字列enumを使用する。

| 値 | 判定 | 方向調整の考え方 |
|---|---|---|
| `Approaching` | ゴールに近づいている | 誘導を弱くする |
| `Unchanged` | ゴールとの距離がほぼ変わらない | 少し誘導する |
| `Diverging` | ゴールから遠ざかっている | より強い誘導が必要になり得る（現段階は経過観察・自然さ重視） |
| `Achieved` | ゴールを達成した | Pythonが終了処理に進む |

前3値は直前までの状態に対する**傾向**、`Achieved`は**達成状態**。Python側で`progress == "Achieved"`なら次の発言生成に進まず終了処理に入る。数値の閾値によるgoal判定は行わない。

`goal`が未指定のときの`progress`の表現と自然終了条件は**未確定**。暫定対応が必要なら仕様外の値を勝手に追加せず、既存動作を壊さない最小限の扱いにして明記する。

## 4. 次の話者の決定【確定】

初回話者は引き続き`characters`配列の**先頭**。

2ターン目以降は、直前のCoordinatorが返した推薦をPythonで扱う。

1. `speaker_recommendation.name`が参加キャラクター名であり、`confidence >= threshold`ならその人物を選ぶ。
2. 推薦度が閾値未満なら、**これまでに確定した各キャラクターのターン数**を数える。
3. カウントが最小のキャラクターを選ぶ。
4. 最小値で同点なら、開始時の`characters`配列で順番が先の人物を選ぶ。
5. `Achieved`なら話者選択を行わず終了処理へ進む。

推薦名が不正・欠落している場合は安全にフォールバックする。閾値の**具体値と設定キーは未確定**なので、変更しやすい定数か暫定設定値を設け、採用値と場所を報告すること。閾値を恣意的に最終仕様扱いしない。

## 5. キャラクターとCoordinatorに見せる情報【確定／初期方針】

| 情報 | Character | Coordinator |
|---|---|---|
| `config.situation` | 原則渡さず、清書後の状況を使用 | 初期化で参照 |
| `config.goal` / `conversation.goal` | **渡さない** | 参照 |
| `conversation.situation` | 参照 | 初期化で生成、以後必要に応じて参照 |
| 最新の`current_situation` | 参照 | 更新する |
| 自分の`persona`、`speech`、`relationships` | 参照 | 必要に応じて参照可能 |
| 他者の`profile` | 参照可能 | 参照可能 |
| 他者の`persona` | 渡さない | 必要に応じて参照可能 |
| 参加者一覧・非登場人物一覧 | 参照 | 管理 |
| `beginning_narration` | 参照 | 生成 |
| 過去の`speech` | 参照 | 参照 |
| 自分の過去の`thought` | 参照 | 参照可能 |
| 他者の過去の`thought` | **除外** | 参照可能 |
| 確定版の過去の`narration` | 参照 | 参照 |
| 過去の`expression` | 参照 | 参照 |
| `progress` / `progress_reason` | 渡さない | 生成・参照 |
| 次話者推薦 | 渡さない | 生成、Pythonが使用 |

- Characterには、**開始時の状況・最新の状況・閲覧可能な全会話履歴**を渡すのが初期方針。履歴は時系列順、話者名付きで構築する。
- 他者の`thought`だけを機械的に除外する。`narration`から間接的に内心を推測できる可能性は、今の段階では問題視せず経過観察する。物理的に観察可能かどうかもモデルの文脈判断に任せる。
- Characterが生成した元の`character.narration`ではなく、**Coordinator確定後のターン直下`narration`**を次ターンの履歴に渡す。
- Coordinatorが会話を評価する場合は必要な会話履歴と各人物の内心も扱える。ただしCoordinatorの知識がそのままキャラクターの知識になるわけではない。
- 毎ターンの全履歴再入力による性能・コンテキスト増加は既知の課題。今回はまず動作・品質を観察し、Nターンごとの要約は実装しない。

## 6. セッションJSONの保存形式【確定】

**キャラクターの生成原文とCoordinatorの出力を、各`turns[]`要素で併記する**。改変前後の地の文が両方残る。セッションには全出力を保存する。

次は値側が説明の**概念例**であり、実際のJSON Schemaでは適切な型、必須、enum、数値範囲を定義すること。

```json
{
  "id": "Unique identifier of the conversation",
  "status": "Queued / Starting / Running / Ended / Failed",
  "timestamps": {
    "queued": "The datetime when the conversation was queued",
    "started": "The datetime when the conversation started",
    "ended": "The datetime when the conversation ended"
  },
  "config": {
    "language": "Japanese",
    "situation": "Original initial situation supplied by the user",
    "goal": "Original desired final situation supplied by the user",
    "llm_config": {
      "temperature": 0.8,
      "max_tokens": 512
    }
  },
  "characters": [
    { "name": "Character definition snapshot; retain other character fields" }
  ],
  "conversation": {
    "situation": "Initial situation refined by the coordinator",
    "goal": "Goal refined by the coordinator; never shown to characters",
    "beginning_narration": "Narration describing the initial situation",
    "turns": [
      {
        "character": {
          "name": "Name of the speaking character",
          "speech": "What the character says aloud",
          "thought": "What the character thinks but does not say aloud",
          "narration": "Narration originally generated by the character",
          "expression": "The character's facial expression"
        },
        "narration": "Final narration revised by the coordinator",
        "current_situation": "The updated situation after this turn",
        "progress": "Approaching",
        "progress_reason": "Why this progress state was chosen",
        "speaker_recommendation": {
          "name": "Name of the recommended next speaker",
          "confidence": 0.85
        }
      }
    ],
    "ending_narration": "Narration describing the situation at the end of the conversation",
    "stop_reason": "Reason why generation stopped"
  }
}
```

- `character.thought`が正しいスペル（`throught`ではない）。
- `character`の`name`, `speech`, `thought`, `narration`, `expression`は**全て必須**。内容がない場合は**空文字列**を許容する。以前の「任意フィールド」という扱いは取り消す。
- 各ターンのCoordinator出力5項目も原則必須。`speaker_recommendation.confidence`は0〜1。
- `conversation.situation`/`conversation.goal`は初期化結果として保存し、`config.situation`/`config.goal`は変更しない。
- `current_situation`は各ターンに記録するので、過去の状態の変化も追跡できる。
- 全セッションのファイルは**会話中も有効なJSON**として保存する。既存のatomic replace方針を維持する。キャラクター生成→Coordinator処理の途中で落ちた場合も、途中の生成結果が失われない保存設計を検討すること（途中状態の表現は実装上の選択として明記）。
- 現行プロトタイプの正常終了ステータスは`Ended`。以前の資料には`Completed`もあるが、今回**無関係な変更を加えず、既存テンプレートと実装の`Ended`を維持**する。
- 開始ナレーションは会話言語で生成する。`ending_narration`をどのLLM呼び出しで生成するか、エラー時にどう扱うかなどは現行動作を確認し、暫定方針を明記する。

## 7. 処理の流れ（今回の到達目標）

```text
ローカルでセッションを作成（1 JSONファイル）
  → status=Starting
  → Coordinator Initialization
     - conversation.situation / conversation.goal
     - beginning_narration / 非登場人物
  → status=Running
  → 最初の話者 = characters[0]
  → [繰り返し]
       Character生成（4必須フィールド、空文字列は可）
       → Coordinator Update（narration調整、現状更新、progress判定、次話者推薦）
       → 全結果をセッションJSONに保存
       → progress == Achieved ?
            Yes: 終了処理へ
            No: Pythonが推薦度と発言回数を用いて次話者を選ぶ
  → 終了処理（stop_reason・ending_narration等）
  → status=Ended（失敗時はFailed）
```

- テスト時に無限に続かないよう、**最大ターン数**を暫定の安全装置として設ける。値・設定箇所は明記する。goal到達と最大ターン数到達は`stop_reason`で区別する。
- 複数ターンが実際に生成されることを確認する。最初から1ターンで止める既存プロトタイプの制限を置き換える。
- `goal`に向けた誘導はCoordinatorの地の文と状況調整に含め、キャラクターに直接目標を教えない。

## 8. 実装・テスト要求

1. **既存コードを先に調査**し、現在のプロンプト構築、LLM入出力、JSON Schema、状態遷移、保存処理を把握する。必要箇所だけ改修する。
2. Coordinator初期化の新フィールドと、毎ターンCoordinator Updateの構造化出力を実装する。
3. `character`側4必須文字列フィールド、Coordinator側5必須フィールド、`progress` enum、推薦の範囲・人物名をスキーマとPython検証で担保する。
4. キャラクターごとに履歴を投影する関数を用意し、**他者の`thought`を取り除き、自身の`thought`は残す**。次に渡す`narration`はCoordinator確定版を使う。`goal`や`progress_reason`の混入を防ぐ。
5. Pythonの次話者推薦採用／閾値未満の発言回数フォールバックを実装する。同点時の配列順、`Achieved`時の非呼び出しを確認する。
6. 全LLM出力が一つのセッションJSONに保存され、各ターンの`character.narration`とCoordinatorの`narration`が両方残ることを確認する。
7. mockテストで、少なくとも**2人で3ターン以上**、異なる推薦度、同点フォールバック、4必須項目、`Achieved`、異常系、逐次保存を検証する。
8. 実モデルが使えるなら、小さい量子化モデルで数ターンの実行を確認する。未実施の26B/GPU検証を成功したと報告しない。
9. READMEまたはテスト報告に、実行方法、採用した暫定値、スキーマ変更点、観察された出力品質、制約を記載する。

### 特に確認したい自動テスト例

- `goal`が含まれるのはCoordinator向け入力のみ。キャラクター向けプロンプトに`config.goal`/`conversation.goal`が含まれない。
- 他者の`thought`は削除され、自身の過去の`thought`は保持される。
- 4フィールドが揃い、空文字列の出力でも構文検証を通る。
- `character.narration`は原文のまま保存され、ターン直下`narration`に改稿版が保存される。
- `confidence`が閾値以上／未満、発言回数の最少・同数時の選択が仕様どおり。
- `Achieved`判定後に次のキャラクターが生成されない。
- 異常時にもセッションJSONを破損させず`Failed`と終了時刻を保存する。

## 9. 今回決めないこと／過度に作り込まないこと

- 履歴圧縮・Nターンごとの要約、その要約方式（将来の拡張対象）。
- KVキャッシュ最適化やトークンコストの最適なバランス。
- 進行誘導の数値化、強制イベント生成、`Diverging`時の複雑な介入ルール。
- 観察可能性や心理描写を厳密に制限する仕組み。モデルの判断に任せ、問題が出たら検討する。
- GGUFによる固有名詞表記ゆれの対策（26B A4Bでも発生するかを見て判断）。
- goal未指定時の恒久的な自然終了判定。
- GitHubへの定期push、履歴公開、暗号化。
- 既存セッションファイルの破壊的な一括移行。必要なら新旧フォーマットの扱いを明記する。

## 10. Codexへの依頼

**以上を、既存プロトタイプに対する追加仕様として実装・検証すること。** 設計上の未確定事項は、まず最小限の可逆な暫定実装とし、選択と理由を報告する。仕様と矛盾する変更（goalをCharacterに渡す、他者のthoughtを直接見せる、4フィールドを任意扱いする、ターン内のキャラクター原文を破棄する、など）は行わない。最後に変更ファイル・実行コマンド・テスト結果・未解決事項を簡潔に報告する。
