# Discord接続設定

この資料は「どの情報をBotが使い、なぜIntentが必要なのか」を判断するための一覧です。
実装は `discord_settings.py` に集約し、テストで意図しないON/OFFを検出します。

## ONにするIntent

| Intent | 用途 | Developer Portal |
| --- | --- | --- |
| Guilds | サーバー、ロール、チャンネル、Slash Commandの利用 | 追加操作なし |
| Server Members | 参加・退出・ロール変更、ロール所属メンバーの取得、ゲーム内ランクの同期 | **ONが必要** |
| Guild Messages | 自己紹介チャンネルへの投稿検知 | 追加操作なし |
| Message Content | 評価スレッド・脱退アーカイブへ投稿本文を保存 | **ONが必要** |
| Voice States | VC滞在時間とXPの計測 | 追加操作なし |

## OFFのままにするIntent

- Presence: オンライン状態、ゲーム、アクティビティを参照する機能がないため不要です。
- DM Messages: BotからDMを**送る**だけなら不要です。DM受信イベントは使っていません。
- Reactions / Typing / Polls: 対応するイベントを使っていません。
- Moderation: BAN操作はDiscord APIへの命令であり、BANイベントの購読はしていません。

Presence IntentとVoice States Intentは別物です。PresenceをOFFにしても、VC入退室の計測は動きます。

## 音声ライブラリについて

このBotはVCへの接続・音声再生・録音をせず、VC入退室イベントだけを受信します。
そのためPyNaClとdaveyは現在不要です。将来、Bot自身をVCへ接続させる場合に追加します。

## RAGNA Onlineで追加したIntentはありません

ギルド・使い魔・ギルドバトルは、既にONにしている Guilds と Server Members だけで動きます。
ゲーム内ランクの同期に `on_member_join` と `on_member_update` を使いますが、
これらは Server Members Intent に含まれます。

一方でDiscordの**権限**は追加が必要です。ギルド設立時に専用カテゴリーと
4つの常設チャンネルを作成し、対戦成立のたびにバトル専用チャンネルを追加するため、
Botに「チャンネルの管理」権限と、対象カテゴリーへの権限が必要です。
