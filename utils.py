"""Discord表示まわりで複数のCogから利用する共通処理。

このファイルには、業務ルールを持たない小さな処理だけを置きます。
Bot固有の設定値やデータベース操作は、それぞれ ``config.py`` と
``database.py`` に集約してください。
"""

import logging

import discord


logger = logging.getLogger(__name__)


# ==================================================
# 通話時間表示
# ==================================================
def format_time(minutes: int) -> str:
    """分単位の値を「○時間○分」の読みやすい形式に変換する。"""

    hours = minutes // 60
    remaining_minutes = minutes % 60

    if hours == 0:
        return f"{remaining_minutes}分"

    if remaining_minutes == 0:
        return f"{hours}時間"

    return f"{hours}時間{remaining_minutes}分"


# ==================================================
# 常設パネルの自動復旧
# ==================================================
async def ensure_panel_message(
    bot: discord.Client,
    guild: discord.Guild,
    channel_id: int,
    *,
    panel_title: str,
    embed: discord.Embed,
    view: discord.ui.View,
    panel_name: str,
    history_limit: int = 30,
) -> bool:
    """常設パネルが見つからない場合だけ、新しいメッセージを送信する。

    Bot再起動のたびに同じパネルが増えないよう、指定チャンネルの直近履歴から
    「Bot自身が送った・同じタイトル・操作部品付き」のメッセージを探します。
    戻り値は、既存パネルを確認できたか新規送信に成功した場合に ``True`` です。
    """

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.NotFound:
            logger.warning("%sチャンネルが見つかりません: %s", panel_name, channel_id)
            return False
        except discord.HTTPException:
            logger.exception("%sチャンネルの取得に失敗しました: %s", panel_name, channel_id)
            return False

    # 設定ミスでカテゴリや音声チャンネルが指定された場合も、タスクを停止させない。
    if not hasattr(channel, "history") or not hasattr(channel, "send"):
        logger.error("%sの送信先がテキストチャンネルではありません: %s", panel_name, channel_id)
        return False

    if bot.user is None:
        logger.warning("Botユーザー確定前のため%sの確認を延期します", panel_name)
        return False

    try:
        async for message in channel.history(limit=history_limit):
            is_our_panel = (
                message.author.id == bot.user.id
                and bool(message.embeds)
                and bool(message.components)
                and message.embeds[0].title == panel_title
            )
            if is_our_panel:
                return True
    except discord.HTTPException:
        logger.exception("%sの履歴取得に失敗しました: %s", panel_name, channel_id)
        return False

    try:
        await channel.send(embed=embed, view=view)
        logger.info("%sを設置しました: channel_id=%s", panel_name, channel_id)
        return True
    except discord.HTTPException:
        logger.exception("%sの送信に失敗しました: %s", panel_name, channel_id)
        return False
