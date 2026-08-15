# RAGNA Bot 設計ガイド

この資料は、新しく参加したメンバーが「どこを直せばよいか」を短時間で判断するための案内です。

## 起動から稼働まで

1. `bot.py` がログ、Discord Intent、Bot本体を準備します。
2. `setup_hook()` が `database.py` の初期化を実行します。
3. `cogs/` の各機能を読み込み、対象サーバーへSlash Commandを同期します。
4. 各CogがDiscordイベント、コマンド、常設パネル、定期処理を担当します。
5. 終了時は未保存のVC時間とXPをDBへ書き込んでから切断します。

## ファイルの責務

| ファイル | 担当 |
| --- | --- |
| `bot.py` | 起動、Cog読込、コマンド同期、安全な終了 |
| `config.py` | 環境変数、Discordのロール・チャンネルID、料金などの設定 |
| `database.py` | SQLite接続、テーブル操作、複数更新のトランザクション |
| `schema.sql` | 新規DBで作成するテーブル定義 |
| `utils.py` | 時間表示や常設パネル確認など、業務ルールを持たない共通処理 |
| `cogs/*.py` | Discord上の個別機能 |
| `railway.json` | Railwayでの起動・再起動設定 |

## Cog一覧

| Cog | 主な役割 |
| --- | --- |
| `coin.py` | 残高、送金、ATM、月次報酬、負債ロール |
| `trial_member.py` | 精霊の召喚、評価、追記、転生、評価スレッド |
| `trial_member_views.py` | 精霊評価と転生アンケートのボタン・入力画面 |
| `xp.py` | VC滞在時間とXPの計測、5分ごとの保存、月次リセット |
| `member.py` | 参加・退出、メンバー確認、招待ポイント、BAN |
| `hotel.py` | 有料VCの作成、管理、自動削除 |
| `ranking.py` | coin、招待、VC時間、XP、アンケートのランキング |
| `class_change.py` | クラス候補、分布、クラス変更 |
| `ticket.py` | 問い合わせの作成、担当、終了、再開、保存、削除 |
| `introduction.py` | 自己紹介テンプレートの自動設置 |

## 変更時のルール

- Tokenなどの秘密情報はコードへ書かず、環境変数へ入れます。
- SQLはCogへ直接書かず、`database.py` に関数を追加します。
- 残高など複数の更新が一組になる処理は、必ず1つのDBトランザクションにします。
- 複数Cogで同じDiscord処理が必要な場合は、`utils.py` へ共通化します。
- 新しい常設ボタンには固定の `custom_id` を付け、Cog読込時にViewを再登録します。
- 新しいSlash Commandは対象Guildを明示し、必要な権限を設定します。
- 変更後は `python scripts/check_project.py` と `python -m unittest discover -s tests` を実行します。

## データの扱い

本番DBはRailway Volumeの `/data/ragna.db` です。SQLiteは1つのBotプロセスから利用する前提なので、RailwayのReplicaは必ず1にします。既存DBを移す場合は `/data/ragna.db.import` としてアップロードし、起動時の整合性確認とバックアップを経由して取り込みます。
