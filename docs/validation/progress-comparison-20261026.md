# 目標処理の統一とprogressの確認（2026-10-26）

初期化から目標の生成・整形を除き、共通目標と個人目標を同じ評価形式へ統一した変更の検証記録です。

## 自動テストと初期化の再現

`python -m unittest discover -s tests -v` は89件中86件成功、Windows対象外の3件をスキップしました。

報告された `20261025-150456-798Z-4a32a0f9f5b8.json` の初期化応答を再生し、以降の1ターンをMockClientで生成しました。不要な共通目標の出力で停止せず、入力の個人目標を保持し、ターン上限で正常終了しました。元セッションは変更していません。リポジトリの回帰テストにも、同じエラーの構造を持つ中立的な入力を追加しています。

## 実LLMでの固定会話の比較

モデルは `Melody1437-26B-A4B.i1-Q4_K_M.gguf`。temperature=0、seed=42、max_tokens=4096、reasoning_budget_tokens=2048で、各条件1回ずつ実行しました。目標は「報告書を提出し、Bobの明示的な承認を得る」です。

| 状況 | 期待する判定 | 旧版の共通目標 | 修正版の共通目標 | 修正版の個人目標・日本語キー |
|---|---|---|---|---|
| 下書きを開始 | Approaching | Unchanged | Approaching | 未実施 |
| 提出したが承認はない | Approaching | Approaching | Approaching | Approaching |
| 提出後、明示的な承認を得た | Achieved | Achieved | Achieved | Achieved |
| 無関係な発言 | Unchanged | Unchanged | Unchanged | 未実施 |

旧版は `5ea9903` の更新指示文と文字列のprogress形式、修正版は共通化した指示文と目標別オブジェクト形式を使用しました。修正版は確認した6応答が期待する判定と一致しました。ただし、小規模な中立的会話の確認であり、一般的な精度や、過去の誤判定の解消を保証するものではありません。指示文全体とSchemaの違いを含む比較なので、progressの文言変更だけの効果も切り分けていません。

入力、Schema、指示文、応答、生成設定は [JSON記録](progress-comparison-20261026.json) に保存しています。非公開の推論文や認証情報は含みません。
