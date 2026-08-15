"""Discord接続時に有効化する情報と送信ルール。

Intentは「現在のコードが実際に使っているイベント・情報」だけを明示的に
有効化します。機能を追加するときは、該当するIntentとDeveloper Portal側の
設定が必要かを確認し、このファイルと ``docs/DISCORD_SETTINGS.md`` を更新してください。
"""

import discord


REQUIRED_INTENT_NAMES = (
    "guilds",
    "members",
    "guild_messages",
    "message_content",
    "voice_states",
)


def create_intents() -> discord.Intents:
    """RAGNA Botが現在使用しているIntentだけを返す。"""

    # default()を使うと、未使用Intentがライブラリ更新で増減しても気づきにくい。
    # none()から必要なものだけONにし、コードとDiscord側設定の対応を明確にする。
    intents = discord.Intents.none()

    intents.guilds = True
    # 参加・退出・ロール変更と、ロール所属メンバーの取得に必要（特権Intent）。
    intents.members = True
    # 自己紹介チャンネルへの投稿をon_messageで検知するために必要。
    intents.guild_messages = True
    # 評価スレッドや脱退ログへ、ユーザー投稿本文を保存するために必要（特権Intent）。
    intents.message_content = True
    # VC滞在時間とXPをon_voice_state_updateで計測するために必要。
    intents.voice_states = True

    # Presenceはステータス・アクティビティを参照していないためOFFのままにする。
    return intents


def create_allowed_mentions() -> discord.AllowedMentions:
    """Botのメッセージが意図せず@everyoneを通知しないよう制限する。"""

    return discord.AllowedMentions(
        everyone=False,
        roles=True,
        users=True,
        replied_user=False,
    )
