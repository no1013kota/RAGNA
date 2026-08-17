# RAGNA Discord Bot

Discord Gatewayへ常時接続して動作する、RAGNAサーバー専用Botです。
RailwayではWebサービスではなく、常駐Workerとして起動します。

- [機能一覧](docs/FEATURES.md)
- [設計・開発ガイド](docs/ARCHITECTURE.md)
- [Discord Intent・使用情報一覧](docs/DISCORD_SETTINGS.md)
- [RAGNA Online ギルド・ギルドバトル仕様](docs/GAME_SPEC.md)
- [RAGNA Online 戦闘ルール・使い魔データ](docs/BATTLE_RULES.md)

## 必要な設定

- Python 3.12
- Discord Bot Token
- DiscordのServer Members IntentとMessage Content Intent
- Railway Volume（SQLite永続化用）

## ローカル実行

~~~bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
~~~

`.env` の `DISCORD_BOT_TOKEN` と `DISCORD_GUILD_ID` を設定してから起動します。

~~~bash
python bot.py
~~~

.envとSQLiteファイルはGitの対象外です。

## デプロイ前チェック

外部ツールを追加インストールせずに、構文・Railway設定・DB整合性を確認できます。

~~~bash
python scripts/check_project.py
python -m unittest discover -s tests
~~~

## Railway設定

1. GitHubのPrivateリポジトリへ、このフォルダのコードをpushします。
2. RailwayでDeploy from GitHub repoを選びます。
3. ServiceのVariablesに次を追加します。

   - DISCORD_BOT_TOKEN: Discordで再発行したToken
   - DISCORD_GUILD_ID: 対象サーバーID
   - LOG_LEVEL: INFO

4. ServiceへVolumeを追加し、Mount Pathを/dataにします。
5. デプロイします。railway.jsonによりpython -u bot.pyで起動します。

RAGNA Onlineを有効にする場合は、次のVariablesも追加します（[公開手順](#ragna-onlineの公開手順)を参照）。

- GAME_ENABLED: true
- GUILD_INTRO_CHANNEL_ID / GUILD_MEMBER_RECRUITMENT_CHANNEL_ID /
  FAMILIAR_PANEL_CHANNEL_ID / GUILD_BATTLE_RECRUITMENT_CHANNEL_ID

Volumeが接続されている場合、DBは自動的に/data/ragna.dbへ保存されます。
Volumeがないローカル環境ではdata/ragna.dbが使用されます。

## 既存DBをRailwayへ移す場合のみ

新規運用で引き継ぐDBがない場合、この手順は不要です。Railway Volumeの
`/data/ragna.db` が初回起動時に自動作成されます。PC側の `data/ragna.db` は
Gitの対象外なので、そのままローカルのバックアップとして残して構いません。

MacではRailway CLIをインストールしてログインします。

~~~bash
brew install railway
railway login
~~~

Botフォルダで対象ProjectとServiceを選択します。

~~~bash
railway link
railway service link
~~~

現在PCなどで動いているBotを停止してから、DBを一時ファイルとしてアップロードします。

~~~bash
railway service files upload data/ragna.db /data/ragna.db.import --overwrite
railway service restart
~~~

Bot起動時に整合性を確認して/data/ragna.dbへ取り込み、直前のDBは
/data/ragna.db.before-importとして残します。

直接/data/ragna.dbへ上書きしないでください。稼働中のSQLiteを直接置換すると
データ破損につながる可能性があります。

## 運用

- Botは1インスタンスだけ起動してください。SQLite Volumeは複数Replica向けではありません。
- RailwayのRestart PolicyはAlwaysを使用します。
- RailwayのBackups画面でVolumeの定期バックアップを有効にしてください。
- Railway ServiceがGitHubの対象ブランチへ接続され、Autodeployが有効なら、
  コード更新をそのブランチへpushすると自動デプロイされます。
- Bot Tokenや.env、data/ragna.dbをGitHubへ追加しないでください。
- Railwayへ切り替えた後は、PC上の同じBotを同時起動しないでください。

## RAGNA Onlineの公開手順

ギルド・使い魔・ギルドバトルは実装済みですが、既定では停止しています。
`GAME_ENABLED` が未設定の間、Cogは読み込まれますがパネルを設置せず、
ボタン操作もすべて拒否します。段階的に公開できるようにするためです。

公開前に次を用意してください。詳細は
[ゲーム仕様書 32節](docs/GAME_SPEC.md#32-今後必要な準備物)を参照します。

1. Discordチャンネルを4つ作り、IDを環境変数へ設定します。

   - `GUILD_INTRO_CHANNEL_ID`：ギルド紹介（設立パネル・申請確認）
   - `GUILD_MEMBER_RECRUITMENT_CHANNEL_ID`：メンバー募集（募集Embed・参加申請）
   - `FAMILIAR_PANEL_CHANNEL_ID`：ガチャパネルと使い魔管理パネル
   - `GUILD_BATTLE_RECRUITMENT_CHANNEL_ID`：公開バトル募集とランキング

   `GAME_ADMIN_LOG_CHANNEL_ID`（運営ログの転送先）は任意です。未設定でも
   運営操作は `game_admin_logs` テーブルへ必ず記録します。

2. Botにチャンネル管理権限を与え、Botロールを管理対象ロールより上へ置きます。
   ギルド設立のたびに、専用カテゴリーと4つの常設チャンネルを自動作成します。
   バトル用チャンネルは対戦成立時に自動生成し、終了から既定7日で自動削除します。

3. 任意で、マスターデータを補完します（未登録でも公開できます）。

   - Cランク使い魔（`data/master/familiars.json`）
     未登録の間、Cランク分の排出率はBランクへ加算します。
   - 使い魔画像（`assets/familiars/<使い魔ID>.png`）
     未登録の個体は共通の仮画像 `default.png` を表示します。
   - 性別は神話・伝承に基づく暫定値を登録済みです。変更する場合は
     `data/master/familiars.json` と `docs/BATTLE_RULES.md` を同時に更新してください。

4. `GAME_ENABLED=true` を設定してデプロイします。5分以内に各パネルが設置されます。

数値バランスは `data/master/*.json` で管理します。変更するときは
`docs/GAME_SPEC.md` の該当値と `tests/test_game_master_data.py` の期待値も
同じ変更として更新してください。

## Discord側で必要な権限

Developer PortalのBot設定でServer Members IntentとMessage Content Intentを有効にしてください。
このBotは参加・退出、ロール、評価対象者一覧に加え、評価ログやスレッド本文の保存を
扱うため、Member情報とメッセージ本文へのアクセスが必要です。

Botには、使用するチャンネルで次の権限が必要です。

- チャンネルの閲覧・メッセージ送信・履歴閲覧
- ロールの管理
- チャンネルの管理
- メンバーの移動
- メンバーのBAN
- スレッド・フォーラムの作成と管理

Botのロールは、Botが付与・削除するすべてのロールより上に配置してください。
