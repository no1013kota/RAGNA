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
# ``ensure_panel_message`` の結果。呼び出し側が並び順を直したいときに使う。
PANEL_MISSING = "missing"  # 設置できなかった
PANEL_FOUND = "found"  # 既に設置済みだった
PANEL_UPDATED = "updated"  # 表題が変わったので書き換えた
PANEL_CREATED = "created"  # 新しく設置した


def panel_custom_ids(message: discord.Message) -> set[str]:
    """メッセージに付いている操作部品の ``custom_id`` を集める。"""

    found: set[str] = set()

    for row in message.components:
        for child in getattr(row, "children", ()) or ():
            custom_id = getattr(child, "custom_id", None)
            if custom_id:
                found.add(str(custom_id))

        custom_id = getattr(row, "custom_id", None)
        if custom_id:
            found.add(str(custom_id))

    return found


async def remove_legacy_panels(
    bot: discord.Client,
    guild: discord.Guild,
    channel_id: int,
    *,
    titles: tuple[str, ...],
    history_limit: int = 30,
) -> int:
    """表題が変わって不要になった常設パネルを片づける。

    削除するのは「Bot自身が送った・操作部品付き・表題が ``titles`` と完全一致」の
    メッセージだけです。利用者の投稿や現行パネルには触れません。パネルの表題を
    変更したとき、古いパネルが残って二重に見えるのを防ぐために使います。
    """

    if not titles:
        return 0

    channel = guild.get_channel(channel_id)
    if channel is None or not hasattr(channel, "history"):
        return 0

    if bot.user is None:
        return 0

    removed = 0

    try:
        async for message in channel.history(limit=history_limit):
            is_legacy_panel = (
                message.author.id == bot.user.id
                and bool(message.embeds)
                and bool(message.components)
                and message.embeds[0].title in titles
            )
            if not is_legacy_panel:
                continue

            try:
                await message.delete()
            except discord.HTTPException:
                logger.warning(
                    "旧パネルの削除に失敗しました: message_id=%s", message.id
                )
                continue

            removed += 1
            logger.info(
                "旧パネル「%s」を削除しました: channel_id=%s",
                message.embeds[0].title,
                channel_id,
            )
    except discord.HTTPException:
        logger.exception("旧パネルの確認に失敗しました: channel_id=%s", channel_id)

    return removed


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
    match_custom_ids: tuple[str, ...] = (),
) -> str:
    """常設パネルが見つからない場合だけ、新しいメッセージを送信する。

    Bot再起動のたびに同じパネルが増えないよう、指定チャンネルの直近履歴から
    「Bot自身が送った・同じタイトル・操作部品付き」のメッセージを探します。

    ``match_custom_ids`` を渡すと、表題ではなくボタンの ``custom_id`` で見分けます。
    表題がギルド名のように変わりうるパネルはこちらを使い、見つかった既存パネルの
    表題が古い場合はその場で書き換えます（改名しても増えません）。

    戻り値は ``PANEL_MISSING`` ``PANEL_FOUND`` ``PANEL_UPDATED`` ``PANEL_CREATED``
    のいずれかです。
    """

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)
        except discord.NotFound:
            logger.warning("%sチャンネルが見つかりません: %s", panel_name, channel_id)
            return PANEL_MISSING
        except discord.HTTPException:
            logger.exception("%sチャンネルの取得に失敗しました: %s", panel_name, channel_id)
            return PANEL_MISSING

    # 設定ミスでカテゴリや音声チャンネルが指定された場合も、タスクを停止させない。
    if not hasattr(channel, "history") or not hasattr(channel, "send"):
        logger.error("%sの送信先がテキストチャンネルではありません: %s", panel_name, channel_id)
        return PANEL_MISSING

    if bot.user is None:
        logger.warning("Botユーザー確定前のため%sの確認を延期します", panel_name)
        return PANEL_MISSING

    wanted_ids = set(match_custom_ids)

    try:
        async for message in channel.history(limit=history_limit):
            if message.author.id != bot.user.id:
                continue
            if not message.embeds or not message.components:
                continue

            if wanted_ids:
                if not wanted_ids & panel_custom_ids(message):
                    continue
            elif message.embeds[0].title != panel_title:
                continue

            if message.embeds[0].title == panel_title:
                return PANEL_FOUND

            # 表題が変わった（例：ギルド名の変更）ので、その場で書き換える
            try:
                await message.edit(embed=embed, view=view)
            except discord.HTTPException:
                logger.exception(
                    "%sの書き換えに失敗しました: message_id=%s", panel_name, message.id
                )
                return PANEL_FOUND

            logger.info(
                "%sの表題を「%s」へ書き換えました: channel_id=%s",
                panel_name,
                panel_title,
                channel_id,
            )
            return PANEL_UPDATED
    except discord.HTTPException:
        logger.exception("%sの履歴取得に失敗しました: %s", panel_name, channel_id)
        return PANEL_MISSING

    try:
        await channel.send(embed=embed, view=view)
        logger.info("%sを設置しました: channel_id=%s", panel_name, channel_id)
        return PANEL_CREATED
    except discord.HTTPException:
        logger.exception("%sの送信に失敗しました: %s", panel_name, channel_id)
        return PANEL_MISSING
