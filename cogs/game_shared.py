"""RAGNA Onlineの3つのCogが共通で使うDiscord処理。

ギルド専用チャンネルの権限、DM通知、運営ログ、プレイヤーランク同期など、
ギルド・使い魔・ギルドバトルのどれか1つに属さない処理だけを置きます。
戦闘計算は ``game/``、SQLは ``database/`` の責務です。
"""

from __future__ import annotations

import logging

from datetime import datetime, timedelta, timezone

import discord
import config

from database.battle import add_admin_log
from database.player_rank import (
    get_player_rank,
    get_player_rank_record,
    sync_player_rank,
    sync_player_ranks,
)


logger = logging.getLogger(__name__)


# ==================================================
# 共通の定数
# ==================================================
# 利用者へ返す共通メッセージ
DISABLED_MESSAGE = "現在この機能はご利用いただけません。"
CHANNEL_ERROR_MESSAGE = "チャンネル情報を取得できませんでした。"
UNEXPECTED_ERROR_MESSAGE = "処理に失敗しました。時間をおいて試してください。"

# ギルド名にメンション表現が含まれていても通知が飛ばないようにする（5.2節）
NO_MENTIONS = discord.AllowedMentions.none()

# 報酬日付などの判定に使う日本時間（既存 cogs/coin/cog.py と同じ定義）
JST = timezone(timedelta(hours=9))


def now_utc() -> datetime:
    """現在時刻（UTC）を返す。"""

    return datetime.now(timezone.utc)


def parse_iso(value: str | None) -> datetime | None:
    """DBに保存されたUTCのISO 8601文字列をdatetimeへ戻す。"""

    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("日時の読み込みに失敗しました: %r", value)
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


# ==================================================
# エラーコードの日本語表示
# ==================================================
ERROR_MESSAGES = {
    # ギルド
    "already_in_guild": "既にギルドへ所属しています。",
    "insufficient_balance": "coinが不足しています。",
    "guild_not_found": "ギルド情報が見つかりません。",
    "guild_full": "このギルドは現在満員のため参加申請できません。",
    "capacity_max": "メンバー枠は最大まで拡張済みです。",
    "not_master": "ギルドマスターだけが実行できます。",
    "not_member": "対象がこのギルドに所属していません。",
    "master_cannot_leave": "ギルドマスターは脱退できません。マスターを譲渡するか、ギルドを解散してください。",
    "already_requested": "既にこのギルドへ参加申請しています。",
    "recruitment_closed": "このギルドは現在メンバーを募集していません。",
    "not_pending": "この申請は既に処理済みです。",
    "not_owner": "この申請の作成者だけが取り消せます。",
    # 使い魔
    "not_owned": "その使い魔を所有していません。",
    "different_familiar": "同じ種類の使い魔同士でしか合成できません。",
    "same_instance": "同じ個体を素材にできません。",
    "max_level": "この使い魔は既に最大レベルです。",
    "in_use": "バトルで使用中の使い魔は操作できません。",
    # バトル
    "roster_locked": "編成がロックされているため変更できません。",
    "duplicate_member": "同じメンバーを重複して選択できません。",
    "not_selected": "出場者に選ばれていません。",
    "same_guild": "自分のギルドへは申し込めません。",
    "guild_busy": "このギルドには既に有効な申請・募集・進行中バトルがあります。",
    "opponent_busy": "相手ギルドには既に有効な申請・募集・進行中バトルがあります。",
    "already_matched": "この募集は既に受付を終了しました。",
    "not_open": "この募集は既に終了しています。",
    "already_finished": "このバトルは既に終了しています。",
}


def error_message(code: str | None) -> str:
    """DB層のエラーコードを利用者向けの日本語へ変換する。"""

    if not code:
        return "処理に失敗しました。"

    return ERROR_MESSAGES.get(code, "処理に失敗しました。")


# ==================================================
# 公開状態の確認
# ==================================================
def is_game_enabled() -> bool:
    return bool(config.GAME_ENABLED)


def missing_channel_settings() -> list[str]:
    """未設定のチャンネルID環境変数名を返す。"""

    required = {
        "GUILD_INTRO_CHANNEL_ID": config.GUILD_INTRO_CHANNEL_ID,
        "GUILD_MEMBER_RECRUITMENT_CHANNEL_ID": (
            config.GUILD_MEMBER_RECRUITMENT_CHANNEL_ID
        ),
        "FAMILIAR_PANEL_CHANNEL_ID": config.FAMILIAR_PANEL_CHANNEL_ID,
        "GUILD_BATTLE_RECRUITMENT_CHANNEL_ID": config.GUILD_BATTLE_RECRUITMENT_CHANNEL_ID,
        "GUILD_RECEPTION_CHANNEL_ID": config.GUILD_RECEPTION_CHANNEL_ID,
    }
    return [name for name, value in required.items() if not value]


# ==================================================
# プレイヤーランク同期（27節）
# ==================================================
def _rank_payload(member: discord.Member) -> dict:
    role_ids = {role.id for role in member.roles}

    player_rank = None
    for role_id, rank in config.PLAYER_RANK_ROLES.items():
        if role_id in role_ids:
            player_rank = rank
            break

    return {
        "user_id": member.id,
        "player_rank": player_rank,
        "is_manager": config.ROLE_MANAGER in role_ids,
        "is_sub_manager": config.ROLE_SUB_MANAGER in role_ids,
        "is_member": config.ROLE_MEMBER in role_ids,
    }


def sync_member_rank(member: discord.Member) -> None:
    """1人分のDiscordロールを ``player_roles`` へ同期する。"""

    payload = _rank_payload(member)
    sync_player_rank(
        payload["user_id"],
        player_rank=payload["player_rank"],
        is_manager=payload["is_manager"],
        is_sub_manager=payload["is_sub_manager"],
        is_member=payload["is_member"],
    )


def sync_all_member_ranks(guild: discord.Guild) -> int:
    """サーバー全員分のロールを一括同期する。Bot起動時に使う。"""

    records = [_rank_payload(member) for member in guild.members if not member.bot]
    if not records:
        return 0

    return sync_player_ranks(records)


def get_player_rank_info(user_id: int) -> dict | None:
    """ゲーム処理で使うプレイヤーランク情報を返す。

    同期できていない場合は ``None`` を返します。呼び出し側は推測でランクを
    補わず、操作を停止してください（27節）。
    """

    record = get_player_rank_record(user_id)
    if record is None:
        return None

    return {
        "player_rank": record["player_rank"],
        "is_manager": bool(record["is_manager"]),
        "is_sub_manager": bool(record["is_sub_manager"]),
        "is_member": bool(record["is_member"]),
    }


def can_found_guild_member(member: discord.Member) -> bool:
    """騎士・七星のいずれかを持つか（4節）。"""

    role_ids = {role.id for role in member.roles}
    return any(role_id in role_ids for role_id in config.GUILD_FOUNDER_ROLES)


def is_manager(member: discord.Member) -> bool:
    role_ids = {role.id for role in member.roles}
    return config.ROLE_MANAGER in role_ids or member.guild_permissions.administrator


# ==================================================
# ギルド専用チャンネル（5.3節）
# ==================================================
CHANNEL_LABELS = {
    "guild_text": "ギルドtc",
    "guild_voice": "ギルドvc",
    "master_text": "ギルドマスター専用tc",
    "battle_member": "バトル出場者専用tc",
}

# 対戦成立時に作るバトル専用チャンネルの名前（34.14節）
BATTLE_CHANNEL_PREFIX = "バトル-"


def battle_channel_name(battle_id: int) -> str:
    return f"{BATTLE_CHANNEL_PREFIX}{battle_id}"


def _base_overwrites(guild: discord.Guild) -> dict:
    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }

    manager_role = guild.get_role(config.ROLE_MANAGER)
    if manager_role is not None:
        overwrites[manager_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )

    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
        )

    return overwrites


def member_overwrites(
    guild: discord.Guild,
    user_ids: list[int],
    *,
    send_messages: bool = True,
) -> dict:
    """指定ユーザーだけが閲覧できる権限設定を作る。"""

    overwrites = _base_overwrites(guild)

    for user_id in user_ids:
        member = guild.get_member(user_id)
        if member is None:
            continue

        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=send_messages,
            read_message_history=True,
        )

    return overwrites


async def create_guild_channels(
    guild: discord.Guild, *, guild_name: str, master_id: int
) -> dict | None:
    """ギルド専用カテゴリーと4つの常設チャンネルを作る（5.3節）。

    バトル用チャンネルは常設せず、対戦成立時に ``create_battle_channel`` で
    作ります（34.14節）。途中で失敗した場合は作成済みのチャンネルを削除し、
    ``None`` を返します。
    """

    created: list[discord.abc.GuildChannel] = []
    reason = f"RAGNA Onlineギルド「{guild_name}」の作成"

    try:
        # カテゴリーの権限は「このギルドを見られる人」の定義になる。
        # 終了後のバトルログはこの権限へ同期するため、所属メンバーを含める（34.14節）。
        category = await guild.create_category(
            name=guild_name,
            overwrites=member_overwrites(guild, [master_id], send_messages=False),
            reason=reason,
        )
        created.append(category)

        member_perms = member_overwrites(guild, [master_id])
        master_perms = member_overwrites(guild, [master_id])

        guild_text = await guild.create_text_channel(
            CHANNEL_LABELS["guild_text"],
            category=category,
            overwrites=member_perms,
            reason=reason,
        )
        created.append(guild_text)

        guild_voice = await guild.create_voice_channel(
            CHANNEL_LABELS["guild_voice"],
            category=category,
            overwrites=member_perms,
            reason=reason,
        )
        created.append(guild_voice)

        master_text = await guild.create_text_channel(
            CHANNEL_LABELS["master_text"],
            category=category,
            overwrites=master_perms,
            reason=reason,
        )
        created.append(master_text)

        battle_member = await guild.create_text_channel(
            CHANNEL_LABELS["battle_member"],
            category=category,
            overwrites=master_perms,
            reason=reason,
        )
        created.append(battle_member)

    except Exception:
        # 通信断やタイムアウトなど、Discordの例外以外で失敗しても途中状態を残さない。
        # ここで必ずNoneを返し、呼び出し側が確実に返金できるようにする（5.2節）。
        logger.exception("ギルド専用チャンネルの作成に失敗しました: %s", guild_name)

        for channel in reversed(created):
            try:
                await channel.delete(reason="ギルド作成の巻き戻し")
            except Exception:
                logger.warning("巻き戻し中のチャンネル削除に失敗しました: %s", channel.id)

        return None

    return {
        "category_id": category.id,
        "guild_text_channel_id": guild_text.id,
        "guild_voice_channel_id": guild_voice.id,
        "master_text_channel_id": master_text.id,
        "battle_member_channel_id": battle_member.id,
    }


async def create_battle_channel(
    guild: discord.Guild,
    guild_row: dict,
    *,
    battle_id: int,
    member_ids: list[int],
) -> int | None:
    """対戦成立時に、ギルドカテゴリー内へバトル専用チャンネルを作る（34.14節）。

    所属メンバー全員が閲覧できますが、投稿はできません。操作はすべて
    Botのボタンから行い、行動ログ・戦況・ターン通知もこのチャンネルへ出します。
    """

    category_id = guild_row.get("category_id")
    if not category_id:
        logger.warning(
            "ギルドカテゴリーが未登録のためバトルチャンネルを作れません: guild_id=%s",
            guild_row.get("guild_id"),
        )
        return None

    category = guild.get_channel(int(category_id))
    if not isinstance(category, discord.CategoryChannel):
        logger.warning("ギルドカテゴリーが見つかりません: %s", category_id)
        return None

    overwrites = member_overwrites(guild, member_ids, send_messages=False)

    try:
        channel = await guild.create_text_channel(
            battle_channel_name(battle_id),
            category=category,
            overwrites=overwrites,
            reason=f"ギルドバトル{battle_id}の開始",
        )
    except Exception:
        logger.exception(
            "バトル専用チャンネルの作成に失敗しました: guild_id=%s battle_id=%s",
            guild_row.get("guild_id"),
            battle_id,
        )
        return None

    return channel.id


async def open_battle_log_channel(
    guild: discord.Guild, channel_id: int | None
) -> bool:
    """終了したバトルのチャンネルを、カテゴリーの権限へ同期する（34.14節）。

    同期すると「ギルドカテゴリーを見られる人」全員が過去ログを読めるようになり、
    以後にギルドへ加入したメンバーも自動的に閲覧できます。
    """

    if not channel_id:
        return True

    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return True

    try:
        await channel.edit(
            sync_permissions=True, reason="ギルドバトル終了によるログ公開"
        )
    except Exception:
        logger.warning("バトルログの権限同期に失敗しました: %s", channel_id)
        return False

    return True


async def delete_channel(guild: discord.Guild, channel_id: int | None) -> bool:
    """チャンネルを削除する。既に無い場合も成功として扱う。"""

    if not channel_id:
        return True

    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return True

    try:
        await channel.delete(reason="RAGNA Onlineバトルチャンネルの保存期間終了")
    except discord.NotFound:
        return True
    except Exception:
        logger.warning("チャンネルの削除に失敗しました: %s", channel_id)
        return False

    return True


async def apply_guild_permissions(
    guild: discord.Guild,
    guild_row: dict,
    *,
    member_ids: list[int],
    roster_ids: list[int],
) -> None:
    """所属メンバーと出場者の変更をチャンネル権限へ反映する。"""

    master_id = guild_row["master_id"]

    targets = (
        # カテゴリーは「このギルドを見られる人」の定義。終了後のバトルログは
        # ここへ同期するため、所属メンバーの増減を必ず反映する（34.14節）。
        (guild_row.get("category_id"), member_ids, False),
        (guild_row.get("guild_text_channel_id"), member_ids, True),
        (guild_row.get("guild_voice_channel_id"), member_ids, True),
        (guild_row.get("master_text_channel_id"), [master_id], True),
        (
            guild_row.get("battle_member_channel_id"),
            sorted({*roster_ids, master_id}),
            True,
        ),
    )

    for channel_id, allowed_ids, can_send in targets:
        if not channel_id:
            continue

        channel = guild.get_channel(channel_id)
        if channel is None:
            continue

        try:
            await channel.edit(
                overwrites=member_overwrites(
                    guild, allowed_ids, send_messages=can_send
                ),
                reason="RAGNA Onlineギルド権限の更新",
            )
        except (discord.HTTPException, discord.Forbidden):
            logger.warning("ギルドチャンネルの権限更新に失敗しました: %s", channel_id)


async def revoke_guild_permissions(guild: discord.Guild, guild_row: dict) -> bool:
    """ギルド専用チャンネルの閲覧権限を、運営とBotだけに戻す（5.3節・7.5節）。

    解散時に呼びます。カテゴリーのアーカイブに失敗しても、元メンバー（元マスターを
    含む）がチャンネルを閲覧できる状態を残さないため、権限解除だけを独立して
    実行できるようにしています。
    """

    overwrites = _base_overwrites(guild)
    channel_ids = [
        guild_row.get("category_id"),
        guild_row.get("guild_text_channel_id"),
        guild_row.get("guild_voice_channel_id"),
        guild_row.get("master_text_channel_id"),
        guild_row.get("battle_member_channel_id"),
    ]

    succeeded = True

    for channel_id in channel_ids:
        if not channel_id:
            continue

        channel = guild.get_channel(int(channel_id))
        if channel is None:
            continue

        try:
            await channel.edit(
                overwrites=overwrites, reason="RAGNA Onlineギルドの解散"
            )
        except Exception:
            logger.warning("解散時の権限解除に失敗しました: %s", channel_id)
            succeeded = False

    return succeeded


async def archive_guild_channels(
    guild: discord.Guild, guild_row: dict, *, prefix: str
) -> bool:
    """解散したギルドのカテゴリーを運営限定の保管状態にする（5.3節・7.5節）。"""

    category_id = guild_row.get("category_id")
    if not category_id:
        return True

    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return False

    overwrites = _base_overwrites(guild)
    name = category.name
    if not name.startswith(prefix):
        name = f"{prefix}{name}"

    try:
        await category.edit(
            name=name[:100],
            overwrites=overwrites,
            position=len(guild.categories),
            reason="RAGNA Onlineギルドの解散",
        )

        for channel in category.channels:
            await channel.edit(
                overwrites=overwrites, reason="RAGNA Onlineギルドの解散"
            )

    except (discord.HTTPException, discord.Forbidden):
        logger.exception("ギルドカテゴリーのアーカイブに失敗しました: %s", category_id)
        return False

    return True


async def delete_guild_channels(guild: discord.Guild, guild_row: dict) -> bool:
    """保存期間を過ぎたギルドカテゴリーを削除する。"""

    category_id = guild_row.get("category_id")
    if not category_id:
        return True

    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return True

    try:
        for channel in list(category.channels):
            await channel.delete(reason="RAGNA Onlineギルドの保存期間終了")
        await category.delete(reason="RAGNA Onlineギルドの保存期間終了")

    except (discord.HTTPException, discord.Forbidden):
        logger.exception("ギルドカテゴリーの削除に失敗しました: %s", category_id)
        return False

    return True


# ==================================================
# メッセージ操作
# ==================================================
# メッセージ取得の結果。一時的な通信失敗と「本当に消えている」を区別する。
MESSAGE_FOUND = "found"
MESSAGE_GONE = "gone"
MESSAGE_UNAVAILABLE = "unavailable"


async def fetch_message_state(
    bot: discord.Client, channel_id: int | None, message_id: int | None
) -> tuple[str, discord.Message | None]:
    """メッセージを取得し、``(状態, メッセージ)`` を返す。

    Discordの一時的な障害やレート制限で取得できなかっただけの場合に、
    「利用者が削除した」と誤判定してDBの記録を消さないよう、
    ``MESSAGE_GONE``（本当に存在しない）と ``MESSAGE_UNAVAILABLE``
    （今回は確認できなかった）を区別します（34.15節）。
    """

    if not channel_id or not message_id:
        return MESSAGE_GONE, None

    channel = bot.get_channel(int(channel_id))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except discord.NotFound:
            return MESSAGE_GONE, None
        except Exception:
            return MESSAGE_UNAVAILABLE, None

    if not hasattr(channel, "fetch_message"):
        return MESSAGE_GONE, None

    try:
        return MESSAGE_FOUND, await channel.fetch_message(int(message_id))
    except discord.NotFound:
        return MESSAGE_GONE, None
    except Exception:
        return MESSAGE_UNAVAILABLE, None


async def fetch_message(
    bot: discord.Client, channel_id: int | None, message_id: int | None
) -> discord.Message | None:
    """チャンネルIDとメッセージIDからメッセージを取得する。見つからなければNone。"""

    _, message = await fetch_message_state(bot, channel_id, message_id)
    return message


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
    except Exception:
        logger.warning("メッセージの削除に失敗しました: message_id=%s", message_id)
        return False


async def safe_send(
    channel: discord.abc.Messageable | None,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    file: discord.File | None = None,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> discord.Message | None:
    """送信失敗で処理を止めないための送信ヘルパ。

    通信断やタイムアウトなど、Discordの例外以外で失敗しても ``None`` を返します。
    """

    if channel is None or not hasattr(channel, "send"):
        return None

    kwargs: dict = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    if allowed_mentions is not None:
        kwargs["allowed_mentions"] = allowed_mentions

    try:
        return await channel.send(**kwargs)
    except Exception:
        logger.warning(
            "メッセージの送信に失敗しました: channel_id=%s",
            getattr(channel, "id", "unknown"),
        )
        return None


async def safe_delete_message(
    channel: discord.abc.Messageable | None, message_id: int | None
) -> None:
    """チャンネルオブジェクトからメッセージを削除する。既に無い場合は何もしない。"""

    if channel is None or not message_id or not hasattr(channel, "fetch_message"):
        return

    try:
        message = await channel.fetch_message(int(message_id))
        await message.delete()
    except discord.NotFound:
        return
    except Exception:
        logger.warning("メッセージの削除に失敗しました: message_id=%s", message_id)


# ==================================================
# 通知・ログ
# ==================================================
async def send_dm(
    user: discord.abc.User | None,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
) -> bool:
    """DMを送る。ブロックされていても処理を止めない。"""

    if user is None:
        return False

    try:
        await user.send(content=content, embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException):
        logger.info("DM送信に失敗しました: %s", getattr(user, "id", "unknown"))
        return False


async def game_admin_log(
    bot: discord.Client,
    *,
    action: str,
    executor_id: int | None = None,
    target_user_id: int | None = None,
    target_guild_id: int | None = None,
    target_battle_id: int | None = None,
    success: bool = True,
    reason: str | None = None,
    operation_id: str | None = None,
    detail: str | None = None,
) -> None:
    """運営操作ログをDBへ保存し、設定があればログチャンネルへも投稿する（29節）。"""

    add_admin_log(
        executor_id=executor_id,
        action=action,
        target_user_id=target_user_id,
        target_guild_id=target_guild_id,
        target_battle_id=target_battle_id,
        success=success,
        reason=reason,
        operation_id=operation_id,
    )

    if not config.GAME_ADMIN_LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(config.GAME_ADMIN_LOG_CHANNEL_ID)
    if channel is None or not hasattr(channel, "send"):
        return

    lines = [detail or "—", ""]

    if executor_id:
        lines.append(item_line("実行者", f"<@{executor_id}>"))
    if target_user_id:
        lines.append(item_line("対象ユーザー", f"<@{target_user_id}>"))
    if target_guild_id:
        lines.append(item_line("対象ギルド", target_guild_id))
    if target_battle_id:
        lines.append(item_line("対象バトル", target_battle_id))
    if reason:
        lines.append(item_line("理由", reason[:1000]))

    embed = discord.Embed(
        title=f"RAGNA Online：{action}",
        description="\n".join(lines),
        color=config.COLOR_GREEN if success else config.COLOR_RED,
    )

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.warning("運営ログの送信に失敗しました")


# ==================================================
# 表示
# Embedはfieldを使わず、本文へ「【項目】結果」の形で並べる
# ==================================================
def item_line(label: str, value: object) -> str:
    """「【項目】結果」の1行を作る。

    Embedの ``field`` は列幅が端末やモバイルで大きく変わるため使わず、
    項目はすべて本文へこの形で並べます。
    """

    return f"【{label}】{value}"


def format_coin(amount: int) -> str:
    return f"{amount:,} coin"


RANK_COLORS = {
    "S": 0xFEE75C,
    "A": 0x5C21FF,
    "B": 0xBEDBFF,
    "C": 0xBEFFD7,
}

RANK_EMOJIS = {
    "S": "🌟",
    "A": "💎",
    "B": "🔷",
    "C": "⬜",
}


def rank_label(rank: str) -> str:
    return f"{RANK_EMOJIS.get(rank, '')}{rank}"


async def respond(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
    allowed_mentions: discord.AllowedMentions | None = None,
) -> None:
    """まだ応答していなければ送信し、応答済みならfollowupで送る。

    ギルド名など、意図しないメンション通知を避けたい応答には
    ``allowed_mentions=NO_MENTIONS`` を渡します（5.2節）。
    """

    kwargs: dict = {"ephemeral": ephemeral}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if allowed_mentions is not None:
        kwargs["allowed_mentions"] = allowed_mentions

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
    except discord.HTTPException:
        logger.warning("Interactionへの応答に失敗しました")


# get_player_rank は他Cogからも直接使うため再公開する
__all__ = [
    "ERROR_MESSAGES",
    "apply_guild_permissions",
    "archive_guild_channels",
    "can_found_guild_member",
    "create_guild_channels",
    "delete_guild_channels",
    "error_message",
    "format_coin",
    "game_admin_log",
    "get_player_rank",
    "get_player_rank_info",
    "is_game_enabled",
    "is_manager",
    "item_line",
    "member_overwrites",
    "missing_channel_settings",
    "rank_label",
    "respond",
    "send_dm",
    "sync_all_member_ranks",
    "sync_member_rank",
]
