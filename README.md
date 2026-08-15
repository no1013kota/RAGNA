# RAGNA Discord Bot

Discord Gatewayへ常時接続して動作する、RAGNAサーバー専用Botです。
RailwayではWebサービスではなく、常駐Workerとして起動します。

- [機能一覧](docs/FEATURES.md)
- [設計・開発ガイド](docs/ARCHITECTURE.md)
- [Discord Intent・使用情報一覧](docs/DISCORD_SETTINGS.md)
- [RAGNA Online ギルド・ギルドバトル仕様](docs/GAME_SPEC.md)
- [RAGNA Online 使い魔マスターデータ](docs/FAMILIAR_MASTER.md)

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
