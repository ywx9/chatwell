# JSON入力・会話キューの検証

この文書は旧版の検証記録です。現行版の操作・検証結果は [README](../../README.md) と [v0.4.0](../releases/v0.4.0.md) を参照してください。

実施日: 2026-10-23（日本時間）。本機のLinux ARM64 / NVIDIA GB10、Python 3.12.3、既存Melody GGUFと固定llama.cppイメージを使用。

## 実装内容

- `scripts/queue.py --input <path>`: 単一JSONのconfig/charactersを検証し、内容のスナップショットを保存して終了。`--max-turns` の既定12。
- `scripts/_jobs.py` / `_worker.py`: ディスクキューとバックグラウンドの単一処理担当。登録順に逐次生成。flockで多重処理を防ぎ、PIDと生成時刻・コマンドを確認して管理対象へ終了通知。
- `start.py` / `stop.py` / `status.py`: 推論サーバーと処理担当の起動・停止、キュー件数の確認。
- 依頼: `.runtime/queue/<id>.json`。結果: `sessions/<session-id>.json`。生応答: `debug/<job-id>/`。処理担当ログ: `.runtime/worker.log`。
- `config.coordinator.narration_style`: 初期化と毎ターン更新のCoordinatorだけへ送信。
- 推論基盤の設定を`.env`へ移し、`reasoning_effort` は未送信。サンプル入力から推論基盤側の項目を除去。

## 自動テスト

既存の回帰テストに加え、入力のスナップショット・登録順・入力不正時の拒否・失敗後の継続・中断時のpending保持・異常終了復旧・完了直後のクラッシュ復旧・他プロセスの誤停止防止・Coordinatorだけへの設定伝達・送信JSONでの推論設定とeffort未指定を検証。

## 実機で見つかった点

1. 最初の1ターン実行はInitialization・Character・Updateの3回の推論に成功し、Ended / max_turns_reachedで保存された。
2. 最初のCoordinator更新では、地の文に発言と内心の再掲が見られた。地の文は台詞・内心の転載ではなく観察可能な動作等とする指示を追加した。意味上の漏洩を完全検出する機能はない。
3. 停止直後の再起動で、ポート確認がTIME_WAITを競合と誤判定する場合があった。SO_REUSEADDRで確認し、再起動成功を確認した。実際に使用中のポートを拒否するテストは維持。

実行例:

```bash
python3 scripts/queue.py --input samples/input.json --max-turns 2
python3 scripts/start.py
python3 scripts/status.py
python3 scripts/stop.py
```

会話品質、長時間運用、途中再開、自動再試行は今回の機能保証に含めない。GUIは未実装。

追加観察: thinking有効・reasoning_effort未指定で、Coordinatorが4096トークンを思考に使用し、JSON本文を返す前に上限へ達した依頼が1件あった。依頼・セッションともFailed、キャラクターのpending結果と生応答を保持した。生成上限を `.env` / `.env.example` の8192に変更し、新規依頼で再確認した。上限変更は自動再試行ではない。

自動テスト最終結果: `python3 -m unittest discover -s tests -v` **44件すべて成功**。`git diff --check` も成功。

## 最終実機結果

- 8192トークン上限でサンプルの2ターン実行に成功。話者はアリス → ボブ。Initialization 1回 + Character 2回 + Update 2回の計5回、すべてfinish_reason=stop。依頼・会話ともEnded、max_turns_reached、pendingなし。
- 結果: `sessions/8b31008c-7d7c-41d6-9d37-d7ccb09bd0cc.json`。生応答は `debug/20261022T173156031789Z-e88aee6ccf6f4f3d8549b62334e5c746/`。
- 今回の修正後の1ターン目では、地の文への発言・内心の転載が見られなかった。モデル依存のため恒久的な保証ではない。
- 続く新規依頼を処理開始後にstop.pyで中断。依頼・会話ともFailed、会話のstop_reason=user_interrupted、終了時刻を保存し、処理担当・推論サーバーの停止を確認。
- 検証終了時は停止中。旧APIとVAICには変更なし。テストの完了・失敗・中断の依頼と会話結果を保持。12ターンの長時間実行は未検証。公開・pushなし。
