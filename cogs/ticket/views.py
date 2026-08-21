"""問い合わせパネルとチケット内の操作画面をまとめたDiscord UI層。"""

import logging
import discord
import config

from database.ticket import (
    create_ticket,
    get_ticket,
    get_ticket_by_owner,
    close_ticket,
    reopen_ticket,
    review_ticket,
    delete_ticket
)
from texts import common as common_texts
from texts import ticket as ticket_texts


logger = logging.getLogger(__name__)


# ==========================================
# お問い合わせパネル
# ==========================================
class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=ticket_texts.PANEL_BUTTON_OPEN,style=discord.ButtonStyle.primary,custom_id="ticket:open")
    async def open_ticket(self,interaction: discord.Interaction,button: discord.ui.Button):

        existing_ticket = get_ticket_by_owner(interaction.user.id)
        if existing_ticket is not None:

            channel_id = existing_ticket[0]
            channel = interaction.guild.get_channel(channel_id)

            # DBにはあるがチャンネルが存在しない場合は記録を削除
            if channel is None:

                delete_ticket(channel_id)

            else:

                await interaction.response.send_message(
                    ticket_texts.ALREADY_OPEN.format(channel=channel.mention),
                    ephemeral=True
                )
                return

        await interaction.response.send_message(
            "",
            view=TicketTypeSelectView(),
            ephemeral=True
        )


# ==========================================
# お問い合わせ先選択View
# ==========================================
class TicketTypeSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

        self.add_item(TicketTypeSelect())


# ==========================================
# お問い合わせ先選択
# ==========================================
class TicketTypeSelect(discord.ui.Select):
    def __init__(self):

        options = []

        for ticket_type, data in config.TICKET_TYPES.items():

            options.append(
                discord.SelectOption(label=data["name"],description=data["description"],value=ticket_type)
            )

        super().__init__(
            placeholder=ticket_texts.TYPE_SELECT_PLACEHOLDER,
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self,interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(common_texts.GUILD_NOT_FOUND,ephemeral=True)
            return

        # すでに作成しているチケットがないか再確認
        existing_ticket = get_ticket_by_owner(interaction.user.id)
        if existing_ticket is not None:

            channel_id = existing_ticket[0]
            channel = guild.get_channel(channel_id)

            # DBだけ残っている場合
            if channel is None:

                delete_ticket(channel_id)

            else:

                await interaction.followup.send(
                    ticket_texts.ALREADY_OPEN.format(channel=channel.mention),
                    ephemeral=True
                )
                return

        ticket_type = self.values[0]
        ticket_data = config.TICKET_TYPES.get(ticket_type)
        if ticket_data is None:
            await interaction.followup.send(ticket_texts.TYPE_NOT_FOUND,ephemeral=True)
            return

        category = guild.get_channel(config.CATEGORY_TICKET)
        if not isinstance(category,discord.CategoryChannel):
            await interaction.followup.send(ticket_texts.CATEGORY_NOT_FOUND,ephemeral=True)
            return

        manager_role = guild.get_role(config.ROLE_MANAGER)
        if manager_role is None:
            await interaction.followup.send(ticket_texts.MANAGER_ROLE_NOT_FOUND,ephemeral=True)
            return

        # ==========================================
        # チャンネル権限
        # ==========================================
        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(view_channel=False),

            interaction.user:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),

            manager_role:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        }

        # 問い合わせ先に設定されたロール
        for role_id in ticket_data["roles"]:

            role = guild.get_role(role_id)
            if role is None:
                continue

            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        # Bot自身
        bot_member = guild.me

        if bot_member is not None:

            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )

        channel = None

        try:

            channel = await guild.create_text_channel(
                name=ticket_texts.CHANNEL_NAME.format(
                    name=interaction.user.display_name
                ),
                category=category,
                overwrites=overwrites,
                reason=(f"{interaction.user}による{ticket_data['name']}作成")
            )

            create_ticket(channel_id=channel.id,owner_id=interaction.user.id,ticket_type=ticket_type)

        except Exception as e:

            logger.warning(f"お問い合わせ作成失敗：{interaction.user.id} / {ticket_type} / {e}")

            # DB登録などで失敗した場合は
            # 作成途中のチャンネルを削除
            if channel is not None:

                try:
                    await channel.delete()

                except discord.HTTPException:
                    pass

            await interaction.followup.send(ticket_texts.CREATE_FAILED,ephemeral=True)
            return

        # ==========================================
        # チケット最初のメッセージ
        # ==========================================
        embed = discord.Embed(
            title=ticket_data["name"],
            description=ticket_texts.FIRST_MESSAGE_BODY.format(
                user=interaction.user.mention
            ),
            color=config.COLOR_WHITE
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        try:
            await channel.send(embed=embed,view=TicketCloseView())

        except discord.HTTPException as e:

            logger.warning(f"お問い合わせ初期メッセージ送信失敗：{channel.id} / {e}")

            delete_ticket(channel.id)

            try:
                await channel.delete()

            except discord.HTTPException:
                pass

            await interaction.followup.send(ticket_texts.CREATE_FAILED,ephemeral=True)
            return

        await interaction.followup.send(
            ticket_texts.CREATED.format(channel=channel.mention),
            ephemeral=True
        )


# ==========================================
# お問い合わせを閉じる
# ==========================================
class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=ticket_texts.BUTTON_CLOSE,style=discord.ButtonStyle.danger,custom_id="ticket:close")
    async def close(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel

        if (guild is None or not isinstance(channel,discord.TextChannel)):
            await interaction.followup.send(ticket_texts.CHANNEL_ERROR,ephemeral=True)
            return

        ticket = get_ticket(channel.id)
        if ticket is None:
            await interaction.followup.send(ticket_texts.NOT_A_TICKET,ephemeral=True)
            return

        # tickets
        # 0 channel_id
        # 1 owner_id
        # 2 ticket_type
        # 3 status
        # 4 created_at
        owner_id = ticket[1]
        status = ticket[3]
        if status != "open":
            await interaction.followup.send(ticket_texts.ALREADY_CLOSED,ephemeral=True)
            return

        manager_role = guild.get_role(config.ROLE_MANAGER)
        if manager_role is None:
            await interaction.followup.send(ticket_texts.MANAGER_ROLE_NOT_FOUND,ephemeral=True)
            return

        # 本人または運営のみ
        is_owner = interaction.user.id == owner_id
        is_manager = interaction.user.guild_permissions.administrator

        if not is_owner and not is_manager:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        ticket_data = config.TICKET_TYPES.get(ticket[2])
        if ticket_data is None:
            await interaction.followup.send(ticket_texts.TYPE_NOT_FOUND,ephemeral=True)
            return

        # ==========================================
        # 閉じた後は本人を外す
        # 問い合わせ対象ロールと運営は閲覧可能
        # ==========================================
        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(view_channel=False),

            manager_role:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        }

        for role_id in ticket_data["roles"]:

            role = guild.get_role(role_id)
            if role is None:
                continue

            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        bot_member = guild.me
        if bot_member is not None:

            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )

        try:

            await channel.edit(overwrites=overwrites,reason=(f"{interaction.user}によるお問い合わせ終了"))

        except discord.HTTPException as e:

            logger.warning(f"お問い合わせ終了時の権限変更失敗：{channel.id} / {e}")

            await interaction.followup.send(ticket_texts.CLOSE_FAILED,ephemeral=True)
            return

        # 閉じる
        close_ticket(channel.id)

        embed = discord.Embed(
            title=ticket_texts.CLOSED_TITLE,
            description=ticket_texts.CLOSED_BODY.format(
                user=interaction.user.mention
            ),
            color=config.COLOR_GREY
        )

        try:
            await channel.send(embed=embed,view=TicketStaffView())

        except discord.HTTPException as e:

            logger.warning(f"お問い合わせ終了メッセージ送信失敗：{channel.id} / {e}")

        await interaction.followup.send(ticket_texts.CLOSE_DONE,ephemeral=True)


# ==========================================
# 閉じたお問い合わせ管理
# ==========================================
class TicketStaffView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ==========================================
    # 再開
    # ==========================================
    @discord.ui.button(label=ticket_texts.BUTTON_REOPEN,style=discord.ButtonStyle.success,custom_id="ticket:staff_reopen")
    async def reopen(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel

        if (guild is None or not isinstance(channel,discord.TextChannel)):
            await interaction.followup.send(ticket_texts.CHANNEL_ERROR,ephemeral=True)
            return

        ticket = get_ticket(channel.id)

        if ticket is None:
            await interaction.followup.send(ticket_texts.TICKET_NOT_FOUND,ephemeral=True)
            return

        if ticket[3] != "closed":
            await interaction.followup.send(ticket_texts.CANNOT_REOPEN,ephemeral=True)
            return

        ticket_data = config.TICKET_TYPES.get(ticket[2])
        if ticket_data is None:
            await interaction.followup.send(ticket_texts.TYPE_NOT_FOUND,ephemeral=True)
            return

        # 問い合わせ対象ロールのみ
        allowed = False

        for role_id in ticket_data["roles"]:

            if any(role.id == role_id for role in interaction.user.roles):
                allowed = True
                break

        if interaction.user.guild_permissions.administrator:
            allowed = True

        if not allowed:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        owner = guild.get_member(ticket[1])
        if owner is None:
            try:
                owner = await guild.fetch_member(ticket[1])

            except (discord.NotFound,discord.HTTPException):
                await interaction.followup.send(ticket_texts.OWNER_NOT_FOUND,ephemeral=True)
                return

        manager_role = guild.get_role(config.ROLE_MANAGER)
        if manager_role is None:
            await interaction.followup.send(ticket_texts.MANAGER_ROLE_NOT_FOUND,ephemeral=True)
            return

        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(view_channel=False),

            owner:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),

            manager_role:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        }

        for role_id in ticket_data["roles"]:

            role = guild.get_role(role_id)
            if role is None:
                continue

            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        bot_member = guild.me
        if bot_member is not None:

            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )

        try:

            await channel.edit(overwrites=overwrites,reason=(f"{interaction.user}によるお問い合わせ再開"))

        except discord.HTTPException as e:

            logger.warning(f"お問い合わせ再開時の権限変更失敗：{channel.id} / {e}")

            await interaction.followup.send(ticket_texts.REOPEN_FAILED,ephemeral=True)
            return

        # 再開
        reopen_ticket(channel.id)

        embed = discord.Embed(
            title=ticket_texts.REOPENED_TITLE,
            description=ticket_texts.REOPENED_BODY.format(owner=owner.mention),
            color=config.COLOR_GREEN
        )

        try:
            await channel.send(embed=embed,view=TicketCloseView())

        except discord.HTTPException as e:
            logger.warning(f"お問い合わせ再開メッセージ送信失敗：{channel.id} / {e}")

        await interaction.followup.send(ticket_texts.REOPEN_DONE,ephemeral=True)

    # ==========================================
    # 削除
    # ==========================================
    @discord.ui.button(label=ticket_texts.BUTTON_DELETE,style=discord.ButtonStyle.danger,custom_id="ticket:staff_delete")
    async def delete(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel

        if (guild is None or not isinstance(channel,discord.TextChannel)):
            await interaction.followup.send(ticket_texts.CHANNEL_ERROR,ephemeral=True)
            return

        ticket = get_ticket(channel.id)

        if ticket is None:
            await interaction.followup.send(ticket_texts.TICKET_NOT_FOUND,ephemeral=True)
            return

        if ticket[3] != "closed":
            await interaction.followup.send(ticket_texts.CANNOT_REVIEW,ephemeral=True)
            return

        ticket_data = config.TICKET_TYPES.get(ticket[2])

        if ticket_data is None:
            await interaction.followup.send(ticket_texts.TYPE_NOT_FOUND,ephemeral=True)
            return

        # 問い合わせ対象ロールのみ
        allowed = False

        for role_id in ticket_data["roles"]:

            if any(role.id == role_id for role in interaction.user.roles):
                allowed = True
                break

        if interaction.user.guild_permissions.administrator:
            allowed = True

        if not allowed:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        manager_role = guild.get_role(config.ROLE_MANAGER)

        if manager_role is None:
            await interaction.followup.send(ticket_texts.MANAGER_ROLE_NOT_FOUND,ephemeral=True)
            return

        # ==========================================
        # 削除確認後は運営のみ閲覧可能
        # ==========================================
        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(view_channel=False),

            manager_role:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        }

        bot_member = guild.me

        if bot_member is not None:

            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )

        try:

            await channel.edit(overwrites=overwrites,reason=(f"{interaction.user}によるお問い合わせ削除確認"))

        except discord.HTTPException as e:

            logger.warning(f"お問い合わせ削除確認時の権限変更失敗：{channel.id} / {e}")

            await interaction.followup.send(ticket_texts.REVIEW_FAILED,ephemeral=True)
            return

        # 削除
        review_ticket(channel.id)

        embed = discord.Embed(
            title=ticket_texts.REVIEW_TITLE,
            description=ticket_texts.REVIEW_BODY.format(
                user=interaction.user.mention
            ),
            color=config.COLOR_GREY
        )

        try:
            await channel.send(embed=embed,view=TicketManagerView())

        except discord.HTTPException as e:
            logger.warning(f"お問い合わせ削除確認メッセージ送信失敗：{channel.id} / {e}")

        await interaction.followup.send(ticket_texts.REVIEW_DONE,ephemeral=True)


# ==========================================
# 運営による最終処理
# ==========================================
class TicketManagerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # ==========================================
    # 削除
    # ==========================================
    @discord.ui.button(label=ticket_texts.BUTTON_DELETE,style=discord.ButtonStyle.danger,custom_id="ticket:manager_delete")
    async def delete(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel

        if (guild is None or not isinstance(channel,discord.TextChannel)):
            await interaction.followup.send(ticket_texts.CHANNEL_ERROR,ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        ticket = get_ticket(channel.id)

        if ticket is None:
            await interaction.followup.send(ticket_texts.TICKET_NOT_FOUND,ephemeral=True)
            return

        if ticket[3] != "review":
            await interaction.followup.send(ticket_texts.NOT_IN_REVIEW,ephemeral=True)
            return

        channel_id = channel.id

        try:

            await channel.delete(reason=(f"{interaction.user}によるお問い合わせ削除"))

        except discord.HTTPException as e:

            logger.warning(f"お問い合わせチャンネル削除失敗：{channel_id} / {e}")

            await interaction.followup.send(ticket_texts.DELETE_FAILED,ephemeral=True)
            return

        # Discord側の削除成功後にDB削除
        delete_ticket(channel_id)

    # ==========================================
    # 再開
    # ==========================================
    @discord.ui.button(label=ticket_texts.BUTTON_REOPEN,style=discord.ButtonStyle.primary,custom_id="ticket:manager_reopen")
    async def reopen(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel

        if (guild is None or not isinstance(channel,discord.TextChannel)):
            await interaction.followup.send(ticket_texts.CHANNEL_ERROR,ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        ticket = get_ticket(channel.id)

        if ticket is None:
            await interaction.followup.send(ticket_texts.TICKET_NOT_FOUND,ephemeral=True)
            return

        if ticket[3] != "review":
            await interaction.followup.send(ticket_texts.CANNOT_REOPEN,ephemeral=True)
            return

        ticket_data = config.TICKET_TYPES.get(ticket[2])

        if ticket_data is None:
            await interaction.followup.send(ticket_texts.TYPE_NOT_FOUND,ephemeral=True)
            return

        owner = guild.get_member(ticket[1])

        if owner is None:

            try:
                owner = await guild.fetch_member(ticket[1])

            except (discord.NotFound,discord.HTTPException):
                await interaction.followup.send(ticket_texts.OWNER_NOT_FOUND,ephemeral=True)
                return

        manager_role = guild.get_role(config.ROLE_MANAGER)

        if manager_role is None:
            await interaction.followup.send(ticket_texts.MANAGER_ROLE_NOT_FOUND,ephemeral=True)
            return

        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(view_channel=False),

            owner:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),

            manager_role:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        }

        for role_id in ticket_data["roles"]:

            role = guild.get_role(role_id)

            if role is None:
                continue

            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        bot_member = guild.me

        if bot_member is not None:

            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )

        try:

            await channel.edit(overwrites=overwrites,reason=(f"{interaction.user}によるお問い合わせ再開"))

        except discord.HTTPException as e:

            logger.warning(f"お問い合わせ再開時の権限変更失敗：{channel.id} / {e}")

            await interaction.followup.send(ticket_texts.REOPEN_FAILED,ephemeral=True)
            return

        reopen_ticket(channel.id)

        embed = discord.Embed(
            title=ticket_texts.REOPENED_TITLE,
            description=ticket_texts.REOPENED_BY_MANAGER_BODY.format(
                owner=owner.mention
            ),
            color=config.COLOR_GREEN
        )

        try:

            await channel.send(embed=embed,view=TicketCloseView())

        except discord.HTTPException as e:
            logger.warning(f"お問い合わせ再開メッセージ送信失敗：{channel.id} / {e}")

        await interaction.followup.send(ticket_texts.REOPEN_DONE,ephemeral=True)

    # ==========================================
    # 保存
    # ==========================================
    @discord.ui.button(label=ticket_texts.BUTTON_SAVE,style=discord.ButtonStyle.success,custom_id="ticket:manager_save")
    async def save(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        channel = interaction.channel

        if (guild is None or not isinstance(channel,discord.TextChannel)):
            await interaction.followup.send(ticket_texts.CHANNEL_ERROR,ephemeral=True)
            return

        if not interaction.user.guild_permissions.administrator:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        ticket = get_ticket(channel.id)

        if ticket is None:
            await interaction.followup.send(ticket_texts.TICKET_NOT_FOUND,ephemeral=True)
            return

        if ticket[3] != "review":
            await interaction.followup.send(ticket_texts.NOT_IN_REVIEW,ephemeral=True)
            return

        archive_category = guild.get_channel(config.CATEGORY_TICKET_ARCHIVE)

        if not isinstance(archive_category,discord.CategoryChannel):
            await interaction.followup.send(ticket_texts.ARCHIVE_CATEGORY_NOT_FOUND,ephemeral=True)
            return

        manager_role = guild.get_role(config.ROLE_MANAGER)

        if manager_role is None:
            await interaction.followup.send(ticket_texts.MANAGER_ROLE_NOT_FOUND,ephemeral=True)
            return

        # ==========================================
        # 保存後は運営のみ閲覧可能
        # ==========================================
        overwrites = {
            guild.default_role:
                discord.PermissionOverwrite(view_channel=False),

            manager_role:
                discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        }

        bot_member = guild.me
        if bot_member is not None:

            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True
            )

        try:
            await channel.edit(
                category=archive_category,
                overwrites=overwrites,
                reason=(f"{interaction.user}によるお問い合わせ保存")
            )

        except discord.HTTPException as e:

            logger.warning(f"お問い合わせ保存失敗：{channel.id} / {e}")

            await interaction.followup.send(ticket_texts.SAVE_FAILED,ephemeral=True)
            return

        # 保存完了後はBot管理対象から外す
        delete_ticket(channel.id)

        embed = discord.Embed(
            title=ticket_texts.SAVED_TITLE,
            description=ticket_texts.SAVED_BODY.format(
                user=interaction.user.mention
            ),
            color=config.COLOR_GREEN
        )

        try:
            await channel.send(embed=embed)

        except discord.HTTPException:
            pass

        await interaction.followup.send(ticket_texts.SAVE_DONE,ephemeral=True)
