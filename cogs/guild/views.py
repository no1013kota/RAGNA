"""ギルド機能のボタン・Select・Modalをまとめたdiscord UI層。

常設パネルのViewは ``timeout=None`` かつ全アイテムに固定の ``custom_id`` を持たせ、
Bot再起動後も操作できるようにします。使う ``custom_id`` は次の14個だけです。

``guild:create`` ``guild:my_requests`` ``guild:join_request``
``guild:request_approve`` ``guild:request_reject``
``guild:kick`` ``guild:transfer`` ``guild:expand`` ``guild:rename``
``guild:description`` ``guild:recruit`` ``guild:disband``
``guild:info`` ``guild:leave``

一時的な（ephemeralな）Select・確認Viewには ``custom_id`` を付けません
（discord.pyが自動採番します）。ボタンが見えていても、押された時点で所属・役割・
現在状態をDBから必ず再確認します（GAME_SPEC 29節）。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import discord
import config

from cogs import game_shared
from database.guild import (
    approve_join_request,
    cancel_join_request,
    count_guild_members,
    create_join_request,
    expand_guild_capacity,
    get_guild,
    get_guild_by_channel,
    get_guild_by_recruitment_message,
    get_guild_member,
    get_guild_members,
    get_join_request_by_message,
    get_pending_join_requests_by_user,
    get_player_guild,
    reject_join_request,
    remove_guild_member,
    rename_guild,
    transfer_guild_master,
    update_guild_description,
)
from game.master_data import load_master_data

from . import service


logger = logging.getLogger(__name__)


# ==================================================
# 共通メッセージと再確認ヘルパー（29節）
# ==================================================
DISABLED_MESSAGE = game_shared.DISABLED_MESSAGE
CHANNEL_ERROR_MESSAGE = game_shared.CHANNEL_ERROR_MESSAGE
NOT_GUILD_CHANNEL_MESSAGE = "このチャンネルはギルド専用チャンネルとして登録されていません。"
ARCHIVED_MESSAGE = "このギルドは既に解散しています。"


async def _resolve_master_guild(
    interaction: discord.Interaction, *, guild_id: int | None = None
) -> dict[str, Any] | None:
    """操作者が対象ギルドの「現在のマスター」かをDBから再確認する。

    条件を満たさない場合は理由をephemeralで返し ``None`` を返します。
    """

    if not game_shared.is_game_enabled():
        await game_shared.respond(interaction, DISABLED_MESSAGE)
        return None

    if guild_id is not None:
        guild_row = get_guild(guild_id)
    else:
        channel = interaction.channel
        if channel is None:
            await game_shared.respond(interaction, CHANNEL_ERROR_MESSAGE)
            return None
        guild_row = get_guild_by_channel(channel.id)

    if guild_row is None:
        await game_shared.respond(interaction, NOT_GUILD_CHANNEL_MESSAGE)
        return None

    if str(guild_row.get("status")) != "active":
        await game_shared.respond(interaction, ARCHIVED_MESSAGE)
        return None

    if int(guild_row["master_id"]) != interaction.user.id:
        await game_shared.respond(interaction, game_shared.error_message("not_master"))
        return None

    return guild_row


async def _resolve_member_guild(
    interaction: discord.Interaction,
) -> dict[str, Any] | None:
    """操作者がそのチャンネルのギルドへ所属しているかをDBから再確認する。"""

    if not game_shared.is_game_enabled():
        await game_shared.respond(interaction, DISABLED_MESSAGE)
        return None

    channel = interaction.channel
    if channel is None:
        await game_shared.respond(interaction, CHANNEL_ERROR_MESSAGE)
        return None

    guild_row = get_guild_by_channel(channel.id)
    if guild_row is None:
        await game_shared.respond(interaction, NOT_GUILD_CHANNEL_MESSAGE)
        return None

    if str(guild_row.get("status")) != "active":
        await game_shared.respond(interaction, ARCHIVED_MESSAGE)
        return None

    if get_guild_member(int(guild_row["guild_id"]), interaction.user.id) is None:
        await game_shared.respond(interaction, "このギルドに所属していません。")
        return None

    return guild_row


def _member_label(discord_guild: discord.Guild | None, user_id: int) -> str:
    """メンバー選択肢に表示する名前を作る。"""

    member = discord_guild.get_member(user_id) if discord_guild else None
    if member is None:
        return f"ID:{user_id}"
    return member.display_name[:100]


async def _resolve_user(
    interaction: discord.Interaction, user_id: int
) -> discord.abc.User | None:
    """DM送信用にユーザーを解決する。"""

    if interaction.guild is not None:
        member = interaction.guild.get_member(user_id)
        if member is not None:
            return member

    return interaction.client.get_user(user_id)


# ==================================================
# 一時View：確認画面・単一セレクト
# ==================================================
class ConfirmView(discord.ui.View):
    """ephemeralな確認画面。一時Viewのため custom_id は付けない。"""

    def __init__(
        self,
        user_id: int,
        handler: Callable[[discord.Interaction], Awaitable[None]],
        *,
        confirm_label: str = "実行する",
        confirm_style: discord.ButtonStyle = discord.ButtonStyle.danger,
    ):
        super().__init__(timeout=180)

        self.user_id = user_id
        self.handler = handler
        self.confirm.label = confirm_label[:80]
        self.confirm.style = confirm_style

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="実行する", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()

        # 連打による二重実行を防ぐため、押下直後にボタンを消す
        try:
            await interaction.response.edit_message(view=None)
        except discord.HTTPException:
            logger.warning("確認画面の更新に失敗しました")

        try:
            await self.handler(interaction)
        except Exception:
            logger.exception("確認後の処理に失敗しました")
            await game_shared.respond(interaction, "処理に失敗しました。")

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await game_shared.respond(interaction, "操作をキャンセルしました。")


class SingleItemView(discord.ui.View):
    """Selectを1つだけ載せるephemeralなView。"""

    def __init__(self, item: discord.ui.Item):
        super().__init__(timeout=180)
        self.add_item(item)


# ==================================================
# 設立パネル（ギルド紹介チャンネル）
# ==================================================
class GuildPanelView(discord.ui.View):
    """ギルド紹介チャンネルへ常設する設立パネル。"""

    def __init__(self):
        super().__init__(timeout=None)

    # ==================================================
    # ギルド設立
    # ==================================================
    @discord.ui.button(
        label="ギルド設立",
        style=discord.ButtonStyle.primary,
        custom_id="guild:create",
    )
    async def create(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not game_shared.is_game_enabled():
            await game_shared.respond(interaction, DISABLED_MESSAGE)
            return

        if not isinstance(interaction.user, discord.Member):
            await game_shared.respond(interaction, "サーバー内で操作してください。")
            return

        # Modalを出す前に、明らかに設立できない場合を先に案内する
        if not game_shared.can_found_guild_member(interaction.user):
            await game_shared.respond(
                interaction,
                "ギルドを設立できるのは騎士・七星・運営のロールを持つプレイヤーだけです。",
            )
            return

        existing = get_player_guild(interaction.user.id)
        if existing is not None:
            await service.respond_no_mention(
                interaction, f"既に「{existing['name']}」へ所属しているため設立できません。"
            )
            return

        await interaction.response.send_modal(GuildCreateModal())

    # ==================================================
    # 自分の参加申請の確認・取消
    # ==================================================
    @discord.ui.button(
        label="申請確認・取消",
        style=discord.ButtonStyle.secondary,
        custom_id="guild:my_requests",
    )
    async def my_requests(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not game_shared.is_game_enabled():
            await game_shared.respond(interaction, DISABLED_MESSAGE)
            return

        requests = get_pending_join_requests_by_user(interaction.user.id)
        if not requests:
            await game_shared.respond(interaction, "未処理の参加申請はありません。")
            return

        await service.respond_no_mention(
            interaction,
            "取り消す参加申請を選択してください。",
            view=SingleItemView(JoinRequestCancelSelect(requests)),
        )


class GuildCreateModal(discord.ui.Modal, title="ギルド設立"):
    """ギルド名を入力する設立Modal（5.2節）。"""

    def __init__(self):
        super().__init__(timeout=300)

        balance = load_master_data().guild

        self.guild_name = discord.ui.TextInput(
            label="ギルド名",
            placeholder=f"{balance.name_min_length}〜{balance.name_max_length}文字",
            min_length=balance.name_min_length,
            max_length=balance.name_max_length,
            required=True,
        )
        self.add_item(self.guild_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await service.found_guild(interaction, self.guild_name.value)


class JoinRequestCancelSelect(discord.ui.Select):
    """自分が出している参加申請を選んで取り消すセレクト（一時View）。"""

    def __init__(self, requests: list[dict[str, Any]]):
        options: list[discord.SelectOption] = []

        for request in requests[:25]:
            guild_row = get_guild(int(request["guild_id"]))
            name = str(guild_row["name"]) if guild_row else f"ID:{request['guild_id']}"
            options.append(
                discord.SelectOption(label=name[:100], value=str(request["request_id"]))
            )

        super().__init__(
            placeholder="取り消す参加申請を選択してください",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        request_id = int(self.values[0])
        result = cancel_join_request(request_id, user_id=interaction.user.id)

        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        # 対象ギルドの申請Embedを削除する（6.2節）
        await service.delete_message(
            interaction.client, result["channel_id"], result["message_id"]
        )

        guild_row = get_guild(int(result["guild_id"]))
        name = str(guild_row["name"]) if guild_row else f"ID:{result['guild_id']}"

        await service.respond_no_mention(
            interaction, f"「{name}」への参加申請を取り消しました。"
        )


# ==================================================
# 募集Embed（ギルド紹介チャンネル・6.1節）
# ==================================================
class GuildRecruitmentView(discord.ui.View):
    """募集Embedへ付ける参加申請ボタン。募集停止中は無効化して残す。"""

    def __init__(self, *, disabled: bool = False):
        super().__init__(timeout=None)
        self.join_request.disabled = disabled

    @discord.ui.button(
        label="参加申請",
        style=discord.ButtonStyle.primary,
        custom_id="guild:join_request",
    )
    async def join_request(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not game_shared.is_game_enabled():
            await game_shared.respond(interaction, DISABLED_MESSAGE)
            return

        await interaction.response.defer(ephemeral=True)

        message = interaction.message
        if message is None:
            await game_shared.respond(interaction, "募集情報を取得できませんでした。")
            return

        guild_row = get_guild_by_recruitment_message(message.id)
        if guild_row is None:
            await game_shared.respond(interaction, "この募集は既に終了しています。")
            return

        if str(guild_row.get("status")) != "active":
            await game_shared.respond(interaction, ARCHIVED_MESSAGE)
            return

        guild_id = int(guild_row["guild_id"])

        if str(guild_row.get("recruitment_status")) != "open":
            await game_shared.respond(
                interaction, game_shared.error_message("recruitment_closed")
            )
            return

        if get_player_guild(interaction.user.id) is not None:
            await game_shared.respond(
                interaction, game_shared.error_message("already_in_guild")
            )
            return

        # 満員のときは申請を作らず、申請者だけへ通知する（6.3節）
        if count_guild_members(guild_id) >= int(guild_row["capacity"]):
            await game_shared.respond(
                interaction, game_shared.error_message("guild_full")
            )
            return

        result = create_join_request(guild_id, interaction.user.id)
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        request_id = int(result["request_id"])

        posted = await service.post_join_request_embed(
            interaction.client, guild_row, request_id, interaction.user
        )
        if not posted:
            # 申請Embedを出せない場合は申請を残さない
            cancel_join_request(request_id, user_id=interaction.user.id)
            await game_shared.respond(
                interaction,
                "参加申請の送信に失敗しました。時間をおいて再度お試しください。",
            )
            return

        await service.respond_no_mention(
            interaction,
            f"「{guild_row['name']}」へ参加申請しました。\n結果はDMでお知らせします。",
        )


# ==================================================
# 参加申請Embed（ギルドマスター専用TC・6.2節）
# ==================================================
class JoinRequestView(discord.ui.View):
    """参加申請Embedの承認・拒否ボタン。現在のマスターだけが操作できる。"""

    def __init__(self):
        super().__init__(timeout=None)

    # ==================================================
    # 承認
    # ==================================================
    @discord.ui.button(
        label="承認",
        style=discord.ButtonStyle.success,
        custom_id="guild:request_approve",
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        request_row = await self._resolve_request(interaction)
        if request_row is None:
            return

        request_id = int(request_row["request_id"])
        guild_id = int(request_row["guild_id"])
        applicant_id = int(request_row["user_id"])

        result = approve_join_request(request_id, approver_id=interaction.user.id)
        if not result["ok"]:
            if result["error"] == "not_pending":
                await service.delete_join_request_embed(interaction.client, request_row)
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        # 回答済みEmbedと、他ギルドで自動取消された申請Embedを削除する
        await service.delete_join_request_embed(interaction.client, request_row)
        for cancelled in result["cancelled"]:
            await service.delete_message(
                interaction.client, cancelled["channel_id"], cancelled["message_id"]
            )

        guild_row = get_guild(guild_id)
        if guild_row is not None:
            await service.sync_guild_permissions(interaction.guild, guild_row)

        await service.refresh_recruitment_embed(interaction.client, guild_id)

        guild_name = str(guild_row["name"]) if guild_row else f"ID:{guild_id}"
        applicant = await _resolve_user(interaction, applicant_id)
        await game_shared.send_dm(
            applicant,
            embed=discord.Embed(
                title="ギルド参加申請",
                description=f"「{guild_name}」への参加が承認されました。",
                color=config.COLOR_GREEN,
            ),
        )

        await service.respond_no_mention(
            interaction, f"<@{applicant_id}> の参加を承認しました。"
        )

    # ==================================================
    # 拒否
    # ==================================================
    @discord.ui.button(
        label="拒否",
        style=discord.ButtonStyle.danger,
        custom_id="guild:request_reject",
    )
    async def reject(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        request_row = await self._resolve_request(interaction)
        if request_row is None:
            return

        request_id = int(request_row["request_id"])
        guild_id = int(request_row["guild_id"])
        applicant_id = int(request_row["user_id"])

        result = reject_join_request(request_id, approver_id=interaction.user.id)
        if not result["ok"]:
            if result["error"] == "not_pending":
                await service.delete_join_request_embed(interaction.client, request_row)
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        await service.delete_join_request_embed(interaction.client, request_row)

        guild_row = get_guild(guild_id)
        guild_name = str(guild_row["name"]) if guild_row else f"ID:{guild_id}"

        applicant = await _resolve_user(interaction, applicant_id)
        await game_shared.send_dm(
            applicant,
            embed=discord.Embed(
                title="ギルド参加申請",
                description=f"「{guild_name}」への参加は見送られました。",
                color=config.COLOR_RED,
            ),
        )

        await service.respond_no_mention(
            interaction, f"<@{applicant_id}> の参加を拒否しました。"
        )

    # ==================================================
    # 共通：申請の再確認
    # ==================================================
    async def _resolve_request(
        self, interaction: discord.Interaction
    ) -> dict[str, Any] | None:
        """押されたEmbedから申請を特定し、現在のマスターかを再確認する。"""

        if not game_shared.is_game_enabled():
            await game_shared.respond(interaction, DISABLED_MESSAGE)
            return None

        await interaction.response.defer(ephemeral=True)

        message = interaction.message
        if message is None:
            await game_shared.respond(interaction, "申請情報を取得できませんでした。")
            return None

        request_row = get_join_request_by_message(message.id)
        if request_row is None:
            await game_shared.respond(interaction, "この参加申請は見つかりません。")
            return None

        guild_row = get_guild(int(request_row["guild_id"]))
        if guild_row is None or str(guild_row.get("status")) != "active":
            await game_shared.respond(interaction, ARCHIVED_MESSAGE)
            return None

        if int(guild_row["master_id"]) != interaction.user.id:
            await game_shared.respond(
                interaction, game_shared.error_message("not_master")
            )
            return None

        if str(request_row.get("status")) != "pending":
            await service.delete_join_request_embed(interaction.client, request_row)
            await game_shared.respond(
                interaction, game_shared.error_message("not_pending")
            )
            return None

        return request_row


# ==================================================
# ギルド管理パネル（ギルドマスター専用TC・8.1節）
# ==================================================
class GuildManageView(discord.ui.View):
    """ギルドマスターだけが操作できる常設の管理パネル。"""

    def __init__(self):
        super().__init__(timeout=None)

    # ==================================================
    # メンバー追放（7.2節）
    # ==================================================
    @discord.ui.button(
        label="メンバー追放",
        style=discord.ButtonStyle.danger,
        custom_id="guild:kick",
        row=0,
    )
    async def kick(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_master_guild(interaction)
        if guild_row is None:
            return

        guild_id = int(guild_row["guild_id"])
        members = [
            row
            for row in get_guild_members(guild_id)
            if str(row["member_role"]) != "master"
        ]

        if not members:
            await game_shared.respond(interaction, "追放できる一般メンバーがいません。")
            return

        await game_shared.respond(
            interaction,
            "追放するメンバーを選択してください。",
            view=SingleItemView(
                KickMemberSelect(interaction.guild, members, guild_id=guild_id)
            ),
        )

    # ==================================================
    # マスター譲渡（7.3節）
    # ==================================================
    @discord.ui.button(
        label="マスター譲渡",
        style=discord.ButtonStyle.secondary,
        custom_id="guild:transfer",
        row=0,
    )
    async def transfer(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_master_guild(interaction)
        if guild_row is None:
            return

        guild_id = int(guild_row["guild_id"])
        members = [
            row
            for row in get_guild_members(guild_id)
            if str(row["member_role"]) != "master"
        ]

        if not members:
            await game_shared.respond(interaction, "譲渡できるメンバーがいません。")
            return

        await game_shared.respond(
            interaction,
            "マスターを譲渡するメンバーを選択してください。",
            view=SingleItemView(
                TransferMemberSelect(interaction.guild, members, guild_id=guild_id)
            ),
        )

    # ==================================================
    # メンバー枠拡張（7.4節）
    # ==================================================
    @discord.ui.button(
        label="メンバー枠拡張",
        style=discord.ButtonStyle.secondary,
        custom_id="guild:expand",
        row=0,
    )
    async def expand(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_master_guild(interaction)
        if guild_row is None:
            return

        balance = load_master_data().guild
        guild_id = int(guild_row["guild_id"])
        capacity = int(guild_row["capacity"])

        if capacity >= balance.max_capacity:
            await game_shared.respond(
                interaction, game_shared.error_message("capacity_max")
            )
            return

        async def _run(confirm_interaction: discord.Interaction) -> None:
            row = await _resolve_master_guild(confirm_interaction, guild_id=guild_id)
            if row is None:
                return

            result = expand_guild_capacity(
                guild_id,
                payer_id=confirm_interaction.user.id,
                cost=balance.member_slot_cost,
                max_capacity=balance.max_capacity,
            )
            if not result["ok"]:
                await game_shared.respond(
                    confirm_interaction, game_shared.error_message(result["error"])
                )
                return

            await service.refresh_recruitment_embed(confirm_interaction.client, guild_id)

            await game_shared.respond(
                confirm_interaction,
                f"メンバー枠を拡張しました。\n"
                f"定員：{result['capacity']}人\n"
                f"支払い：{game_shared.format_coin(balance.member_slot_cost)}",
            )

        await game_shared.respond(
            interaction,
            f"メンバー枠を1つ拡張します（{capacity}人 → {capacity + 1}人）。\n"
            f"費用：{game_shared.format_coin(balance.member_slot_cost)}\n"
            "よろしいですか？",
            view=ConfirmView(
                interaction.user.id,
                _run,
                confirm_label="拡張する",
                confirm_style=discord.ButtonStyle.success,
            ),
        )

    # ==================================================
    # ギルド名変更（5.2節）
    # ==================================================
    @discord.ui.button(
        label="ギルド名変更",
        style=discord.ButtonStyle.secondary,
        custom_id="guild:rename",
        row=1,
    )
    async def rename(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_master_guild(interaction)
        if guild_row is None:
            return

        await interaction.response.send_modal(
            GuildRenameModal(int(guild_row["guild_id"]), str(guild_row["name"]))
        )

    # ==================================================
    # ギルド説明変更（6.1節）
    # ==================================================
    @discord.ui.button(
        label="ギルド説明変更",
        style=discord.ButtonStyle.secondary,
        custom_id="guild:description",
        row=1,
    )
    async def description(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_master_guild(interaction)
        if guild_row is None:
            return

        await interaction.response.send_modal(
            GuildDescriptionModal(
                int(guild_row["guild_id"]), guild_row.get("description")
            )
        )

    # ==================================================
    # メンバー募集（6.1節）
    # ==================================================
    @discord.ui.button(
        label="メンバー募集",
        style=discord.ButtonStyle.primary,
        custom_id="guild:recruit",
        row=1,
    )
    async def recruit(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_master_guild(interaction)
        if guild_row is None:
            return

        state = service.RECRUITMENT_LABELS.get(
            str(guild_row.get("recruitment_status")),
            service.RECRUITMENT_LABELS["closed"],
        )

        await service.respond_no_mention(
            interaction,
            f"現在の募集状態：{state}\n"
            "募集を開始するにはギルド説明の登録が必要です。",
            view=RecruitControlView(interaction.user.id, int(guild_row["guild_id"])),
        )

    # ==================================================
    # ギルド解散（7.5節）
    # ==================================================
    @discord.ui.button(
        label="ギルド解散",
        style=discord.ButtonStyle.danger,
        custom_id="guild:disband",
        row=1,
    )
    async def disband(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_master_guild(interaction)
        if guild_row is None:
            return

        guild_id = int(guild_row["guild_id"])
        balance = load_master_data().guild

        async def _final(final_interaction: discord.Interaction) -> None:
            row = await _resolve_master_guild(final_interaction, guild_id=guild_id)
            if row is None:
                return

            await service.disband_guild(final_interaction, row)

        async def _first(first_interaction: discord.Interaction) -> None:
            row = await _resolve_master_guild(first_interaction, guild_id=guild_id)
            if row is None:
                return

            reason = service.can_disband_or_leave(guild_id, row)
            if reason is not None:
                await game_shared.respond(first_interaction, reason)
                return

            # 2段階目の確認
            await service.respond_no_mention(
                first_interaction,
                f"最終確認：ギルド「{row['name']}」を解散します。\n"
                "この操作は取り消せません。本当によろしいですか？",
                view=ConfirmView(
                    first_interaction.user.id, _final, confirm_label="解散する"
                ),
            )

        await service.respond_no_mention(
            interaction,
            f"ギルド「{guild_row['name']}」を解散します。\n"
            "・所属メンバー全員がギルドから外れます。\n"
            "・支払い済みの費用は返金されません。\n"
            f"・専用カテゴリーは{balance.archive_days}日間保存後に削除されます。",
            view=ConfirmView(interaction.user.id, _first, confirm_label="確認へ進む"),
        )


# ==================================================
# 管理パネルの補助View・Modal
# ==================================================
class KickMemberSelect(discord.ui.Select):
    """追放するメンバーを選ぶセレクト（一時View）。"""

    def __init__(
        self,
        discord_guild: discord.Guild | None,
        members: list[dict[str, Any]],
        *,
        guild_id: int,
    ):
        self.guild_id = guild_id

        options = [
            discord.SelectOption(
                label=_member_label(discord_guild, int(row["user_id"])),
                value=str(int(row["user_id"])),
            )
            for row in members[:25]
        ]

        super().__init__(
            placeholder="追放するメンバーを選択してください",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = await _resolve_master_guild(interaction, guild_id=self.guild_id)
        if guild_row is None:
            return

        target_id = int(self.values[0])
        if target_id == int(guild_row["master_id"]):
            await game_shared.respond(interaction, "ギルドマスターは追放できません。")
            return

        # 追放も出場者セットを変更するため、編成ロック中・バトル中は実行しない
        # （9節「編成がロックされた後はセットを変更できません」）。
        blocked = service.can_kick_member(self.guild_id, guild_row)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        result = remove_guild_member(self.guild_id, target_id)
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        guild_row = get_guild(self.guild_id)
        if guild_row is not None:
            await service.sync_guild_permissions(interaction.guild, guild_row)

        await service.refresh_recruitment_embed(interaction.client, self.guild_id)

        guild_name = str(guild_row["name"]) if guild_row else f"ID:{self.guild_id}"
        target = await _resolve_user(interaction, target_id)
        await game_shared.send_dm(
            target,
            embed=discord.Embed(
                title="ギルド追放",
                description=f"ギルド「{guild_name}」から追放されました。",
                color=config.COLOR_RED,
            ),
        )

        await service.respond_no_mention(
            interaction, f"<@{target_id}> をギルドから追放しました。"
        )


class TransferMemberSelect(discord.ui.Select):
    """マスターを譲渡する相手を選ぶセレクト（一時View）。"""

    def __init__(
        self,
        discord_guild: discord.Guild | None,
        members: list[dict[str, Any]],
        *,
        guild_id: int,
    ):
        self.guild_id = guild_id

        options = [
            discord.SelectOption(
                label=_member_label(discord_guild, int(row["user_id"])),
                value=str(int(row["user_id"])),
            )
            for row in members[:25]
        ]

        super().__init__(
            placeholder="譲渡するメンバーを選択してください",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild_row = await _resolve_master_guild(interaction, guild_id=self.guild_id)
        if guild_row is None:
            return

        target_id = int(self.values[0])
        guild_id = self.guild_id

        async def _run(confirm_interaction: discord.Interaction) -> None:
            row = await _resolve_master_guild(confirm_interaction, guild_id=guild_id)
            if row is None:
                return

            result = transfer_guild_master(
                guild_id,
                current_master_id=confirm_interaction.user.id,
                new_master_id=target_id,
            )
            if not result["ok"]:
                await game_shared.respond(
                    confirm_interaction, game_shared.error_message(result["error"])
                )
                return

            # マスター専用TCの権限を新マスターへ切り替える
            updated = get_guild(guild_id)
            if updated is not None:
                await service.sync_guild_permissions(
                    confirm_interaction.guild, updated
                )

            guild_name = str(updated["name"]) if updated else f"ID:{guild_id}"
            new_master = await _resolve_user(confirm_interaction, target_id)
            await game_shared.send_dm(
                new_master,
                embed=discord.Embed(
                    title="ギルドマスター譲渡",
                    description=f"ギルド「{guild_name}」のギルドマスターになりました。",
                    color=config.COLOR_GOLD,
                ),
            )

            await service.respond_no_mention(
                confirm_interaction, f"<@{target_id}> へギルドマスターを譲渡しました。"
            )

        await service.respond_no_mention(
            interaction,
            f"<@{target_id}> へギルドマスターを譲渡します。\n"
            "譲渡後、あなたは一般メンバーになります。よろしいですか？",
            view=ConfirmView(interaction.user.id, _run, confirm_label="譲渡する"),
        )


class GuildRenameModal(discord.ui.Modal, title="ギルド名変更"):
    """ギルド名を変更するModal（5.2節）。"""

    def __init__(self, guild_id: int, current_name: str):
        super().__init__(timeout=300)

        self.guild_id = guild_id
        balance = load_master_data().guild

        self.new_name = discord.ui.TextInput(
            label="新しいギルド名",
            placeholder=f"{balance.name_min_length}〜{balance.name_max_length}文字",
            default=current_name[: balance.name_max_length],
            min_length=balance.name_min_length,
            max_length=balance.name_max_length,
            required=True,
        )
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = await _resolve_master_guild(interaction, guild_id=self.guild_id)
        if guild_row is None:
            return

        balance = load_master_data().guild
        new_name = self.new_name.value.strip()

        if not (balance.name_min_length <= len(new_name) <= balance.name_max_length):
            await game_shared.respond(
                interaction,
                f"ギルド名は{balance.name_min_length}文字以上"
                f"{balance.name_max_length}文字以下で入力してください。",
            )
            return

        result = rename_guild(
            self.guild_id,
            new_name=new_name,
            payer_id=interaction.user.id,
            cost=balance.rename_cost,
        )
        if not result["ok"]:
            await game_shared.respond(
                interaction, game_shared.error_message(result["error"])
            )
            return

        # カテゴリー名と募集Embedを新しい名称へ更新する
        category_id = guild_row.get("category_id")
        if category_id and interaction.guild is not None:
            category = interaction.guild.get_channel(int(category_id))
            if isinstance(category, discord.CategoryChannel):
                try:
                    await category.edit(
                        name=new_name[:100], reason="RAGNA Onlineギルド名の変更"
                    )
                except (discord.HTTPException, discord.Forbidden):
                    logger.warning("カテゴリー名の変更に失敗しました: %s", category_id)

        await service.refresh_recruitment_embed(interaction.client, self.guild_id)

        await service.respond_no_mention(
            interaction,
            f"ギルド名を「{result['old_name']}」から「{new_name}」へ変更しました。\n"
            f"支払い：{game_shared.format_coin(balance.rename_cost)}",
        )


class GuildDescriptionModal(discord.ui.Modal, title="ギルド説明変更"):
    """ギルド説明文を変更するModal（6.1節）。"""

    def __init__(self, guild_id: int, current_description: str | None):
        super().__init__(timeout=300)

        self.guild_id = guild_id
        balance = load_master_data().guild

        self.description = discord.ui.TextInput(
            label="ギルド説明",
            style=discord.TextStyle.paragraph,
            placeholder=(
                f"{balance.description_min_length}〜"
                f"{balance.description_max_length}文字"
            ),
            default=(current_description or "")[: balance.description_max_length],
            min_length=balance.description_min_length,
            max_length=balance.description_max_length,
            required=True,
        )
        self.add_item(self.description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = await _resolve_master_guild(interaction, guild_id=self.guild_id)
        if guild_row is None:
            return

        balance = load_master_data().guild
        description = self.description.value.strip()

        if not (
            balance.description_min_length
            <= len(description)
            <= balance.description_max_length
        ):
            await game_shared.respond(
                interaction,
                f"ギルド説明は{balance.description_min_length}文字以上"
                f"{balance.description_max_length}文字以下で入力してください。",
            )
            return

        if not update_guild_description(self.guild_id, description):
            await game_shared.respond(
                interaction, game_shared.error_message("guild_not_found")
            )
            return

        await service.refresh_recruitment_embed(interaction.client, self.guild_id)

        await game_shared.respond(interaction, "ギルド説明を更新しました。")


class RecruitControlView(discord.ui.View):
    """募集の開始・停止・説明変更を選ぶephemeralなView（6.1節）。"""

    def __init__(self, user_id: int, guild_id: int):
        super().__init__(timeout=180)

        self.user_id = user_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(label="募集開始", style=discord.ButtonStyle.success)
    async def start(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = await _resolve_master_guild(interaction, guild_id=self.guild_id)
        if guild_row is None:
            return

        balance = load_master_data().guild
        description = str(guild_row.get("description") or "").strip()

        if len(description) < balance.description_min_length:
            await game_shared.respond(
                interaction,
                "募集を開始するにはギルド説明の登録が必要です。\n"
                "「ギルド説明変更」から登録してください。",
            )
            return

        if not await service.open_recruitment(interaction.client, self.guild_id):
            await game_shared.respond(interaction, "募集を開始できませんでした。")
            return

        await game_shared.respond(interaction, "メンバー募集を開始しました。")

    @discord.ui.button(label="募集停止", style=discord.ButtonStyle.secondary)
    async def stop_recruitment(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = await _resolve_master_guild(interaction, guild_id=self.guild_id)
        if guild_row is None:
            return

        if not await service.close_recruitment(interaction.client, self.guild_id):
            await game_shared.respond(interaction, "募集を停止できませんでした。")
            return

        await game_shared.respond(interaction, "メンバー募集を停止しました。")

    @discord.ui.button(label="説明変更", style=discord.ButtonStyle.secondary)
    async def edit_description(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_master_guild(interaction, guild_id=self.guild_id)
        if guild_row is None:
            return

        await interaction.response.send_modal(
            GuildDescriptionModal(self.guild_id, guild_row.get("description"))
        )


# ==================================================
# ギルドメンバー用パネル（ギルドTC・8.3節）
# ==================================================
class GuildMemberPanelView(discord.ui.View):
    """ギルドTCへ常設する、所属メンバー向けのパネル。"""

    def __init__(self):
        super().__init__(timeout=None)

    # ==================================================
    # ギルド情報
    # ==================================================
    @discord.ui.button(
        label="ギルド情報",
        style=discord.ButtonStyle.primary,
        custom_id="guild:info",
    )
    async def info(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_member_guild(interaction)
        if guild_row is None:
            return

        await service.respond_no_mention(
            interaction,
            embed=service.build_guild_info_embed(interaction.guild, guild_row),
        )

    # ==================================================
    # ギルド脱退（7.1節）
    # ==================================================
    @discord.ui.button(
        label="ギルド脱退",
        style=discord.ButtonStyle.danger,
        custom_id="guild:leave",
    )
    async def leave(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        guild_row = await _resolve_member_guild(interaction)
        if guild_row is None:
            return

        guild_id = int(guild_row["guild_id"])

        if int(guild_row["master_id"]) == interaction.user.id:
            await game_shared.respond(
                interaction, game_shared.error_message("master_cannot_leave")
            )
            return

        # 進行中バトル・編成ロックに加えて、申請中・募集中も脱退できない（7.1節）
        reason = service.can_leave_guild(guild_id, guild_row)
        if reason is not None:
            await game_shared.respond(interaction, reason)
            return

        async def _run(confirm_interaction: discord.Interaction) -> None:
            row = get_guild(guild_id)
            if row is None or str(row.get("status")) != "active":
                await game_shared.respond(confirm_interaction, ARCHIVED_MESSAGE)
                return

            if get_guild_member(guild_id, confirm_interaction.user.id) is None:
                await game_shared.respond(
                    confirm_interaction, game_shared.error_message("not_member")
                )
                return

            # 進行中バトル・編成ロック・申請中・募集中を再確認する
            blocked = service.can_leave_guild(guild_id, row)
            if blocked is not None:
                await game_shared.respond(confirm_interaction, blocked)
                return

            result = remove_guild_member(guild_id, confirm_interaction.user.id)
            if not result["ok"]:
                await game_shared.respond(
                    confirm_interaction, game_shared.error_message(result["error"])
                )
                return

            updated = get_guild(guild_id)
            if updated is not None:
                await service.sync_guild_permissions(confirm_interaction.guild, updated)

            await service.refresh_recruitment_embed(confirm_interaction.client, guild_id)

            await service.respond_no_mention(
                confirm_interaction, f"ギルド「{row['name']}」から脱退しました。"
            )

        await service.respond_no_mention(
            interaction,
            f"ギルド「{guild_row['name']}」から脱退します。よろしいですか？",
            view=ConfirmView(interaction.user.id, _run, confirm_label="脱退する"),
        )
