# 初回動作確認結果

この文書は旧版の検証記録です。現行版の操作・検証結果は [README](../../README.md) と [v0.4.0](../releases/v0.4.0.md) を参照してください。

実施日: 2026-10-22（日本時間）。指定GGUFを使用し、外部推論APIは使用していません。

## 実行結果

| 段階 | 方法 | 結果 |
| --- | --- | --- |
| 環境調査 | Python/CMake、レジストリ、Windows API、GGUFヘッダー | 完了。CIM権限不足・専用VRAM取得不可はREADME記載 |
| llama.cpp導入 | `python scripts/install_llama.py` | 公式CPU版b11342をSHA256検証後に展開 |
| モデル読込 | 指定GGUF、CPU4スレッド、ctx4096、parallel1 | 成功、`/health`は`{"status":"ok"}` |
| 平文接続 | `--probe` | 実モデル応答`Hello!` |
| 構造化生成 | 調整者・ターンのJSON Schema | 実モデルで成功、JSON解析・構造検証成功 |
| 調整者 | 2人のprofile/relationships、開始状況、goal | 英語current_situation、Charlieの抽出、日本語beginning_narrationを確認 |
| 先頭キャラクター | Alice、Japanese指定 | 日本語1ターン、Bob・Charlieによる発言なし |
| 保存 | 各状態でatomic replace | 単一JSON、正常終了`Ended`、終了時刻あり |
| mock実行 | `python -m chatwell --mock --probe --output-dir sessions/mock` | 成功、固定応答として明示 |
| 自動テスト | `python -m unittest discover -s tests -v` | **9件すべて成功** |

実モデル検証コマンド:

```powershell
python -m chatwell --probe --timeout 240 --debug-dir debug/real-verified --output-dir sessions/real-verified
```

実モデル成果物:

```text
sessions/real-verified/027245a7-f748-47cd-afc3-0591f77e2b2e.json
debug/real-verified/response-001.json  # 平文probe
debug/real-verified/response-002.json  # 調整者
debug/real-verified/response-003.json  # Alice
.runtime/logs/server.err.log
```

セッション実行時間は約12.2秒（モデル読込・平文probeは除外）。開始ナレーション:

> 授業が終わり、静かなカフェでアリスとボブは会った。二人はお互いに、もっと話しやすい関係になりたいと感じていた。

Aliceの発言:

> あの、ボブさん。今日は、ゆっくり話したいと思っています。

初回実モデル実行ではJSON構造と日本語発言は正しかったものの、開始ナレーションが英語でした。調整者プロンプトでフィールド別言語規則を明確にし、上記の再実行で日本語を確認しています。生成品質は今回のサンプルに対する確認であり、すべての入力への遵守を保証するものではありません。

## テスト範囲

1. 保存の各時点でパース可能、templatesとのキー一致、状態順序、途中の時刻、スナップショット、非参加者集合、先頭話者。
2. mockによるtimeout・構造エラー・接続拒否時のFailed保存と終了時刻。
3. ターン出力に不正なnameがある場合の拒否と開始ナレーション保持。
4. 重複人物名、空の言語、API制御項目の上書き入力を拒否。
5. ローカルHTTPスタブでAPIパス・Schema送信、正常JSON、壊れたJSON、トークン上限打ち切りを検証。
6. ローカルHTTPスタブの実タイムアウトでFailed・endedを検証。
7. mock平文probeの回帰テスト。
8. 外部ホスト等のendpoint入力を拒否。
9. 実際のローカル未待受ポートへの接続拒否でFailed保存。

実モデルのタイムアウトを意図的に起こすテストはしていません。タイムアウト処理はローカルHTTPスタブで、構造不正はmockとHTTPスタブで検証しています。状態遷移途中の読取は自動テストのobserverで確認しました。

## 未実施・暫定事項

26Bモデル、GPU推論、ソースビルド、長時間・複数ターン生成は未実施です。1ターンでの終了と空のending_narrationは初回プロトタイプ用の暫定処理です。正常終了状態はセットアップ文書のCompletedよりtemplatesのEndedを優先しています。モデルの取得履歴は不明ですが、GGUF内の出所・ライセンス情報はREADMEに記録しました。

検証用に起動したサーバーは検証後に停止しています。再実行時は`scripts/start-server.ps1`で起動してください。
