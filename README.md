# CHATWELL

キャラクター定義を読み込み、ローカルllama.cppでCoordinatorによる初期化・毎ターン更新と複数ターンの会話を生成するPythonプロトタイプ。Python 3.10以上、標準ライブラリのみを使用します。

## 実行

ワークスペース直下のPowerShellで実行してください。

```powershell
# 公式CPU版をワークスペース内に導入。既に導入済みならダウンロードしません。
python scripts/install_llama.py

# サーバー起動。この端末はサーバー用に使用します。
.\scripts\start-server.ps1
```

別の端末で:

```powershell
python -m chatwell --probe --timeout 240 --max-turns 12

# モデル不要の固定応答テスト
python -m chatwell --mock --probe --output-dir sessions/mock

# ユニットテスト
python -m unittest discover -s tests -v
```

起動スクリプトは以下のモデルを既定で使用します。モデルのダウンロードは行いません。

```text
%USERPROFILE%\models\gemma-4-E2B-it-Q4_K_M.gguf
```

起動オプションの例:

```powershell
.\scripts\start-server.ps1 -Port 8081 -ContextSize 4096 -GpuLayers 0 -Threads 4
python -m chatwell --endpoint http://127.0.0.1:8081 --config examples/config.json --characters examples/alice.json examples/bob.json
```

接続先は`--endpoint`または環境変数`CHATWELL_ENDPOINT`で変更できます。`http://127.0.0.1:8080`と`http://127.0.0.1:8080/v1`の両方を受け付けます。接続はループバックHTTPのみに限定し、プロキシとリダイレクトを無効化しています。CPU版では`GpuLayers`を増やしてもGPU推論にはなりません。

`--probe`はセッション前に短い英語の応答を確認します。その後の調整者・ターン生成自体がJSON Schemaによる構造化出力の接続テストです。`--debug-dir debug/run1`で生API応答を別ディレクトリへ保存できます。繰り返し実行する場合は異なるデバッグディレクトリを指定してください。

## JSONの契約

**templates内のJSONを正**とします。`CHATWELL_CODEX_NEXT_STEPS.md`の追加仕様に合わせて`templates/conversation.json`を更新しました。入力用のconfig/characterとセッション外形のテンプレートは維持しています。テンプレート中の文字列は説明用であり、入力データではありません。`examples/`に実入力を用意しています。

| 項目 | 採用した構造 |
| --- | --- |
| 設定 | `language`は会話の言語を表す文字列。調整者の内部整理は英語 |
| 開始状況・目標 | `config.situation`、任意の`config.goal` |
| 生成設定 | 任意の`config.llm_config` |
| 正常終了 | `Queued → Starting → Running → Ended` |
| 異常終了 | `Starting`または`Running`から`Failed`、`timestamps.ended`を記録 |
| セッション | `id/status/timestamps/config/characters/conversation`を単一JSONに保存 |

入力は設定ファイルとキャラクターファイルのリストをCLIで指定します。`characters`の指定順を保持し、先頭が最初に発言します。セッションの`config`には入力設定をそのままスナップショット保存し、人物を重複格納しません。Coordinatorの清書結果は`conversation.situation`と`conversation.goal`に別途保存します。`goal`を省略することもできます。

`llm_config`で扱う設定は`temperature/max_tokens/top_p/top_k/min_p/seed/repeat_penalty`です。既定は`temperature=0.8`、`max_tokens=512`。テンプレートの`_`は説明用なので実入力には含めません。役割・出力制約などのAPI制御項目は入力から上書きできません。

各状態・開始時の地の文・ターン確定時に同じファイルを更新します。一時ファイルへの全体書き込み、flush/fsync後に`os.replace`で置換し、途中のファイルも正しいJSONとして読めるようにしています。時刻はUTCオフセット付きISO 8601。進行中の`ended`と、Queued時の`started`は`null`です。

`conversation.non_participants`に非登場人物を保存します。人物関係のキーと参加者名の集合差分で非参加者を補完し、自由記述に登場する人物はCoordinatorにも抽出させます。構造不正、参加者の非参加者扱い、生成打ち切り、通信エラーなどは失敗として保存します。

## 複数ターンと閲覧範囲

各`conversation.turns[]`に以下を保存します。

- `character`: `name/speech/thought/narration/expression`の5必須文字列。nameはPythonが付与し、残り4項目は空文字列も許可します。
- ターン直下の`narration`: Coordinatorが確定した地の文。`character.narration`には原文が残ります。
- `current_situation/progress/progress_reason/speaker_recommendation`: 毎ターンのCoordinator出力。progressは`Approaching/Unchanged/Diverging/Achieved`です。

キャラクターへは清書後の開始状況、最新状況、開始ナレーションと全履歴を時系列順に渡します。他者のthoughtを除外し、自分のthoughtだけを保持します。過去の地の文はCoordinator確定版を使用します。自分以外のpersona、goal、progress、progress_reason、次話者推薦は渡しません。明示的な許可リストで入力を構築し、セッション全体を送ることはありません。

Coordinatorには専用のgoalと全履歴・全人物設定を渡し、地の文と状況を軽く調整させます。初期化・更新プロンプトでgoalをキャラクターの意図や既成事実として混入させないよう指示しています。自由文の意味的な漏洩を完全に検出する機能はありません。

初回は先頭人物、その後は有効な推薦名かつconfidenceが閾値以上なら推薦を採用します。それ以外は確定済みターン数が最少の人物、同数ならcharacters配列で先の人物を選びます。confidenceは0〜1の有限数、progressはenumをPythonでも検証します。API Schemaは推薦名を参加者enumで制約しますが、不正名・欠落が返った場合もPythonは発言回数へフォールバックします。欠落名は空文字列、欠落confidenceは0に補完します。範囲外confidenceなどの構造不正はFailedです。

goalがある場合、Coordinatorが`Achieved`を返したターンでPythonが`goal_achieved`として停止し、次話者の選択・生成を行いません。ターン上限では`max_turns_reached`となります。

```powershell
python -m chatwell --max-turns 6 --recommendation-threshold 0.7
# goalなしで4ターンを観察
python -m chatwell --config examples/observation.json --max-turns 4
```

最大ターン数の既定12、推薦閾値の既定0.7は暫定値です。`chatwell/speakers.py`の定数とCLI引数で変更でき、入力configの構造は増やしていません。実行条件の再現にはCLI引数も記録してください。全履歴を再入力するため、長い会話では起動時の`-ContextSize`を増やす必要があります。今回は要約・圧縮を行いません。

## 途中結果と互換性

キャラクターの応答を受け取った直後に`conversation.pending_turn`へ保存し、Coordinator応答を受け取ると同じ項目に`coordinator`を保存します。検証・確定後に`turns`へ移してpendingを除去します。失敗・中断時はpendingが残り、確定済みターンと区別できます。初期化にも一時的な`pending_initialization`を使います。pendingは実装上の暫定拡張で、通常の完了セッションには残りません。JSONとして解析できなかった応答は`--debug-dir`の生応答を確認してください。

旧セッションファイルは変更・移行しません。新規出力では`turn.character.name`、旧出力では`turn.name`という構造差があります。再開・旧履歴のインポート機能はありません。旧仕様と新仕様の混在はファイルを読む側で区別してください。

## 構成

| ファイル | 役割 |
| --- | --- |
| `chatwell/client.py` | ローカルAPI通信、生応答保存、明示的mock |
| `chatwell/prompts.py`、`chatwell/prompts/*.txt` | Schemaと自然言語プロンプト |
| `chatwell/validation.py` | 入力・調整者出力・ターンの構造検証 |
| `chatwell/storage.py` | JSON読み込みとatomic replace |
| `chatwell/history.py` | キャラクターごとの閲覧可能な履歴・入力の構築 |
| `chatwell/speakers.py` | 推薦と発言回数による次話者選択、暫定値 |
| `chatwell/session.py` | 状態遷移、複数ターン、Coordinator更新、終了判定 |
| `chatwell/__main__.py` | CLI |
| `tests/test_chatwell.py` | 保存、構造、通信、失敗処理の検証 |
| `tests/test_multiturn.py` | 履歴の情報制限、話者選択、複数ターン、途中保存 |

## 環境・llama.cppの導入

2026-10-22の確認結果:

| 項目 | 確認結果 |
| --- | --- |
| OS | Windows build 26200、x64（Pythonのplatform表記はWindows-10） |
| CPU | Intel Core i5-1135G7 @ 2.40GHz |
| RAM | 約15.67GiB、調査時の空き約2.10GiB |
| GPU | Intel Iris Xe Graphics。専用VRAM容量は取得できず |
| Python | 3.10.11 |
| CMake | 4.4.3 |
| 既存llama.cpp | PATH、Downloads、workspace、Scoopの検索範囲では実行ファイルなし |
| 導入したllama.cpp | b11342 / f1cee9941、version 0.5.0-dev、Clang 20.1.8 Windows x86_64 |

CIMによるハードウェア取得はアクセス拒否のため、レジストリとWindows APIから代替取得しました。環境全体の変更を避け、公式リリースのCPU版を`.runtime/b11342`へ展開しています。インストールスクリプトのSHA256は既存Scoop manifestの値を使用しています。

```text
取得元: https://github.com/ggml-org/llama.cpp/releases/download/b11342/llama-b11342-bin-win-cpu-x64.zip
SHA256: cc6f3ac938988f6ed4ce204b119d6d1ffe59342cb3157adef20e8132681acd8d
```

ソースからビルドする場合の代替手順（今回は未実施、Visual Studio C++ビルド環境が必要）:

```powershell
git clone --branch b11342 --depth 1 https://github.com/ggml-org/llama.cpp .runtime/llama.cpp
cmake -S .runtime/llama.cpp -B .runtime/llama-build -DGGML_CUDA=OFF -DLLAMA_CURL=OFF
cmake --build .runtime/llama-build --config Release --target llama-server -j 4
```

モデル埋め込みのネイティブJinjaテンプレートを使い、APIには`system/user`のmessagesを送ります。`--reasoning off`を指定し、初回はコンテキスト4096、CPUのみ、4スレッド、1スロットとしました。構造化出力は`response_format.type=json_schema`、`json_schema.schema`形式で実際に動作確認済みです。仕様参照: [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)、[Gemma公式llama.cppガイド](https://gemma.ai/deployment/llamacpp/)。

## 指定モデル

GGUFメタデータを実ファイルから確認しました。

| 項目 | 結果 |
| --- | --- |
| ファイル | `gemma-4-E2B-it-Q4_K_M.gguf`、3,106,738,272 bytes（約2.89GiB） |
| モデル | Gemma-4-E2B-It、architecture `gemma4`、size_label `4.6B` |
| 量子化 | Q4_K_M、GGUF v3、general.file_type=15 |
| 量子化者 | Unsloth |
| メタデータの取得元情報 | https://huggingface.co/unsloth 、基底モデル https://huggingface.co/google/gemma-4-E2B-it |
| ライセンス情報 | メタデータは`apache-2.0`、リンク https://ai.google.dev/gemma/docs/gemma_4_license |
| 公称コンテキスト | 131072、今回使用4096 |

この既存GGUFの正確なダウンロード元URL・取得履歴までは確認できません。RAMは重み約2.89GiBにKVキャッシュ・作業バッファ等が加わるため数GiB以上が必要です。VRAM使用の要求はありません。実行後のサーバーworking setは約2.68GiB（ページング等で変動、必要メモリの上限ではありません）。26Bはユーザー指定に従い対象外とし、取得も実行もしていません。

## 制限と暫定判断

- `ending_narration`は暫定的に空文字です。終了専用LLM呼び出しは追加せず、終了時点は最後の確定narrationとcurrent_situationで確認します。失敗時も空文字のままです。
- goal未指定時はCoordinatorへ`Unchanged`を指示し、PythonはAchievedが返っても停止条件に採用せず、最大ターン数まで実行します。恒久的な自然終了仕様ではありません。
- 記憶・要約、長時間の最適化、Web UI、GitHub push・公開は対象外です。
- mockは日本語の固定応答です。任意のキャラクターや言語の生成品質を検証する機能ではありません。
- JSON構造の検証と人物・言語への忠実さは別です。言語や他者の台詞の混入を機械的に完全検出する機能はありません。実モデルの今回の出力は目視確認しました。
- サーバーのモデル読込失敗はCLI接続失敗として扱われます。CLI自体はサーバーを自動起動しません。
- 複数ターンの結果は[検証報告](docs/validation/multiturn.md)、初回の記録は[初回検証報告](docs/validation/initial.md)を参照してください。
- セッション、生応答、実行バイナリは`.gitignore`対象です。GitHubへの登録は明示的な操作で行い、自動公開・同期機能はありません。

## 初回リリースとテスト成果物

mainにはコード・自動テスト・検証報告を保存します。実行時のセッションJSON、生API応答、サーバーログはGit管理せず、Release添付用ZIPとして保存します。旧報告書名`TEST_REPORT.md`と`TEST_REPORT_NEXT_STEPS.md`はそれぞれ`docs/validation/initial.md`と`docs/validation/multiturn.md`へ移動しました。

初回リリースの内容と手順は[v0.1.0](docs/releases/v0.1.0.md)を参照してください。ZIPには対応するソースコミット、実行条件、各ファイルのSHA256、パスの匿名化方針を記したmanifestを含みます。
