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

from texts import common as common_texts
from texts import guild as guild_texts

from . import service


logger = logging.getLogger(__name__)


# ==================================================
# 共通メッセージと再確認ヘルパー（29節）
# ==================================================
# 文面は texts/guild.py と texts/common.py にまとめています。
CHANNEL_ERROR_MESSAGE = game_shared.CHANNEL_ERROR_MESSAGE
NOT_GUILD_CHANNEL_MESSAGE = guild_texts.NOT_GUILD_CHANNEL
ARCHIVED_MESSAGE = guild_texts.ARCHIVED


def _game_block_reason(
    interaction: discord.Interaction, *, require_member: bool = True
) -> str | None:
    """ゲーム操作を受け付けられない理由を返す（4節・34.1.1節）。

    ``require_member`` が偽のときは公開スイッチだけを確認します。ギルドから
    抜ける操作は、利用資格を失ったあとでも実行できる必要があるためです。
    """

    if require_member:
        return game_shared.game_block_reason(interaction.user)

    return None if game_shared.is_game_enabled() else game_shared.DISABLED_MESSAGE


async def _resolve_master_guild(
    interaction: discord.Interaction,
    *,
    guild_id: int | None = None,
    require_member: bool = True,
) -> dict[str, Any] | None:
    """操作者が対象ギルドの「現在のマスター」かをDBから再確認する。

    条件を満たさない場合は理由をephemeralで返し ``None`` を返します。
    ``require_member`` を偽にすると利用資格（4節）を求めません。譲渡・解散の
    ような「抜けるための操作」まで止めると、本メンバーでなくなったマスターの
    ギルドが誰にも動かせなくなるためです。
    """

    blocked = _game_block_reason(interaction, require_member=require_member)
    if blocked is not None:
        await game_shared.respond(interaction, blocked)
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
    interaction: discord.Interaction, *, require_member: bool = True
) -> dict[str, Any] | None:
    """操作者がそのチャンネルのギルドへ所属しているかをDBから再確認する。"""

    blocked = _game_block_reason(interaction, require_member=require_member)
    if blocked is not None:
        await game_shared.respond(interaction, blocked)
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
        await game_shared.respond(interaction, guild_texts.NOT_A_MEMBER)
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
        confirm_label: str = guild_texts.CONFIRM_BUTTON_DEFAULT,
        confirm_style: discord.ButtonStyle = discord.ButtonStyle.danger,
    ):
        super().__init__(timeout=180)

        self.user_id = user_id
        self.handler = handler
        self.confirm.label = confirm_label[:80]
        self.confirm.style = confirm_style

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(
        label=guild_texts.CONFIRM_BUTTON_DEFAULT, style=discord.ButtonStyle.danger
    )
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
            await game_shared.respond(interaction, common_texts.FAILED)

    @discord.ui.button(
        label=guild_texts.CONFIRM_BUTTON_CANCEL, style=discord.ButtonStyle.secondary
    )
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.stop()
        await game_shared.respond(interaction, guild_texts.CONFIRM_CANCELLED)


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
        label=guild_texts.INTRO_BUTTON_CREATE,
        style=discord.ButtonStyle.primary,
        custom_id="guild:create",
    )
    async def create(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        if not isinstance(interaction.user, discord.Member):
            await game_shared.respond(interaction, guild_texts.NOT_IN_SERVER)
            return

        # Modalを出す前に、明らかに設立できない場合を先に案内する
        if not game_shared.can_found_guild_member(interaction.user):
            await game_shared.respond(interaction, guild_texts.CREATE_ROLE_REQUIRED)
            return

        existing = get_player_guild(interaction.user.id)
        if existing is not None:
            await service.respond_no_mention(
                interaction,
                guild_texts.CREATE_ALREADY_IN_GUILD.format(
                    guild_name=existing["name"]
                ),
            )
            return

        await interaction.response.send_modal(GuildCreateModal())

    # ==================================================
    # 自分の参加申請の確認・取消
    # ==================================================
    @discord.ui.button(
        label=guild_texts.INTRO_BUTTON_MY_REQUESTS,
        style=discord.ButtonStyle.secondary,
        custom_id="guild:my_requests",
    )
    async def my_requests(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        requests = get_pending_join_requests_by_user(interaction.user.id)
        if not requests:
            await game_shared.respond(interaction, guild_texts.MY_REQUESTS_EMPTY)
            return

        await service.respond_no_mention(
            interaction,
            guild_texts.MY_REQUESTS_PROMPT,
            view=SingleItemView(JoinRequestCancelSelect(requests)),
        )


class GuildCreateModal(discord.ui.Modal, title=guild_texts.CREATE_MODAL_TITLE):
    """ギルド名を入力する設立Modal（5.2節）。"""

    def __init__(self):
        super().__init__(timeout=300)

        balance = load_master_data().guild

        self.guild_name = discord.ui.TextInput(
            label=guild_texts.CREATE_NAME_LABEL,
            placeholder=guild_texts.NAME_PLACEHOLDER.format(
                min_length=balance.name_min_length,
                max_length=balance.name_max_length,
            ),
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
            placeholder=guild_texts.MY_REQUESTS_PLACEHOLDER,
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
            interaction, guild_texts.MY_REQUESTS_CANCELLED.format(guild_name=name)
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
        label=guild_texts.RECRUITMENT_BUTTON_JOIN,
        style=discord.ButtonStyle.primary,
        custom_id="guild:join_request",
    )
    async def join_request(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return

        await interaction.response.defer(ephemeral=True)

        message = interaction.message
        if message is None:
            await game_shared.respond(interaction, guild_texts.JOIN_MESSAGE_ERROR)
            return

        guild_row = get_guild_by_recruitment_message(message.id)
        if guild_row is None:
            await game_shared.respond(interaction, guild_texts.JOIN_RECRUITMENT_ENDED)
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
            await game_shared.respond(interaction, guild_texts.JOIN_POST_FAILED)
            return

        await service.respond_no_mention(
            interaction,
            guild_texts.JOIN_SENT.format(guild_name=guild_row["name"]),
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
        label=guild_texts.JOIN_REQUEST_BUTTON_APPROVE,
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

        # 申請してから承認までの間に本メンバーでなくなることがある（4節）
        applicant = await _resolve_user(interaction, applicant_id)
        blocked = game_shared.game_block_reason(applicant, user_id=applicant_id)
        if blocked is not None:
            await game_shared.respond(
                interaction,
                guild_texts.JOIN_REQUEST_APPLICANT_BLOCKED.format(
                    user_id=applicant_id, reason=blocked.splitlines()[0]
                ),
                allowed_mentions=game_shared.NO_MENTIONS,
            )
            return

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
        await game_shared.send_dm(
            applicant,
            embed=discord.Embed(
                title=guild_texts.JOIN_RESULT_DM_TITLE,
                description=guild_texts.JOIN_APPROVED_DM.format(guild_name=guild_name),
                color=config.COLOR_GREEN,
            ),
        )

        await service.respond_no_mention(
            interaction, guild_texts.JOIN_REQUEST_APPROVED.format(user_id=applicant_id)
        )

    # ==================================================
    # 拒否
    # ==================================================
    @discord.ui.button(
        label=guild_texts.JOIN_REQUEST_BUTTON_REJECT,
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
                title=guild_texts.JOIN_RESULT_DM_TITLE,
                description=guild_texts.JOIN_REJECTED_DM.format(guild_name=guild_name),
                color=config.COLOR_RED,
            ),
        )

        await service.respond_no_mention(
            interaction, guild_texts.JOIN_REQUEST_REJECTED.format(user_id=applicant_id)
        )

    # ==================================================
    # 共通：申請の再確認
    # ==================================================
    async def _resolve_request(
        self, interaction: discord.Interaction
    ) -> dict[str, Any] | None:
        """押されたEmbedから申請を特定し、現在のマスターかを再確認する。"""

        blocked = game_shared.game_block_reason(interaction.user)
        if blocked is not None:
            await game_shared.respond(interaction, blocked)
            return None

        await interaction.response.defer(ephemeral=True)

        message = interaction.message
        if message is None:
            await game_shared.respond(
                interaction, guild_texts.JOIN_REQUEST_MESSAGE_ERROR
            )
            return None

        request_row = get_join_request_by_message(message.id)
        if request_row is None:
            await game_shared.respond(interaction, guild_texts.JOIN_REQUEST_NOT_FOUND)
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
        label=guild_texts.MANAGE_BUTTON_KICK,
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
            await game_shared.respond(interaction, guild_texts.KICK_NO_MEMBERS)
            return

        await game_shared.respond(
            interaction,
            guild_texts.KICK_PROMPT,
            view=SingleItemView(
                KickMemberSelect(interaction.guild, members, guild_id=guild_id)
            ),
        )

    # ==================================================
    # マスター譲渡（7.3節）
    # ==================================================
    @discord.ui.button(
        label=guild_texts.MANAGE_BUTTON_TRANSFER,
        style=discord.ButtonStyle.secondary,
        custom_id="guild:transfer",
        row=0,
    )
    async def transfer(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # 譲渡は「抜けるための操作」。利用資格を失っても実行できる（34.1.1節）
        guild_row = await _resolve_master_guild(interaction, require_member=False)
        if guild_row is None:
            return

        guild_id = int(guild_row["guild_id"])
        members = [
            row
            for row in get_guild_members(guild_id)
            if str(row["member_role"]) != "master"
        ]

        if not members:
            await game_shared.respond(interaction, guild_texts.TRANSFER_NO_MEMBERS)
            return

        await game_shared.respond(
            interaction,
            guild_texts.TRANSFER_PROMPT,
            view=SingleItemView(
                TransferMemberSelect(interaction.guild, members, guild_id=guild_id)
            ),
        )

    # ==================================================
    # メンバー枠拡張（7.4節）
    # ==================================================
    @discord.ui.button(
        label=guild_texts.MANAGE_BUTTON_EXPAND,
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
                guild_texts.EXPAND_DONE.format(
                    capacity=result["capacity"],
                    cost=game_shared.format_coin(balance.member_slot_cost),
                ),
            )

        await game_shared.respond(
            interaction,
            guild_texts.EXPAND_CONFIRM.format(
                capacity=capacity,
                next_capacity=capacity + 1,
                cost=game_shared.format_coin(balance.member_slot_cost),
            ),
            view=ConfirmView(
                interaction.user.id,
                _run,
                confirm_label=guild_texts.EXPAND_BUTTON_CONFIRM,
                confirm_style=discord.ButtonStyle.success,
            ),
        )

    # ==================================================
    # ギルド名変更（5.2節）
    # ==================================================
    @discord.ui.button(
        label=guild_texts.MANAGE_BUTTON_RENAME,
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
        label=guild_texts.MANAGE_BUTTON_DESCRIPTION,
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
        label=guild_texts.MANAGE_BUTTON_RECRUIT,
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
            guild_texts.RECRUIT_CONTROL_PROMPT.format(status=state),
            view=RecruitControlView(interaction.user.id, int(guild_row["guild_id"])),
        )

    # ==================================================
    # ギルド解散（7.5節）
    # ==================================================
    @discord.ui.button(
        label=guild_texts.MANAGE_BUTTON_DISBAND,
        style=discord.ButtonStyle.danger,
        custom_id="guild:disband",
        row=1,
    )
    async def disband(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # 解散も「抜けるための操作」。利用資格を失っても実行できる（34.1.1節）
        guild_row = await _resolve_master_guild(interaction, require_member=False)
        if guild_row is None:
            return

        guild_id = int(guild_row["guild_id"])
        balance = load_master_data().guild

        async def _final(final_interaction: discord.Interaction) -> None:
            row = await _resolve_master_guild(
                final_interaction, guild_id=guild_id, require_member=False
            )
            if row is None:
                return

            await service.disband_guild(final_interaction, row)

        async def _first(first_interaction: discord.Interaction) -> None:
            row = await _resolve_master_guild(
                first_interaction, guild_id=guild_id, require_member=False
            )
            if row is None:
                return

            reason = service.can_disband_or_leave(guild_id, row)
            if reason is not None:
                await game_shared.respond(first_interaction, reason)
                return

            # 2段階目の確認
            await service.respond_no_mention(
                first_interaction,
                guild_texts.DISBAND_FINAL_CONFIRM.format(guild_name=row["name"]),
                view=ConfirmView(
                    first_interaction.user.id,
                    _final,
                    confirm_label=guild_texts.DISBAND_BUTTON_CONFIRM,
                ),
            )

        await service.respond_no_mention(
            interaction,
            guild_texts.DISBAND_CONFIRM.format(
                guild_name=guild_row["name"], archive_days=balance.archive_days
            ),
            view=ConfirmView(
                interaction.user.id,
                _first,
                confirm_label=guild_texts.DISBAND_BUTTON_NEXT,
            ),
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
            placeholder=guild_texts.KICK_PLACEHOLDER,
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
            await game_shared.respond(interaction, guild_texts.KICK_MASTER_DENIED)
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
                title=guild_texts.KICK_DM_TITLE,
                description=guild_texts.KICK_DM_BODY.format(guild_name=guild_name),
                color=config.COLOR_RED,
            ),
        )

        await service.respond_no_mention(
            interaction, guild_texts.KICK_DONE.format(user_id=target_id)
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
            placeholder=guild_texts.TRANSFER_PLACEHOLDER,
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild_row = await _resolve_master_guild(
            interaction, guild_id=self.guild_id, require_member=False
        )
        if guild_row is None:
            return

        target_id = int(self.values[0])
        guild_id = self.guild_id

        async def _run(confirm_interaction: discord.Interaction) -> None:
            row = await _resolve_master_guild(
                confirm_interaction, guild_id=guild_id, require_member=False
            )
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
                    title=guild_texts.TRANSFER_DM_TITLE,
                    description=guild_texts.TRANSFER_DM_BODY.format(
                        guild_name=guild_name
                    ),
                    color=config.COLOR_GOLD,
                ),
            )

            await service.respond_no_mention(
                confirm_interaction,
                guild_texts.TRANSFER_DONE.format(user_id=target_id),
            )

        await service.respond_no_mention(
            interaction,
            guild_texts.TRANSFER_CONFIRM.format(user_id=target_id),
            view=ConfirmView(
                interaction.user.id,
                _run,
                confirm_label=guild_texts.TRANSFER_BUTTON_CONFIRM,
            ),
        )


class GuildRenameModal(discord.ui.Modal, title=guild_texts.RENAME_MODAL_TITLE):
    """ギルド名を変更するModal（5.2節）。"""

    def __init__(self, guild_id: int, current_name: str):
        super().__init__(timeout=300)

        self.guild_id = guild_id
        balance = load_master_data().guild

        self.new_name = discord.ui.TextInput(
            label=guild_texts.RENAME_NAME_LABEL,
            placeholder=guild_texts.NAME_PLACEHOLDER.format(
                min_length=balance.name_min_length,
                max_length=balance.name_max_length,
            ),
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
                guild_texts.NAME_LENGTH_ERROR.format(
                    min_length=balance.name_min_length,
                    max_length=balance.name_max_length,
                ),
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

        # メンバー用パネルの表題はギルド名なので、その場で書き換える（8.3節）
        updated_row = get_guild(self.guild_id)
        if updated_row is not None and interaction.guild is not None:
            try:
                await service.ensure_member_panel(
                    interaction.client, interaction.guild, updated_row
                )
            except Exception:
                logger.exception(
                    "メンバー用パネルの表題更新に失敗しました: guild_id=%s", self.guild_id
                )

        await service.respond_no_mention(
            interaction,
            guild_texts.RENAME_DONE.format(
                old_name=result["old_name"],
                new_name=new_name,
                cost=game_shared.format_coin(balance.rename_cost),
            ),
        )


class GuildDescriptionModal(
    discord.ui.Modal, title=guild_texts.DESCRIPTION_MODAL_TITLE
):
    """ギルド説明文を変更するModal（6.1節）。"""

    def __init__(self, guild_id: int, current_description: str | None):
        super().__init__(timeout=300)

        self.guild_id = guild_id
        balance = load_master_data().guild

        self.description = discord.ui.TextInput(
            label=guild_texts.DESCRIPTION_LABEL,
            style=discord.TextStyle.paragraph,
            placeholder=guild_texts.DESCRIPTION_PLACEHOLDER.format(
                min_length=balance.description_min_length,
                max_length=balance.description_max_length,
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
                guild_texts.DESCRIPTION_LENGTH_ERROR.format(
                    min_length=balance.description_min_length,
                    max_length=balance.description_max_length,
                ),
            )
            return

        if not update_guild_description(self.guild_id, description):
            await game_shared.respond(
                interaction, game_shared.error_message("guild_not_found")
            )
            return

        await service.refresh_recruitment_embed(interaction.client, self.guild_id)

        await game_shared.respond(interaction, guild_texts.DESCRIPTION_DONE)


class RecruitControlView(discord.ui.View):
    """募集の開始・停止・説明変更を選ぶephemeralなView（6.1節）。"""

    def __init__(self, user_id: int, guild_id: int):
        super().__init__(timeout=180)

        self.user_id = user_id
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.button(
        label=guild_texts.RECRUIT_BUTTON_START, style=discord.ButtonStyle.success
    )
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
                interaction, guild_texts.RECRUIT_DESCRIPTION_REQUIRED
            )
            return

        if not await service.open_recruitment(interaction.client, self.guild_id):
            await game_shared.respond(interaction, guild_texts.RECRUIT_START_FAILED)
            return

        await game_shared.respond(interaction, guild_texts.RECRUIT_STARTED)

    @discord.ui.button(
        label=guild_texts.RECRUIT_BUTTON_STOP, style=discord.ButtonStyle.secondary
    )
    async def stop_recruitment(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_row = await _resolve_master_guild(interaction, guild_id=self.guild_id)
        if guild_row is None:
            return

        if not await service.close_recruitment(interaction.client, self.guild_id):
            await game_shared.respond(interaction, guild_texts.RECRUIT_STOP_FAILED)
            return

        await game_shared.respond(interaction, guild_texts.RECRUIT_STOPPED)

    @discord.ui.button(
        label=guild_texts.RECRUIT_BUTTON_DESCRIPTION,
        style=discord.ButtonStyle.secondary,
    )
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
    """ギルド情報チャンネルへ常設する、所属メンバー向けのパネル（8.3節）。"""

    def __init__(self):
        super().__init__(timeout=None)

    # ==================================================
    # ギルド情報
    # ==================================================
    @discord.ui.button(
        label=guild_texts.MEMBER_BUTTON_INFO,
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
        label=guild_texts.MEMBER_BUTTON_LEAVE,
        style=discord.ButtonStyle.danger,
        custom_id="guild:leave",
    )
    async def leave(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        # 脱退も「抜けるための操作」。利用資格を失っても実行できる（34.1.1節）
        guild_row = await _resolve_member_guild(interaction, require_member=False)
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
                confirm_interaction,
                guild_texts.LEAVE_DONE.format(guild_name=row["name"]),
            )

        await service.respond_no_mention(
            interaction,
            guild_texts.LEAVE_CONFIRM.format(guild_name=guild_row["name"]),
            view=ConfirmView(
                interaction.user.id,
                _run,
                confirm_label=guild_texts.LEAVE_BUTTON_CONFIRM,
            ),
        )
