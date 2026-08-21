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


def view_custom_ids(view: discord.ui.View) -> set[str]:
    """Viewが持つ操作部品の ``custom_id`` を集める。"""

    return {
        str(item.custom_id)
        for item in view.children
        if getattr(item, "custom_id", None)
    }


async def remove_legacy_panels(
    bot: discord.Client,
    guild: discord.Guild,
    channel_id: int,
    *,
    titles: tuple[str, ...] = (),
    custom_ids: tuple[str, ...] = (),
    history_limit: int = 30,
) -> int:
    """置き場や表題が変わって不要になった常設パネルを片づける。

    削除するのは「Bot自身が送った・操作部品付き」で、表題が ``titles`` と完全一致
    するか、``custom_ids`` のボタンを持つメッセージだけです。利用者の投稿には
    触れません。

    表題がギルド名のように変わるパネルは ``titles`` では見つけられないため、
    ボタンの ``custom_id`` で探せるようにしています。``custom_ids`` を渡すときは、
    現行パネルのあるチャンネルを対象にしないでください（消してしまいます）。
    """

    if not titles and not custom_ids:
        return 0

    channel = guild.get_channel(channel_id)
    if channel is None or not hasattr(channel, "history"):
        return 0

    if bot.user is None:
        return 0

    removed = 0

    try:
        async for message in channel.history(limit=history_limit):
            if (
                message.author.id != bot.user.id
                or not message.embeds
                or not message.components
            ):
                continue

            matched = message.embeds[0].title in titles or bool(
                set(custom_ids) & panel_custom_ids(message)
            )
            if not matched:
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


async def ensure_top_panel(
    bot: discord.Client,
    guild: discord.Guild,
    channel_id: int,
    *,
    panel_title: str,
    embed: discord.Embed,
    view: discord.ui.View,
    panel_name: str,
    movable_titles: tuple[str, ...],
    history_limit: int = 100,
) -> str:
    """常設パネルを、そのチャンネルのBotパネルの中で一番上に置く。

    Discordはメッセージを並べ替えられないため、上に別のパネルがある場合は
    そちらを削除します。消したパネルは、それぞれの定期タスクが下へ貼り直します。
    一度並び順が直れば以降は何もしないため、貼り直しが続くことはありません。

    削除するのは ``movable_titles`` に挙げた表題のパネルだけです。同じチャンネルに
    並ぶ参加申請Embedやバトル申請EmbedもBotの投稿ですが、こちらは貼り直せないため
    絶対に消してはいけません。知らない投稿は動かさず、その下へ置きます。
    """

    channel = guild.get_channel(channel_id)

    if channel is not None and hasattr(channel, "history") and bot.user is not None:
        panels: list = []
        seen = 0

        try:
            async for message in channel.history(limit=history_limit):
                seen += 1
                if (
                    message.author.id == bot.user.id
                    and message.embeds
                    and message.components
                ):
                    panels.append(message)
        except discord.HTTPException:
            logger.exception("%sの並び順を確認できませんでした: %s", panel_name, channel_id)
            panels = []
            seen = history_limit

        # history は新しい順で届くため、見た目と同じ古い順へ直す
        panels.reverse()

        above: list = []
        found = False

        for message in panels:
            if message.embeds[0].title == panel_title:
                found = True
                break
            if message.embeds[0].title in movable_titles:
                above.append(message)

        # 自分のパネルが見つからず、履歴を最後まで見られてもいない場合は
        # 並べ替えない。窓の外にある可能性があり、消して貼り直すたびに
        # 同じことを繰り返してしまうため。
        if not found and seen >= history_limit:
            above = []

        if above:
            logger.info(
                "%sを一番上へ置くため、上にあるパネル%d件を貼り直します: channel_id=%s",
                panel_name,
                len(above),
                channel_id,
            )

        for message in above:
            try:
                await message.delete()
            except discord.HTTPException:
                logger.warning(
                    "並び替えのためのパネル削除に失敗しました: message_id=%s", message.id
                )

    return await ensure_panel_message(
        bot,
        guild,
        channel_id,
        panel_title=panel_title,
        embed=embed,
        view=view,
        panel_name=panel_name,
        history_limit=history_limit,
    )


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

            same_title = message.embeds[0].title == panel_title
            # Discordは説明文の前後の空白・改行を落として保存するため、
            # そのまま比べると毎回「変わった」と判定されて編集が止まらない
            same_body = (message.embeds[0].description or "").strip() == (
                embed.description or ""
            ).strip()
            same_buttons = panel_custom_ids(message) == view_custom_ids(view)

            if same_title and same_body and same_buttons:
                return PANEL_FOUND

            # 表題（例：ギルド名の変更）・説明文・ボタン構成のどれかが変わったので、
            # その場で書き換える。貼り直さずに編集するため、並び順は変わらない。
            # ボタンまで見るのは、廃止したボタンが押せる状態で残らないようにするため。
            try:
                await message.edit(embed=embed, view=view)
            except discord.HTTPException:
                logger.exception(
                    "%sの書き換えに失敗しました: message_id=%s", panel_name, message.id
                )
                return PANEL_FOUND

            logger.info(
                "%sを最新の内容へ書き換えました（表題「%s」）: channel_id=%s",
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
