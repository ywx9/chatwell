# 複数ターン・Coordinator実装の検証

この文書は旧版の検証記録です。現行版の操作・検証結果は [README](../../README.md) と [v0.4.0](../releases/v0.4.0.md) を参照してください。

実施日: 2026-10-22（日本時間）。`CHATWELL_CODEX_NEXT_STEPS.md`を追加仕様として実装しました。初回の環境・検証記録は[initial.md](initial.md)に保持しています。

## 変更内容

| ファイル | 変更 |
| --- | --- |
| `chatwell/session.py` | 複数ターンのループ、毎ターンUpdate、達成・上限停止、途中結果保存 |
| `chatwell/history.py` | キャラクター向け情報の許可リストと、自分のthoughtだけを残す履歴投影 |
| `chatwell/speakers.py` | 推薦採用・発言回数フォールバック、暫定値 |
| `chatwell/prompts.py` | Initialization、必須4文字列、UpdateのSchema・enum・推薦名/数値範囲 |
| `chatwell/prompts/*.txt` | 初期化・Character・Updateの自然言語指示 |
| `chatwell/validation.py` | 新しい出力構造のPython検証 |
| `chatwell/client.py` | 3ターンで達成する明示的mock、NaN等の不正なJSON定数を拒否。既存ローカル通信方式は維持 |
| `chatwell/__main__.py`、`chatwell/__init__.py` | 複数ターンCLI・説明 |
| `templates/conversation.json` | 追加仕様に沿う新しい保存構造 |
| `examples/observation.json` | goalを省略した観察用入力 |
| `tests/test_chatwell.py`、`tests/test_multiturn.py` | 既存テスト更新、新機能・異常系のテスト |
| `README.md` | 操作手順、閲覧範囲、保存形式、暫定値・互換性 |

入力configとcharacter、セッション外形のテンプレートは変更していません。旧セッションの書換え・一括移行も行っていません。llama.cppと指定GGUFは既存のものを再利用し、再導入・モデル取得は行っていません。

## 自動テスト

```powershell
python -m unittest discover -s tests -v
python -m chatwell --mock --probe --output-dir sessions/multiturn-mock
python -m chatwell --mock --config examples/observation.json --max-turns 4 --output-dir sessions/multiturn-no-goal
python -m chatwell --help
```

**21件すべて成功**。mock CLIもAlice → Bob → Aliceの3ターン、`goal_achieved`でEndedまで完了しました。
goalなしのmock CLIも4ターン、すべてUnchanged、`max_turns_reached`で正常終了しました。

検証項目:

- 入力config・人物設定の保持、清書後situation/goal・非参加者の保存。
- Character向け入力に元goal・清書後goal・評価理由・推薦・他者personaが含まれない。
- 全履歴を時系列順で渡し、他者thoughtを削除、自身thoughtを保持。
- Character原文narrationを保存し、次ターンにはCoordinator確定narrationを渡す。
- 4項目すべて必須、すべて空文字でも保存可能。
- confidenceが閾値と同値なら推薦採用、未満は最少ターン数、同数は配列順。不正な人物名・欠落推薦もフォールバック。
- progress enum、confidenceの型・有限性・0〜1範囲、実行上限の検証。
- Achieved後に話者選択・追加生成を行わない。上限停止とはstop_reasonを区別。
- goal未指定時の上限停止と、誤ったAchievedが返った場合の停止抑止。
- 初期化・各ターンの逐次JSON保存。Coordinator失敗時にもpendingの原文を保持。
- 2ターン目のCoordinator処理を中断しても、確定済み履歴とpendingを保持し、Failedとendedを保存。
- ローカルHTTPによるSchema送信、壊れたJSON、トークン打ち切り、実タイムアウト・接続拒否の既存テスト。

mock成果物:

```text
sessions/multiturn-mock/a16e1837-6ee5-4bda-b5e3-17e55962d509.json
```

## 採用した暫定仕様

- 最大12ターン、推薦閾値0.7。`chatwell/speakers.py`と`--max-turns`/`--recommendation-threshold`で変更可能。入力configは変更しないため、再現時はCLI引数も併せて記録する。
- goal未指定: 清書後goalは空文字。CoordinatorへUnchangedを指示し、Pythonは達成判定を停止に採用しない。自然終了は未実装。
- 終了専用LLM呼び出しは追加せず、ending_narrationは空文字。最後の確定narrationとcurrent_situationを保持する。
- `conversation.non_participants`に非登場人物を保存。関係キーの集合差分で補完する。
- `conversation.pending_initialization`と`conversation.pending_turn`は未検証・未確定結果の保存用拡張。成功して確定すると除去し、失敗時は残す。確定済みturnsの件数だけを話者選択に使用する。
- 推薦のname欠落は空文字、confidence欠落は0へ補完。不正な文字列名も発言回数へフォールバック。範囲外confidenceや文字列以外のnameなどの構造不正はFailed。

## 制約

人物の心理・状況・goalの意味的整合性はモデルの判断を含みます。Pythonによる情報制限は構造的な除外であり、Coordinatorの自由文にgoal由来の意味が混じることを完全検出するものではありません。履歴要約・圧縮、長時間の最適化、26B・GPU検証、公開・pushは行っていません。

## 実モデル検証

既存llama.cpp b11342と指定の`%USERPROFILE%\models\gemma-4-E2B-it-Q4_K_M.gguf`を使用しました。CPU4スレッド、1スロット、コンテキスト8192、reasoning off。実行バイナリとモデルの追加ダウンロードはありません。

再現コマンド（別端末でサーバーを起動）:

```powershell
.\scripts\start-server.ps1 -ContextSize 8192
python -m chatwell --max-turns 4 --timeout 240 --debug-dir debug/multiturn-verified --output-dir sessions/multiturn-verified
```

| 項目 | 結果 |
| --- | --- |
| 開始状況・目標 | 元configを保持、清書後の値をconversationに保存 |
| 呼び出し回数 | Initialization 1回 + Character 4回 + Update 4回 = 9回 |
| 話者 | Alice → Bob → Alice → Bob |
| 進捗 | 4ターンともApproaching |
| 推薦 | Bob 0.90、Alice 0.85、Bob 0.95、Alice 0.90 |
| 終了 | `Ended`、`max_turns_reached`、ended記録あり、pendingなし |
| 言語 | 開始地の文・speech・thought・両narration・expressionは日本語 |
| 非登場人物 | Charlieを保存、発言者にはならない |
| 所要時間 | 約184.45秒（モデル読込を除く） |
| JSON検証 | 4ターンの必須文字列・Update構造・元config一致を保存後に再検証、成功 |

成果物:

```text
sessions/multiturn-verified/bc8a3c0e-f3a6-408d-864a-44a9e039be05.json
debug/multiturn-verified/response-001.json      # Initialization
debug/multiturn-verified/response-002..009.json # Character/Update各4回
.runtime/logs/multiturn.err.log
```

発言の抜粋:

> Alice: あ、ボブさん、こんにちは。
>
> Bob: こんにちは、アリスさん。授業の後だから、ちょっとリラックスできる場所がいいよね。
>
> Alice: あの、ボブさんは、何かお話ししたいことがありますか？

初回の4ターン実行ではAliceへの高信頼推薦が続き、挨拶の反復が見られました。履歴は既に起こった出来事であり、その先へ進むこと、直前発言への自然な応答者を検討することをプロンプトに明記しました。上記の再実行では話者交代と会話の継続が確認できています。初回の記録は`sessions/multiturn-goal/f6bcd8c0-3c59-4f59-a19f-e9a41859c0b8.json`に残しています。

**残る品質課題:** Updateのcurrent_situationが全4回とも初期の一文を繰り返し、最新状況の要約としては不十分でした。また、3ターン目の改稿narrationはBobの応答を先取りして描写しています。Python側では出力を勝手に補正せず、原文と改稿結果を保存しています。長い会話での誘導・評価精度は追加観察が必要です。実モデルのAchievedによる停止は今回観察できておらず、その停止経路はmockで確認しました。goalなしの実モデル実行、26B・GPUでの検証は未実施です。

検証用に起動したサーバーは停止済みです。公開・pushは行っていません。
