"""ギルド機能のDiscord側の手順をまとめた層。

``views.py`` のボタン・Select・Modalから呼ばれ、次の「一連の手順」を担当します。

- ギルド設立（DB登録 → チャンネル作成 → 失敗時の全額返金）
- 募集Embedの投稿・更新（GAME_SPEC 6.1節の文面）
- 参加申請Embedの投稿・削除（GAME_SPEC 6.2節）
- ギルド解散の後片付け（アーカイブ・DM・申請Embed削除）
- 各パネルEmbedの組み立て

SQLはすべて ``database/guild.py`` などの公開APIへ委譲し、料金・上限などの数値は
``game.master_data.load_master_data()`` から取得します（コードへ直接書きません）。
ギルド名を表示するメッセージは、意図しないメンション通知が飛ばないよう
``discord.AllowedMentions.none()`` を明示して送信します（GAME_SPEC 5.2節）。
"""

from __future__ import annotations

import logging

import discord
import config

from cogs import game_shared
from cogs.coin.views import ensure_atm_panel
from utils import PANEL_MISSING, ensure_panel_message, remove_legacy_panels
from database.battle import (
    get_active_battle_for_guild,
    get_battle_lock,
    get_battle_recruitment,
    get_battle_request,
    resolve_battle_recruitment,
    resolve_battle_request,
)
from database.guild import (
    archive_guild,
    count_guild_members,
    create_guild,
    get_guild,
    get_guild_members,
    get_player_guild,
    refund_guild_creation,
    set_guild_channels,
    set_guild_info_channel,
    set_join_request_message,
    set_recruitment_message,
    set_recruitment_status,
)
from game.master_data import load_master_data


logger = logging.getLogger(__name__)


# ==================================================
# 共通定義
# ==================================================
# ギルド名にメンション表現が含まれていても通知が飛ばないようにする（5.2節）
NO_MENTIONS = game_shared.NO_MENTIONS

# ギルド紹介チャンネルの常設パネルのタイトル（ensure_panel_messageの識別キー）
PANEL_TITLE = "RAGNA Online"

# 改名前の表題。見つけたら片づけて、新しいパネルへ置き換える。
LEGACY_PANEL_TITLES = ("RAGNA Online ギルド",)

# マスター専用TCへ設置する常設パネルのタイトル
MANAGE_PANEL_TITLE = "ギルド管理"

# メンバー用パネルは表題がギルド名になるため、ボタンの custom_id で見分ける。
# ギルド名を変えてもパネルが増えず、表題だけが書き換わります（8.3節）。
MEMBER_PANEL_CUSTOM_IDS = ("guild:info", "guild:leave")

# 改名前のメンバー用パネルの表題。旧ギルドTCに残っていたら片づける。
LEGACY_MEMBER_PANEL_TITLES = ("ギルドメンバー",)

# 旧パネルの片づけを済ませたギルド。起動ごとにリセットされる（29節）。
_legacy_member_panels_checked: set[int] = set()

# 募集Embed・参加申請Embedのタイトル（6.1節・6.2節）
RECRUITMENT_TITLE = "【ギルドメンバー募集】"
JOIN_REQUEST_TITLE = "【ギルド参加申請】"

# 募集状態の表示名
RECRUITMENT_LABELS = {
    "open": "募集中",
    "closed": "募集停止",
}
ARCHIVED_LABEL = "解散済み"


# ==================================================
# 応答ヘルパー
# ==================================================
async def respond_no_mention(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> None:
    """ギルド名を含む応答を、メンション通知なしで送る（5.2節）。"""

    await game_shared.respond(
        interaction,
        content,
        embed=embed,
        view=view,
        ephemeral=ephemeral,
        allowed_mentions=NO_MENTIONS,
    )


# ==================================================
# メッセージ操作
# ==================================================
# 取得・削除・送信の共通処理は cogs/game_shared.py が持つ。
# 呼び出し側の記述を短くするため、この層でも同じ名前で使えるようにする。
MESSAGE_FOUND = game_shared.MESSAGE_FOUND
MESSAGE_GONE = game_shared.MESSAGE_GONE
MESSAGE_UNAVAILABLE = game_shared.MESSAGE_UNAVAILABLE

fetch_message_state = game_shared.fetch_message_state
fetch_message = game_shared.fetch_message
delete_message = game_shared.delete_message


async def delete_channels_by_id(
    discord_guild: discord.Guild | None, channel_ids: list[int]
) -> None:
    """作成途中で失敗したギルド専用チャンネルを、IDを頼りに削除する。

    カテゴリーを先に消すと配下のチャンネルを取得できなくなるため、
    テキスト・ボイスを先に削除してからカテゴリーを削除します。
    """

    if discord_guild is None or not channel_ids:
        return

    channels = [
        channel
        for channel in (discord_guild.get_channel(channel_id) for channel_id in channel_ids)
        if channel is not None
    ]
    channels.sort(key=lambda channel: isinstance(channel, discord.CategoryChannel))

    for channel in channels:
        try:
            await channel.delete(reason="ギルド作成の巻き戻し")
        except Exception:
            logger.warning("巻き戻し中のチャンネル削除に失敗しました: %s", channel.id)


async def delete_message(
    bot: discord.Client, channel_id: int | None, message_id: int | None
) -> bool:
    """指定メッセージを削除する。既に無い場合もエラーにしない。"""

    message = await fetch_message(bot, channel_id, message_id)
    if message is None:
        return False

    try:
        await message.delete()
        return True
    except discord.HTTPException:
        logger.warning("メッセージの削除に失敗しました: message_id=%s", message_id)
        return False


# ==================================================
# 権限の反映
# ==================================================
async def sync_guild_permissions(
    discord_guild: discord.Guild | None, guild_row: dict
) -> None:
    """現在の所属メンバーをチャンネル権限へ反映する（5.3節）。"""

    if discord_guild is None:
        return

    guild_id = int(guild_row["guild_id"])
    member_ids = [int(row["user_id"]) for row in get_guild_members(guild_id)]

    await game_shared.apply_guild_permissions(
        discord_guild,
        guild_row,
        member_ids=member_ids,
    )


# ==================================================
# Embedの組み立て
# ==================================================
def build_intro_panel_embed() -> discord.Embed:
    """ギルド紹介チャンネルへ置く設立パネルのEmbedを作る。"""

    balance = load_master_data().guild

    return discord.Embed(
        title=PANEL_TITLE,
        description=(
            "​\n"
            "**ギルド設立**\n"
            f"-# 設立費用：{game_shared.format_coin(balance.create_cost)}\n"
            f"-# 初期定員：{balance.initial_capacity}人（最大{balance.max_capacity}人）\n"
            "-# 騎士・七星だけが設立できます。\n"
            "-# ※既にギルドへ所属している場合は設立できません。\n\n"
            "**申請確認・取消**\n"
            "-# 自分の参加申請を確認・取消可能。"
        ),
        color=config.COLOR_WHITE,
    )


def build_manage_panel_embed() -> discord.Embed:
    """ギルドマスター専用TCへ置く管理パネルのEmbedを作る（8.1節）。"""

    balance = load_master_data().guild

    return discord.Embed(
        title=MANAGE_PANEL_TITLE,
        description=(
            "​\n"
            "**メンバー追放**\n"
            "-# 一般メンバーをギルドから外します。\n\n"
            "**マスター譲渡**\n"
            "-# 所属メンバー1人へマスターを譲ります。\n\n"
            "**メンバー枠拡張**\n"
            f"-# 1枠 {game_shared.format_coin(balance.member_slot_cost)} 自動引き落としで"
            f"拡張可能。（最大{balance.max_capacity}人）\n\n"
            "**ギルド名変更**\n"
            f"-# {game_shared.format_coin(balance.rename_cost)} 自動引き落としで変更可能。\n\n"
            "**ギルド説明変更**\n"
            f"-# {balance.description_min_length}〜{balance.description_max_length}文字で"
            "変更可能。\n\n"
            "**メンバー募集**\n"
            "-# 募集の開始・停止を切り替えます。\n\n"
            "**ギルド解散**\n"
            "-# 再確認のうえギルドを解散します。"
        ),
        color=config.COLOR_WHITE,
    )


def member_panel_title(guild_row: dict) -> str:
    """メンバー用パネルの表題（ギルド名）を返す（8.3節）。"""

    name = str(guild_row.get("name") or "").strip()

    # Embedのタイトル上限は256文字。ギルド名が未登録なら旧表題へ寄せる。
    return name[:256] if name else LEGACY_MEMBER_PANEL_TITLES[0]


def build_member_panel_embed(guild_row: dict) -> discord.Embed:
    """ギルド情報チャンネルへ置くメンバー用パネルのEmbedを作る（8.3節）。"""

    return discord.Embed(
        title=member_panel_title(guild_row),
        description=(
            "​\n"
            "**ギルド情報**\n"
            "-# ギルド名・説明・メンバー・戦績を確認できます。\n\n"
            "**ギルド脱退**\n"
            "-# 確認のうえギルドを脱退します。\n"
            "-# ギルドマスターは脱退できません。"
        ),
        color=config.COLOR_WHITE,
    )


def build_recruitment_embed(guild_row: dict, member_count: int) -> discord.Embed:
    """ギルド紹介チャンネルへ投稿する募集Embedを作る（6.1節の文面）。"""

    balance = load_master_data().guild
    capacity = int(guild_row.get("capacity") or balance.initial_capacity)

    if str(guild_row.get("status")) != "active":
        state = ARCHIVED_LABEL
        color = config.COLOR_GREY
    else:
        state = RECRUITMENT_LABELS.get(
            str(guild_row.get("recruitment_status")), RECRUITMENT_LABELS["closed"]
        )
        color = (
            config.COLOR_GREEN if state == RECRUITMENT_LABELS["open"] else config.COLOR_GREY
        )

    description = str(guild_row.get("description") or "未登録")

    embed = discord.Embed(
        title=RECRUITMENT_TITLE,
        description="\n".join(
            [
                game_shared.item_line("ギルド名", str(guild_row["name"])[:200]),
                game_shared.item_line("ギルド説明", description[:1000]),
                game_shared.item_line("メンバー", f"{member_count} / {capacity}"),
                game_shared.item_line("状態", state),
            ]
        ),
        color=color,
    )
    # embed.set_footer(text=f"ギルドID：{int(guild_row['guild_id'])}")

    return embed


def build_join_request_embed(
    guild_row: dict, applicant: discord.abc.User | int
) -> discord.Embed:
    """ギルドマスター専用TCへ投稿する参加申請Embedを作る（6.2節の文面）。"""

    applicant_id = applicant if isinstance(applicant, int) else applicant.id

    embed = discord.Embed(
        title=JOIN_REQUEST_TITLE,
        description=game_shared.item_line("申請者", f"<@{applicant_id}>"),
        color=config.COLOR_BLUE,
    )
    embed.set_footer(text=f"ギルド：{str(guild_row['name'])[:80]}")

    return embed


def build_guild_info_embed(
    discord_guild: discord.Guild | None, guild_row: dict
) -> discord.Embed:
    """ギルド情報Embedを作る（8.3節）。"""

    balance = load_master_data().guild
    guild_id = int(guild_row["guild_id"])
    capacity = int(guild_row.get("capacity") or balance.initial_capacity)
    members = get_guild_members(guild_id)

    lines: list[str] = []
    for row in members:
        user_id = int(row["user_id"])
        member = discord_guild.get_member(user_id) if discord_guild else None
        display = member.display_name if member is not None else f"ID:{user_id}"
        badge = "👑" if str(row["member_role"]) == "master" else "・"
        lines.append(f"{badge} {display}")

    master_id = int(guild_row["master_id"])
    master_member = discord_guild.get_member(master_id) if discord_guild else None
    master_name = (
        master_member.display_name if master_member is not None else f"ID:{master_id}"
    )

    body = [
        game_shared.item_line(
            "ギルド説明", str(guild_row.get("description") or "未登録")[:1000]
        ),
        game_shared.item_line("ギルドマスター", master_name),
        game_shared.item_line("メンバー", f"{len(members)} / {capacity}"),
        game_shared.item_line(
            "戦績",
            f"{int(guild_row.get('wins') or 0)}勝 "
            f"{int(guild_row.get('losses') or 0)}敗 "
            f"{int(guild_row.get('draws') or 0)}分",
        ),
        game_shared.item_line(
            "募集状態",
            RECRUITMENT_LABELS.get(
                str(guild_row.get("recruitment_status")), RECRUITMENT_LABELS["closed"]
            ),
        ),
        game_shared.item_line("メンバー一覧", "\n" + ("\n".join(lines)[:1500] or "—")),
    ]

    embed = discord.Embed(
        title=str(guild_row["name"])[:256],
        description="\n".join(body),
        color=config.COLOR_WHITE,
    )
    # embed.set_footer(text=f"ギルドID：{guild_id}")

    return embed


# ==================================================
# 募集Embedの投稿・更新（6.1節）
# ==================================================
async def refresh_recruitment_embed(bot: discord.Client, guild_id: int) -> bool:
    """人数・名称・説明・状態が変わったときに既存の募集Embedを更新する。

    メンバー増減・名称変更・説明変更・募集状態変更・解散のたびに呼びます。
    既存Embedを更新できた場合だけ ``True`` を返します。利用者が削除していた
    場合は記録を消して ``False`` を返し、次の募集開始で新規投稿できるように
    します。Discord側の一時的な失敗では記録を消しません。
    """

    from . import views

    guild_row = get_guild(guild_id)
    if guild_row is None:
        return False

    channel_id = guild_row.get("recruitment_channel_id")
    message_id = guild_row.get("recruitment_message_id")
    if not channel_id or not message_id:
        return False

    state, message = await fetch_message_state(bot, channel_id, message_id)

    if state == MESSAGE_GONE:
        # 手動削除された場合だけ記録を消す
        set_recruitment_message(guild_id, None, None)
        return False

    if message is None:
        # 一時的に確認できないだけなので、記録は残して次回に任せる
        logger.warning(
            "募集Embedを確認できませんでした（記録は保持します）: guild_id=%s", guild_id
        )
        return False

    disabled = (
        str(guild_row.get("status")) != "active"
        or str(guild_row.get("recruitment_status")) != "open"
    )

    try:
        await message.edit(
            embed=build_recruitment_embed(guild_row, count_guild_members(guild_id)),
            view=views.GuildRecruitmentView(disabled=disabled),
            allowed_mentions=NO_MENTIONS,
        )
    except discord.HTTPException:
        logger.exception("募集Embedの更新に失敗しました: guild_id=%s", guild_id)
        return False

    return True


async def post_recruitment_embed(bot: discord.Client, guild_id: int) -> bool:
    """メンバー募集チャンネルへ募集Embedを新規投稿する（6.1節）。"""

    from . import views

    if not config.GUILD_MEMBER_RECRUITMENT_CHANNEL_ID:
        logger.warning(
            "GUILD_MEMBER_RECRUITMENT_CHANNEL_ID が未設定のため募集Embedを投稿できません"
        )
        return False

    guild_row = get_guild(guild_id)
    if guild_row is None:
        return False

    channel = bot.get_channel(config.GUILD_MEMBER_RECRUITMENT_CHANNEL_ID)
    if channel is None or not hasattr(channel, "send"):
        logger.warning(
            "メンバー募集チャンネルが見つかりません: %s",
            config.GUILD_MEMBER_RECRUITMENT_CHANNEL_ID,
        )
        return False

    try:
        message = await channel.send(
            embed=build_recruitment_embed(guild_row, count_guild_members(guild_id)),
            view=views.GuildRecruitmentView(),
            allowed_mentions=NO_MENTIONS,
        )
    except discord.HTTPException:
        logger.exception("募集Embedの投稿に失敗しました: guild_id=%s", guild_id)
        return False

    set_recruitment_message(guild_id, channel.id, message.id)
    return True


async def open_recruitment(bot: discord.Client, guild_id: int) -> bool:
    """メンバー募集を開始する。未投稿なら新規投稿、投稿済みなら更新する。"""

    if not set_recruitment_status(guild_id, "open"):
        return False

    guild_row = get_guild(guild_id)
    if guild_row is None:
        return False

    if guild_row.get("recruitment_message_id"):
        if await refresh_recruitment_embed(bot, guild_id):
            return True

        # 既存Embedが削除されていた場合は記録も消えているので、新規投稿へ回す。
        # 記録が残っている＝一時的に確認できなかっただけなので、
        # 二重投稿を避けるためここでは失敗として返す（6.1節）。
        current = get_guild(guild_id)
        if current is not None and current.get("recruitment_message_id"):
            return False

    return await post_recruitment_embed(bot, guild_id)


async def close_recruitment(bot: discord.Client, guild_id: int) -> bool:
    """メンバー募集を停止する。Embedは残し「募集停止」表示へ更新する（6.1節）。"""

    if not set_recruitment_status(guild_id, "closed"):
        return False

    await refresh_recruitment_embed(bot, guild_id)
    return True


# ==================================================
# ギルド設立（5.2節）
# ==================================================
async def found_guild(interaction: discord.Interaction, name: str) -> None:
    """ギルドを設立する一連の手順を実行する。

    「DB登録 → Discordチャンネル作成」の順で進め、チャンネル作成に失敗した場合は
    ``refund_guild_creation`` で全額返金し、途中状態を残しません（5.2節）。
    """

    bot = interaction.client
    discord_guild = interaction.guild
    member = interaction.user

    if discord_guild is None or not isinstance(member, discord.Member):
        await game_shared.respond(interaction, "サーバー内で操作してください。")
        return

    balance = load_master_data().guild
    guild_name = name.strip()

    # 1. 入力チェック
    if not (balance.name_min_length <= len(guild_name) <= balance.name_max_length):
        await game_shared.respond(
            interaction,
            f"ギルド名は{balance.name_min_length}文字以上"
            f"{balance.name_max_length}文字以下で入力してください。",
        )
        return

    # 2. 騎士・七星・運営のロール確認（4節）。判定前にDBのロール同期を更新する
    game_shared.sync_member_rank(member)
    if game_shared.get_player_rank_info(member.id) is None:
        logger.warning("ロール同期を確認できないため設立を中止します: %s", member.id)
        await game_shared.respond(
            interaction, "ロール情報を確認できませんでした。時間をおいて再度お試しください。"
        )
        return

    if not game_shared.can_found_guild_member(member):
        await game_shared.respond(
            interaction,
            "ギルドを設立できるのは騎士・七星・運営のロールを持つプレイヤーだけです。",
        )
        return

    # 3. 既に所属していないか
    existing = get_player_guild(member.id)
    if existing is not None:
        await respond_no_mention(
            interaction, f"既に「{existing['name']}」へ所属しているため設立できません。"
        )
        return

    # 4. DB登録（残高確認・減算・履歴・登録を1トランザクション）
    result = create_guild(
        name=guild_name,
        master_id=member.id,
        capacity=balance.initial_capacity,
        cost=balance.create_cost,
    )
    if not result["ok"]:
        await game_shared.respond(interaction, game_shared.error_message(result["error"]))
        return

    guild_id = int(result["guild_id"])

    # 設立＝自分のギルドへの加入なので、他ギルドへ出していた未処理の参加申請は
    # create_guild が取り消し済み。残っている申請Embedをここで削除する（6.2節）。
    for cancelled in result.get("cancelled") or []:
        await delete_message(
            bot, cancelled.get("channel_id"), cancelled.get("message_id")
        )

    # 5. Discordカテゴリー・チャンネルの作成
    # 何が起きても「チャンネル無し・所属あり」の状態を残さないよう、
    # ここから先の失敗はすべて返金経路へ集約する（5.2節）。
    channels = None
    created_channel_ids: list[int] = []
    try:
        channels = await game_shared.create_guild_channels(
            discord_guild, guild_name=guild_name, master_id=member.id
        )

        if channels is not None:
            created_channel_ids = [int(value) for value in channels.values()]
            set_guild_channels(guild_id, **channels)

    except Exception:
        logger.exception("ギルド専用チャンネルの作成に失敗しました: guild_id=%s", guild_id)
        channels = None

    if channels is None:
        # 作成済みのチャンネルが残っていれば削除してから返金する（5.2節）
        await delete_channels_by_id(discord_guild, created_channel_ids)

        # 途中状態を残さず全額返金する（5.2節）
        refunded = refund_guild_creation(guild_id, member.id, balance.create_cost)

        await game_shared.game_admin_log(
            bot,
            action="ギルド設立失敗",
            executor_id=member.id,
            target_guild_id=guild_id,
            success=False,
            reason="Discordチャンネルの作成に失敗しました",
            detail=f"設立費用の返金：{'成功' if refunded else '未実行'}",
        )

        await game_shared.respond(
            interaction,
            "ギルド専用チャンネルの作成に失敗しました。\n"
            + (
                f"設立費用 {game_shared.format_coin(balance.create_cost)} は返金しました。"
                if refunded
                else "返金処理を確認できませんでした。運営へお問い合わせください。"
            ),
        )
        return

    guild_row = get_guild(guild_id)
    if guild_row is None:
        await game_shared.respond(interaction, "ギルド情報の保存を確認できませんでした。")
        return

    # 6. 常設パネルの設置
    await post_guild_panels(bot, discord_guild, guild_row)

    await game_shared.game_admin_log(
        bot,
        action="ギルド設立",
        executor_id=member.id,
        target_guild_id=guild_id,
        success=True,
        detail=f"ギルド名：{guild_name}",
    )

    guild_text = discord_guild.get_channel(int(channels["guild_text_channel_id"]))
    mention = guild_text.mention if guild_text is not None else "ギルドTC"

    await respond_no_mention(
        interaction,
        f"ギルド「{guild_name}」を設立しました。\n"
        f"設立費用：{game_shared.format_coin(balance.create_cost)}\n"
        f"専用チャンネルを作成しました：{mention}",
    )


async def ensure_member_panel(
    bot: discord.Client, discord_guild: discord.Guild, guild_row: dict
) -> str:
    """ギルド情報チャンネルへメンバー用パネルを（無ければ）設置する（8.3節）。

    表題はギルド名です。ギルド名を変えても増えないよう、ボタンの ``custom_id`` で
    既存パネルを見分けて、表題だけを書き換えます。旧置き場（ギルドTC・使い魔バトル）
    に残っているパネルは、ここで片づけます。

    戻り値は ``utils`` のパネル状態（``PANEL_CREATED`` など）です。
    """

    from . import views

    # 旧置き場に残っているパネルを片づける。定期タスクから何度も呼ばれるため、
    # 履歴の読み直しはギルドごとに起動後1回だけにする（失敗しても再起動で再試行）。
    guild_id = int(guild_row.get("guild_id") or 0)

    if guild_id not in _legacy_member_panels_checked:
        _legacy_member_panels_checked.add(guild_id)

        info_channel_id = guild_row.get("info_channel_id")

        for column in ("guild_text_channel_id", "battle_member_channel_id"):
            old_channel_id = guild_row.get(column)
            if not old_channel_id:
                continue

            # 現行パネルのあるチャンネルは対象にしない
            if info_channel_id and int(old_channel_id) == int(info_channel_id):
                continue

            # 表題はギルド名なので、ボタンの custom_id で探す。旧世代の
            # 「ギルドメンバー」表題も一緒に片づける。
            await remove_legacy_panels(
                bot,
                discord_guild,
                int(old_channel_id),
                titles=LEGACY_MEMBER_PANEL_TITLES,
                custom_ids=MEMBER_PANEL_CUSTOM_IDS,
                history_limit=100,
            )

    channel_id = guild_row.get("info_channel_id")
    if not channel_id:
        return PANEL_MISSING

    return await ensure_panel_message(
        bot,
        discord_guild,
        int(channel_id),
        panel_title=member_panel_title(guild_row),
        embed=build_member_panel_embed(guild_row),
        view=views.GuildMemberPanelView(),
        panel_name="ギルドメンバー用パネル",
        match_custom_ids=MEMBER_PANEL_CUSTOM_IDS,
    )


async def ensure_guild_info_channel(
    bot: discord.Client, discord_guild: discord.Guild, guild_row: dict
) -> dict:
    """ギルド情報チャンネルを用意し、必要なら ``guild_row`` を更新して返す。

    このチャンネルを増やす前に設立されたギルドにも、あとから作ります。
    """

    if guild_row.get("info_channel_id") and discord_guild.get_channel(
        int(guild_row["info_channel_id"])
    ):
        return guild_row

    guild_id = int(guild_row["guild_id"])
    member_ids = [int(row["user_id"]) for row in get_guild_members(guild_id)]

    channel_id = await game_shared.ensure_guild_info_channel(
        discord_guild, guild_row, member_ids=member_ids
    )
    if channel_id is None:
        return guild_row

    set_guild_info_channel(guild_id, channel_id)
    logger.info(
        "ギルド情報チャンネルを追加しました: guild_id=%s channel_id=%s",
        guild_id,
        channel_id,
    )

    return {**guild_row, "info_channel_id": channel_id}


async def post_guild_panels(
    bot: discord.Client, discord_guild: discord.Guild, guild_row: dict
) -> None:
    """設立直後のギルドへ常設パネルを設置する。

    ギルドマスター専用TCへ「ギルド管理」パネル（8.1節）、ギルド情報チャンネルへ
    メンバー用パネル（8.3節）を投稿します。バトル用パネル（8.2節）と
    使い魔バトルチャンネルのパネルは、ギルドバトルCogが公開している
    ``ensure_battle_panel`` へ任せます。
    """

    from . import views

    guild_row = await ensure_guild_info_channel(bot, discord_guild, guild_row)

    # 同じチャンネルに並ぶギルドバトルパネルの表題は、担当Cogから借りる
    # （読み込み順で循環しないよう、ここで取り込む）
    from cogs.guild_battle.views import BATTLE_PANEL_TITLE

    # ensure_panel_message を使い、既に設置済みなら送信しない。
    # 設立時に失敗した場合も、定期タスクから同じ関数を呼んで復旧できる（29節）。
    master_channel_id = guild_row.get("master_text_channel_id")
    if master_channel_id:
        # メンバー枠拡張・ギルド名変更・バトルのベットでcoinを使うため、
        # ATMパネルを一番上へ置く。参加申請Embedやバトル申請Embedは貼り直せない
        # ので、動かしてよい常設パネルの表題だけを渡す。
        await ensure_atm_panel(
            bot,
            discord_guild,
            int(master_channel_id),
            movable_titles=(MANAGE_PANEL_TITLE, BATTLE_PANEL_TITLE),
        )

        await ensure_panel_message(
            bot,
            discord_guild,
            int(master_channel_id),
            panel_title=MANAGE_PANEL_TITLE,
            embed=build_manage_panel_embed(),
            view=views.GuildManageView(),
            panel_name="ギルド管理パネル",
        )

    await ensure_member_panel(bot, discord_guild, guild_row)

    # バトル用パネル（8.2節）はギルドバトルCogの責務。実装があれば設置を任せる。
    for cog in getattr(bot, "cogs", {}).values():
        handler = getattr(cog, "ensure_battle_panel", None)
        if handler is None:
            continue
        try:
            await handler(guild_row)
        except Exception:
            logger.exception("バトル用パネルの設置に失敗しました: %s", guild_row["guild_id"])
        return


# ==================================================
# 参加申請Embed（6.2節）
# ==================================================
async def post_join_request_embed(
    bot: discord.Client,
    guild_row: dict,
    request_id: int,
    applicant: discord.abc.User,
) -> bool:
    """ギルドマスター専用TCへ参加申請Embedを投稿し、メッセージIDを保存する。"""

    from . import views

    channel_id = guild_row.get("master_text_channel_id")
    if not channel_id:
        logger.warning(
            "ギルドマスター専用TCが未設定です: guild_id=%s", guild_row["guild_id"]
        )
        return False

    channel = bot.get_channel(int(channel_id))
    if channel is None or not hasattr(channel, "send"):
        logger.warning("ギルドマスター専用TCが見つかりません: %s", channel_id)
        return False

    try:
        message = await channel.send(
            embed=build_join_request_embed(guild_row, applicant),
            view=views.JoinRequestView(),
            allowed_mentions=NO_MENTIONS,
        )
    except discord.HTTPException:
        logger.exception("参加申請Embedの投稿に失敗しました: request_id=%s", request_id)
        return False

    set_join_request_message(request_id, channel.id, message.id)
    return True


async def delete_join_request_embed(
    bot: discord.Client, request_row: dict | None
) -> None:
    """回答済み・取消済みの参加申請Embedを削除する（6.2節）。"""

    if not request_row:
        return

    await delete_message(
        bot, request_row.get("channel_id"), request_row.get("message_id")
    )


# ==================================================
# ギルド解散（7.5節）
# ==================================================
def can_disband_or_leave(guild_id: int, guild_row: dict) -> str | None:
    """進行中バトル・編成ロック中を判定し、拒否理由があれば返す（7.5節）。

    解散はバトル進行中だけを拒否します。未処理のバトル申請・募集は
    ``disband_guild`` が終了させるため、ここでは拒否理由になりません。
    """

    if get_active_battle_for_guild(guild_id) is not None:
        return "ギルドバトルが進行中のため実行できません。"

    if int(guild_row.get("roster_locked") or 0):
        return "バトルの編成がロックされているため実行できません。"

    return None


def can_kick_member(guild_id: int, guild_row: dict) -> str | None:
    """メンバー追放を実行できるかを判定する（7.2節・9節）。

    追放は出場者セットからの除外も伴うため、編成ロック中と進行中バトル中は
    実行できません。9節「バトルの編成がロックされた後はセットを変更できません」
    を、脱退だけでなく追放にも適用します。
    """

    return can_disband_or_leave(guild_id, guild_row)


def can_leave_guild(guild_id: int, guild_row: dict) -> str | None:
    """脱退できない状態かを判定する（7.1節）。

    7.1節は進行中バトルと編成ロックに加えて、バトル申請中・バトル募集中の
    メンバーも脱退できないと定めています。申請・募集・進行中バトルはギルド
    単位の占有ロックで管理しているため、ロックの種類から理由を返します。
    """

    reason = can_disband_or_leave(guild_id, guild_row)
    if reason is not None:
        return reason

    lock = get_battle_lock(guild_id)
    if lock is None:
        return None

    lock_type = str(lock.get("lock_type") or "")
    if lock_type == "request":
        return "バトル申請中のため脱退できません。申請の取消後に実行してください。"
    if lock_type == "recruitment":
        return "バトル募集中のため脱退できません。募集の取消後に実行してください。"

    return "ギルドバトルの準備中のため脱退できません。"


async def cancel_battle_offers(bot: discord.Client, guild_id: int) -> None:
    """未処理のバトル申請・バトル募集を終了する（7.5節）。

    解散時に呼びます。占有ロックの解放はDB側で行われるため、ここでは
    Discordに残る申請Embed・募集Embedの後始末だけを追加で行います。
    """

    lock = get_battle_lock(guild_id)
    if lock is None:
        return

    lock_type = str(lock.get("lock_type") or "")
    reference_id = int(lock.get("reference_id") or 0)

    if lock_type == "request":
        request_row = get_battle_request(reference_id)
        result = resolve_battle_request(reference_id, "cancelled")

        if result.get("ok") and request_row:
            await delete_message(
                bot, request_row.get("channel_id"), request_row.get("message_id")
            )
        return

    if lock_type == "recruitment":
        recruitment_row = get_battle_recruitment(reference_id)
        result = resolve_battle_recruitment(reference_id, "cancelled")

        if result.get("ok") and recruitment_row:
            await delete_message(
                bot,
                recruitment_row.get("channel_id"),
                recruitment_row.get("message_id"),
            )
        return

    logger.warning(
        "解散時に想定外の占有ロックが残っています: guild_id=%s lock_type=%s",
        guild_id,
        lock_type,
    )


async def disband_guild(interaction: discord.Interaction, guild_row: dict) -> None:
    """ギルドを解散し、Discord側の後片付けまで行う（7.5節）。"""

    bot = interaction.client
    discord_guild = interaction.guild
    guild_id = int(guild_row["guild_id"])
    guild_name = str(guild_row["name"])
    balance = load_master_data().guild

    # 進行中バトル・編成ロックの再確認
    reason = can_disband_or_leave(guild_id, guild_row)
    if reason is not None:
        await game_shared.respond(interaction, reason)
        return

    result = archive_guild(guild_id)
    if not result["ok"]:
        await game_shared.respond(interaction, game_shared.error_message(result["error"]))
        return

    # 未処理の参加申請Embedを削除する
    for channel_id, message_id in result["request_message_ids"]:
        await delete_message(bot, channel_id, message_id)

    # 未処理のバトル申請・バトル募集も終了する（7.5節）
    await cancel_battle_offers(bot, guild_id)

    # 元メンバーの閲覧権限を先に解除する（7.5節）。
    # カテゴリーのアーカイブに失敗しても、元メンバーがチャンネルを見られる
    # 状態を残さないため、権限解除とアーカイブは別々に実行する。
    revoked = True
    archived = False

    if discord_guild is not None:
        revoked = await game_shared.revoke_guild_permissions(discord_guild, guild_row)

        # Discordカテゴリーをアーカイブ状態へ（解散済み表示・運営限定）
        archived = await game_shared.archive_guild_channels(
            discord_guild, guild_row, prefix=balance.archive_name_prefix
        )

    # 募集Embedを「解散済み」へ更新
    await refresh_recruitment_embed(bot, guild_id)

    # 元メンバーへDM通知
    dm_embed = discord.Embed(
        title="ギルド解散",
        description=f"所属していたギルド「{guild_name}」が解散しました。",
        color=config.COLOR_GREY,
    )
    for user_id in result["member_ids"]:
        user = None
        if discord_guild is not None:
            user = discord_guild.get_member(int(user_id))
        if user is None:
            user = bot.get_user(int(user_id))
        await game_shared.send_dm(user, embed=dm_embed)

    await game_shared.game_admin_log(
        bot,
        action="ギルド解散",
        executor_id=interaction.user.id,
        target_guild_id=guild_id,
        success=True,
        detail=(
            f"ギルド名：{guild_name} / "
            f"閲覧権限の解除：{'成功' if revoked else '失敗'} / "
            f"カテゴリーのアーカイブ：{'成功' if archived else '失敗'}"
        ),
    )

    await respond_no_mention(
        interaction,
        f"ギルド「{guild_name}」を解散しました。\n"
        f"専用カテゴリーは{balance.archive_days}日間保存されます。"
        + ("" if archived else "\n※カテゴリーのアーカイブに失敗しました。運営へご連絡ください。"),
    )
