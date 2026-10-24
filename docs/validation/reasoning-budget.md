# 思考予算4096・生成全体6144での会話検証

この文書は旧版の検証記録です。現行版の操作・検証結果は [README](../../README.md) と [v0.4.0](../releases/v0.4.0.md) を参照してください。

実施日: 2026-10-23（日本時間）。本機のLinux ARM64 / NVIDIA GB10、Python 3.12.3、既存Melody GGUF、固定llama.cppイメージを使用。

## 変更

- `.env` / `.env.example` に `CHATWELL_REASONING_BUDGET=4096` を追加。
- `CHATWELL_MAX_TOKENS=6144` に設定。
- 思考予算を整数として検証し、サーバーの `--reasoning-budget` に接続。-1は無制限、0は即時に思考終了、正数は思考予算。-2以下や整数以外は拒否。
- `reasoning_effort` は引き続き未指定。
- 本文2048は独立した上限ではなく、生成全体から思考4096を差し引いた余地。

## 自動テスト

`python3 -m unittest discover -s tests -v`: **45件すべて成功**。

思考予算の設定値検証、起動引数への4096の伝達、reasoning-effort未指定を追加確認。`git diff --check` 成功。

## 実機条件

```bash
python3 scripts/queue.py --input samples/input.json --max-turns 10
python3 scripts/start.py
```

- 入力を変更せず、元のgoalありで実行。目標達成判定による早期終了は維持。
- コンテキスト16384、同時処理1、GPUレイヤー99。
- thinking=true、reasoning_format=auto、reasoning_in_content=false。
- APIタイムアウト600秒。
- 実起動引数から reasoning=on、reasoning-budget=4096、reasoning-effort未指定を確認。
- 依頼: `.runtime/queue/20261022T174513189786Z-e92f0b48ebfe4a5ca5122a52405ed496.json`

## 実機結果

- **10ターン正常終了**。依頼・会話ともEnded、stop_reason=max_turns_reached、pendingなし。
- 実行時間: 900.75秒（約15.0分。モデル読み込み時間は除く）。
- API呼び出し: 21回（Initialization 1 + Character 10 + Update 10）。全応答finish_reason=stop。JSON解析・既存の構造検証をすべて通過。
- 最大入力: 5898トークン。最大生成: 4381トークン。総生成: 52178トークン。
- 進捗判定: {'Approaching': 10}。
- 会話結果: `sessions/575036f4-3144-43e6-96d2-f06cdd023f61.json`
- 生応答・集計・サーバーログ: `debug/20261022T174513189786Z-e92f0b48ebfe4a5ca5122a52405ed496/`。

| 応答 | 段階 | 入力tokens | 生成tokens | 終了理由 |
|---|---|---:|---:|---|
| response-001.json | Initialization | 483 | 1420 | stop |
| response-002.json | Character | 769 | 1118 | stop |
| response-003.json | Update | 1402 | 4323 | stop |
| response-004.json | Character | 940 | 1058 | stop |
| response-005.json | Update | 1834 | 2619 | stop |
| response-006.json | Character | 1165 | 1034 | stop |
| response-007.json | Update | 2332 | 3305 | stop |
| response-008.json | Character | 1372 | 1359 | stop |
| response-009.json | Update | 2806 | 3075 | stop |
| response-010.json | Character | 1601 | 1778 | stop |
| response-011.json | Update | 3279 | 4231 | stop |
| response-012.json | Character | 1805 | 1152 | stop |
| response-013.json | Update | 3783 | 4343 | stop |
| response-014.json | Character | 2031 | 1311 | stop |
| response-015.json | Update | 4280 | 1561 | stop |
| response-016.json | Character | 2313 | 1173 | stop |
| response-017.json | Update | 4866 | 4346 | stop |
| response-018.json | Character | 2615 | 1309 | stop |
| response-019.json | Update | 5375 | 4348 | stop |
| response-020.json | Character | 2658 | 2934 | stop |
| response-021.json | Update | 5898 | 4381 | stop |

## 品質上の観察

実行・構造上の失敗はなかったが、週末の約束は発言上では成立していない。アリスのからかいとボブの緊張が反復し、7〜9ターン目はアリスが連続で選ばれた。全10回がApproachingという評価は、実際の目標達成との距離を十分反映していないように見える。

最後のcurrent_situationは、実際の台詞にない週末の誘いを進行中の出来事として描写している。Coordinatorが目標への進展を先取りする問題が残る。これは今回のJSON解析・通信・生成上限の失敗とは別の、プロンプトとモデルによる内容整合性の課題。今回の実験中はプロンプトや入力を変更していない。

今回の結果では、思考4096・生成全体6144の設定で10ターンの技術的な完走は確認できた。単一実行の結果であり、会話品質や将来の生成が必ず成功することを保証するものではない。

検証後は `stop.py` で処理担当と推論サーバーを停止し、`status.py` で双方停止中を確認。成果物を保持。公開・pushなし。
