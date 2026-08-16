# RAGNA Bot 設計・開発ガイド

この資料は、新しく参加したエンジニアが「どこを直せばよいか」を短時間で判断するための案内です。
利用者から見える機能は [機能一覧](FEATURES.md) に分け、この資料ではコードの配置と責務を説明します。

## 起動から稼働まで

1. `bot.py` がログ、Discord Intent、Bot本体を準備します。
2. `setup_hook()` が `database.connection` の初期化を実行します。
3. `cogs/` の各機能を読み込み、対象サーバーへSlash Commandを同期します。
4. 各CogがDiscordイベント、コマンド、常設パネル、定期処理を担当します。
5. 終了時は未保存のVC時間とXPをDBへ書き込んでから切断します。

## フォルダ構成

```text
RAGNA/
├── bot.py                    # 起動、Cog読込、コマンド同期、安全な終了
├── config.py                 # 環境変数とDiscordサーバー固有の設定
├── discord_settings.py       # Intentとメンション送信ルール
├── schema.sql                # SQLiteのテーブル定義
├── cogs/
│   ├── coin/
│   │   ├── __init__.py       # discord.pyが読み込む公開入口
│   │   ├── cog.py            # コマンド、イベント、定期処理
│   │   └── views.py          # ボタン、選択画面、入力画面
│   ├── member/               # 同じ3層構成
│   ├── hotel/                # 同じ3層構成
│   ├── ticket/               # 同じ3層構成
│   ├── trial_member/         # 同じ3層構成
│   ├── guild/                # RAGNA Online：ギルド（cog/views/service）
│   ├── familiar/             # RAGNA Online：ガチャ／使い魔管理（一覧・合成・売却・図鑑）
│   ├── guild_battle/         # RAGNA Online：ギルドバトル
│   ├── game_shared.py        # ゲーム3Cogの共通Discord処理（Cogではない）
│   ├── xp.py                 # 比較的小さい単独Cog
│   ├── ranking.py
│   ├── class_change.py
│   └── introduction.py
├── game/                     # Discordに依存しない戦闘計算とマスターデータ
│   ├── models.py             # 使い魔・スキル・戦闘状態のデータ構造
│   ├── master_data.py        # data/master/ の読み込みと検証
│   ├── effects.py            # バフ・デバフ・状態異常の保持と集計
│   ├── skill_engine.py       # スキルの発動条件判定と効果適用
│   ├── battle_engine.py      # 行動順、ダメージ、勝敗
│   └── battle_embed.py       # 行動ログ・戦況Embed（唯一Discordを使う）
├── data/master/              # ゲームバランスとマスターデータ（JSON）
│   ├── balance.json          # 料金、成長式、戦闘定数、報酬
│   ├── familiars.json        # 使い魔40体（能力値・性別・スキル）
│   ├── skills.json           # アクティブ・パッシブ34件
│   └── gacha.json            # ガチャ料金と排出率
├── assets/familiars/         # 使い魔画像（<使い魔ID>.png と仮画像 default.png）
├── database/
│   ├── __init__.py           # 旧importとの互換窓口
│   ├── connection.py         # DB接続・初期化の公開窓口
│   ├── coin.py               # coin関連データの公開窓口
│   ├── member.py             # メンバー関連データの公開窓口
│   ├── trial_member.py       # 精霊関連データの公開窓口
│   ├── xp.py                 # VC時間・XPの公開窓口
│   ├── ranking.py            # ランキングの公開窓口
│   ├── hotel.py              # 宿屋データの公開窓口
│   ├── ticket.py             # 問い合わせデータの公開窓口
│   ├── guild.py              # ギルド・所属・参加申請
│   ├── familiar.py           # 使い魔マスター同期・所有・ガチャ・合成・売却
│   ├── battle.py             # 事前登録・出場者セット・対戦成立・戦闘状態・報酬
│   ├── player_rank.py        # Discordロールとゲーム内ランクの同期
│   └── core.py               # 既存SQL実装と共通トランザクション
├── scripts/
│   ├── check_project.py      # デプロイ前の構成・設定・DB確認
│   └── make_familiar_placeholder.py  # 使い魔の仮画像を生成
└── tests/                    # DBと重要な業務ルールの自動テスト
```

`cogs/game_shared.py` は3つのゲームCogの共通処理です。チャンネル権限、DM通知、
運営ログ、プレイヤーランク同期に加えて、メッセージの取得・削除・送信、日時変換、
利用者へ返す共通メッセージもここに集約しています。同じ処理を各Cogへ書かず、
必ずこのモジュール経由で使ってください。

`cogs.coin` などの読み込み名は変更していません。ファイルからパッケージへ変わっても
`__init__.py` が `setup()` を公開するため、`bot.py` とRailwayの起動方法は従来どおりです。

## 各層の責務

| 層 | 書く内容 | 書かない内容 |
| --- | --- | --- |
| `cog.py` | Slash Command、Discordイベント、定期処理、View登録 | SQLの直接実行 |
| `views.py` | ボタン、セレクト、ModalとDiscordへの表示 | SQLite接続の作成 |
| `service.py` | 複数処理をまとめる業務手順（設立、ガチャ抽選、バトル進行） | SQLの直接実行 |
| `database/<機能>.py` | 各機能が利用できるDB操作の公開範囲 | DiscordのInteraction処理 |
| `database/core.py` | 既存SQL実装、接続、トランザクション | Discordの画面・権限処理 |
| `game/*.py` | Discordに依存しない戦闘計算とマスターデータ | Discord APIとSQL |
| `data/master/*.json` | 料金・確率・能力値などのゲームバランス値 | 処理ロジック |
| `cogs/game_shared.py` | ゲーム3Cogが共通で使うDiscord処理 | 戦闘計算とSQL |
| `utils.py` | 複数機能で使う、業務ルールを持たない共通処理 | 特定機能だけの処理 |

現在の `database/<機能>.py` は、安全に互換性を保つため `core.py` の既存実装を再公開しています。
新しいコードは必ず機能別の窓口からimportしてください。これにより、将来SQL実装を一領域ずつ移しても
Cog側を変更せずに済みます。

## 機能とコードの対応表

| 利用者向け機能 | 主な実装 | 主なデータ窓口 |
| --- | --- | --- |
| ATM、残高変更、月次報酬、負債ロール | `cogs/coin/` | `database.coin` |
| メンバー確認、招待ポイント、参加・退出、BAN | `cogs/member/` | `database.member`、`database.coin` |
| 精霊の召喚、評価、追記、転生 | `cogs/trial_member/` | `database.trial_member` |
| VC滞在時間とXP | `cogs/xp.py` | `database.xp` |
| 宿屋の作成・管理・自動削除 | `cogs/hotel/` | `database.hotel` |
| 各種ランキング | `cogs/ranking.py` | `database.ranking` |
| クラス候補・分布・変更 | `cogs/class_change.py` | `database.trial_member` |
| 問い合わせの作成・管理 | `cogs/ticket/` | `database.ticket` |
| 自己紹介テンプレート | `cogs/introduction.py` | なし |
| ギルド設立・募集・参加・管理 | `cogs/guild/` | `database.guild`、`database.player_rank` |
| ガチャ・排出確認・一覧・合成・売却・図鑑 | `cogs/familiar/` | `database.familiar` |
| 事前登録・出場者セット・対戦・バトル進行 | `cogs/guild_battle/` | `database.battle`、`game/` |

## RAGNA Onlineの構成

ゲーム機能は「Discord表示」「業務手順」「戦闘計算」「データ」を分けています。

```text
cogs/guild_battle/views.py   ボタン操作を受け取る
        ↓
cogs/guild_battle/service.py 状態を読み、エンジンへ渡し、保存して投稿する
        ↓                     ↑
game/battle_engine.py        Discordを知らない戦闘計算
        ↓
database/battle.py           状態の保存と楽観ロック
```

バトルで使う使い魔は、次の2段階で決まります。

```text
player_battle_familiars      本人が優先順を付けて事前登録（ギルド・進行状況と無関係）
        ↓ メンバーセット時に自動採用
guild_battle_members         マスターが出場者と「1人あたりの体数」を割り当て
        ↓
guild_battle_entries         実際に出場する使い魔（本人が枠内で差し替え可能）
```

ギルドのDiscordチャンネルは次のように分かれます。

- 設立時に作る常設4チャンネル（ギルドTC・VC、マスター専用TC、出場者専用TC）
- 対戦成立時に作るバトル専用チャンネル `バトル-<バトルID>`
  （`guild_battles.guild_a_channel_id` / `guild_b_channel_id` に保存し、
  終了から `battle_channel_retention_days` 日後に自動削除）

- 戦闘計算は `game/` だけで完結するため、Discordを使わずに自動テストできます
  （`tests/test_game_battle.py` が5体どうしの通し確認を行います）。
- 進行中バトルの二重処理は `guild_battles.action_seq` の楽観ロックで防ぎます。
  `database.battle.save_battle_state(state, expected_action_seq=...)` が `False` を
  返した場合、Cogは**何も投稿せず**に処理を中断します。
- 料金・確率・能力値は `data/master/` のJSONが唯一の定義です。値を変更するときは
  `docs/GAME_SPEC.md` と `tests/test_game_master_data.py` の期待値も同じ変更として更新します。
- Embedでは `field` を使いません。項目は本文へ「【項目】結果」の形で並べます
  （`cogs.game_shared.item_line`、`game.battle_embed.item_line`）。表示幅が端末によって
  変わるうえ、項目が横に並ぶと読み順が崩れるためです。

## 変更時のルール

- Tokenなどの秘密情報はコードへ書かず、環境変数へ入れます。
- SQLをCogやViewへ直接書かず、該当する `database/<機能>.py` の公開APIを使います。
- 残高など複数の更新が一組になる処理は、必ず1つのDBトランザクションにします。
- Discord画面部品は `views.py`、コマンドとイベントは `cog.py` に置きます。
- 複数機能で同じDiscord処理が必要な場合だけ、`utils.py` へ共通化します。
- 1機能だけで使う複雑な計算・判定が増えた場合は、その機能内に `service.py` を追加します。
- 新しい常設ボタンには固定の `custom_id` を付け、Cog読込時にViewを再登録します。
- 新しいSlash Commandは対象Guildを明示し、必要な権限を設定します。
- 公開済みの利用者向け機能は `docs/FEATURES.md` へ記載します。未公開機能は各仕様書へ「公開予定」と明記し、本番公開後に `FEATURES.md` へ移します。配置変更はこの資料も更新します。
- 変更後は `python scripts/check_project.py` と `python -m unittest discover -s tests` を実行します。

## 大きなファイルをさらに分ける基準

今回は挙動を変えないことを優先し、CogとViewの境界、およびDBの機能別窓口を整えました。
今後、1ファイルが大きくなったときは行数だけで機械的に分割せず、次の境界で一領域ずつ移します。

- Discordに依存しない計算・料金判定・状態遷移 → `service.py`
- 特定の画面群だけで完結するUI → `views/<用途>.py`
- SQLとトランザクション → `database/<機能>.py`

移動と仕様変更を同時に行うとレビューが難しくなるため、まずテストを追加し、移動だけの変更と
動作変更を別のコミットにします。

## データの扱い

本番DBはRailway Volumeの `/data/ragna.db` です。SQLiteは1つのBotプロセスから利用する前提なので、
RailwayのReplicaは必ず1にします。既存DBを移す場合は `/data/ragna.db.import` としてアップロードし、
起動時の整合性確認とバックアップを経由して取り込みます。

`schema.sql` は起動のたびに `CREATE TABLE IF NOT EXISTS` として適用されます。
新しいテーブルもRailway上の既存DBへ自動追加されるため、通常は手動のDB移行作業は不要です。

既存テーブルへ**列**を追加する場合は `CREATE TABLE IF NOT EXISTS` では反映されません。
`database/core.py` の `ADDED_COLUMNS` へ `(テーブル名, 列名, 型)` を追記してください。
起動時に不足している列だけを `ALTER TABLE ... ADD COLUMN` で補います。
