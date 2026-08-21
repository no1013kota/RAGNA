"""宿屋パネルと宿屋チャンネル内の操作画面をまとめたDiscord UI層。"""

import logging
import discord
import config
import random

from datetime import datetime, timedelta,timezone
from database.coin import add_balance, subtract_balance_if_enough
from database.hotel import (
    create_hotel_room,
    get_hotel_by_channel,
    update_hotel_limit,
    update_hotel_private,
    add_hotel_manager,
    is_hotel_manager,
    get_hotel_by_text_channel,
)
from database.member import get_hotel_free_rate
from texts import common as common_texts
from texts import hotel as hotel_texts

JST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)

# ==========================================
# 宿屋パネル
# ==========================================
class HotelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 入室プラン
    @discord.ui.button(label=hotel_texts.PANEL_BUTTON_PLAN,style=discord.ButtonStyle.primary,custom_id="hotel:plan")
    async def hotel_plan(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelPlanSelect(),ephemeral=True)

    # VC名前変更
    @discord.ui.button(label=hotel_texts.PANEL_BUTTON_RENAME,style=discord.ButtonStyle.secondary,custom_id="hotel:rename")
    async def rename_vc(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_modal(RenameVCModal())

    # 人数変更
    @discord.ui.button(label=hotel_texts.PANEL_BUTTON_LIMIT,style=discord.ButtonStyle.secondary,custom_id="hotel:limit")
    async def change_limit(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_modal(ChangeLimitModal())

    # ステータス変更
    @discord.ui.button(label=hotel_texts.PANEL_BUTTON_STATUS,style=discord.ButtonStyle.secondary,custom_id="hotel:status")
    async def change_status(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_modal(StatusModal())

# ==========================================
# 宿屋Modal
# ==========================================
class ChangeLimitModal(discord.ui.Modal, title=hotel_texts.LIMIT_MODAL_TITLE):

    limit = discord.ui.TextInput(
        label=hotel_texts.LIMIT_MODAL_LABEL,
        placeholder=hotel_texts.LIMIT_MODAL_PLACEHOLDER,
        required=True,
        max_length=2
    )

    async def on_submit(self, interaction: discord.Interaction):

        vc, hotel = await HotelManager.get_hotel(interaction)

        if vc is None:
            await interaction.response.send_message(
                hotel_texts.NOT_IN_VOICE,
                ephemeral=True
            )
            return

        try:
            limit = int(self.limit.value)
        except ValueError:
            await interaction.response.send_message(
                hotel_texts.LIMIT_INVALID_NUMBER,
                ephemeral=True
            )
            return

        plan = hotel[3]

        if plan == hotel_texts.PLAN_PREMIUM:
            if limit == 0:
                await vc.edit(user_limit=0)
                update_hotel_limit(vc.id,0)

            elif 1 <= limit <= 99:
                await vc.edit(user_limit=limit)
                update_hotel_limit(vc.id,limit)

            else:
                await interaction.response.send_message(
                    hotel_texts.LIMIT_OUT_OF_RANGE_PREMIUM,
                    ephemeral=True
                )
                return

        else:
            if not 1 <= limit <= 3:
                await interaction.response.send_message(
                    hotel_texts.LIMIT_OUT_OF_RANGE,
                    ephemeral=True
                )
                return

            await vc.edit(user_limit=limit)

            update_hotel_limit(vc.id,limit)

        await interaction.response.send_message(
            hotel_texts.LIMIT_CHANGED.format(
                limit=limit if limit else hotel_texts.LIMIT_UNLIMITED
            ),
            ephemeral=True
        )

class RenameVCModal(discord.ui.Modal, title=hotel_texts.RENAME_MODAL_TITLE):

    name = discord.ui.TextInput(label=hotel_texts.RENAME_MODAL_LABEL,max_length=30)

    async def on_submit(self, interaction: discord.Interaction):

        vc, hotel = await HotelManager.get_hotel(interaction)

        if vc is None:
            await interaction.response.send_message(hotel_texts.NOT_IN_VOICE,ephemeral=True)
            return

        await vc.edit(name=self.name.value)
        await interaction.response.send_message(hotel_texts.RENAME_DONE,ephemeral=True)

class StatusModal(discord.ui.Modal, title=hotel_texts.STATUS_MODAL_TITLE):

    status = discord.ui.TextInput(
        label=hotel_texts.STATUS_MODAL_LABEL,
        placeholder=hotel_texts.STATUS_MODAL_PLACEHOLDER,
        required=False,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):

        vc, hotel = await HotelManager.get_hotel(interaction)

        if vc is None:
            await interaction.response.send_message(
                hotel_texts.NOT_IN_VOICE,
                ephemeral=True
            )
            return

        await vc.edit(status=self.status.value)
        await interaction.response.send_message(hotel_texts.STATUS_DONE,ephemeral=True)

# ==========================================
# 宿屋プラン
# ==========================================
class HotelPlanSelect(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(HotelPlanDropdown())

# ==========================================
# 宿屋機能
# ==========================================
class HotelPlanDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=hotel_texts.PLAN_STANDARD,
                description=hotel_texts.PLAN_STANDARD_DESCRIPTION
            ),
            discord.SelectOption(
                label=hotel_texts.PLAN_SECRET,
                description=hotel_texts.PLAN_SECRET_DESCRIPTION
            ),
            discord.SelectOption(
                label=hotel_texts.PLAN_PREMIUM,
                description=hotel_texts.PLAN_PREMIUM_DESCRIPTION
            )
        ]
        super().__init__(
            placeholder=hotel_texts.PLAN_SELECT_PLACEHOLDER,
            options=options
        )
    async def callback(self,interaction: discord.Interaction):
        plan = self.values[0]
        plan_data = config.HOTEL_PLANS[plan]

        await interaction.response.defer(ephemeral=True)

        # 利用禁止ロール
        if any(role.id in config.HOTEL_DENY_ROLES for role in interaction.user.roles):
            await interaction.followup.send(hotel_texts.DENY_ROLE,ephemeral=True)

            return

        # 料金
        payment_success, payment_text, paid_amount = await HotelManager.pay(interaction,plan)

        if not payment_success:
            return

        vc = None
        text_channel = None

        try:

            # カテゴリ取得
            category = interaction.guild.get_channel(config.CATEGORY_HOTEL)

            if category is None:
                raise RuntimeError("宿屋カテゴリが見つかりません。")

            # 初期設定
            max_users = plan_data["limit"]
            is_private = plan_data["default_private"]

            expires_at = (
                datetime.now(timezone.utc)
                + timedelta(hours=config.HOTEL_DURATION_HOURS)
            ).isoformat()

            # 権限
            overwrites = {}

            if plan == hotel_texts.PLAN_STANDARD:
                overwrites[
                    interaction.guild.default_role
                ] = discord.PermissionOverwrite(view_channel=None,connect=None)

            else:
                overwrites[
                    interaction.guild.default_role
                ] = discord.PermissionOverwrite(view_channel=False,connect=False)

            # 利用禁止ロール
            for role_id in config.HOTEL_DENY_ROLES:

                role = interaction.guild.get_role(role_id)

                if role:
                    overwrites[role] = discord.PermissionOverwrite(view_channel=False,connect=False)

            # 作成者
            overwrites[interaction.user] = discord.PermissionOverwrite(view_channel=True,connect=True)

            vc = await category.create_voice_channel(
                name=hotel_texts.VOICE_CHANNEL_NAME.format(
                    plan=plan,
                    name=interaction.user.display_name
                ),
                overwrites=overwrites,
                user_limit=max_users
            )

            if plan != hotel_texts.PLAN_STANDARD:

                text_channel = await category.create_text_channel(
                    name=hotel_texts.TEXT_CHANNEL_NAME.format(plan=plan),
                    overwrites=overwrites
                )

            create_hotel_room(
                channel_id=vc.id,
                text_channel_id=text_channel.id if text_channel else None,
                owner_id=interaction.user.id,
                plan=plan,
                expires_at=expires_at,
                is_private=is_private,
                max_users=max_users
            )

        except Exception as e:

            logger.warning(f"宿屋作成失敗：{interaction.user.id} / {plan} / {e}")

            # 作成途中のテキストチャンネルを削除
            if text_channel is not None:
                try:
                    await text_channel.delete()

                except discord.HTTPException:
                    pass

            # 作成途中のVCを削除
            if vc is not None:
                try:
                    await vc.delete()

                except discord.HTTPException:
                    pass

            # 実際に支払い済みの場合だけ返金
            if paid_amount > 0:
                add_balance(interaction.user.id,paid_amount)

            await interaction.followup.send(
                hotel_texts.CREATE_FAILED.format(
                    refund=(
                        hotel_texts.CREATE_FAILED_REFUNDED.format(
                            amount=f"{paid_amount:,}"
                        )
                        if paid_amount > 0
                        else hotel_texts.CREATE_FAILED_NO_PAYMENT
                    )
                ),
                ephemeral=True
            )

            return

        #インチャット用ボタン
        if plan == hotel_texts.PLAN_PREMIUM:

            embed = discord.Embed(
                title=hotel_texts.PERMISSION_PANEL_TITLE,
                description=hotel_texts.PREMIUM_PANEL_BODY.format(
                    voice_channel=vc.mention
                ),
                color=config.COLOR_WHITE
            )
            await text_channel.send(embed=embed,view=HotelPremiumView())

        elif plan == hotel_texts.PLAN_SECRET:
            embed = discord.Embed(
                title=hotel_texts.PERMISSION_PANEL_TITLE,
                description=hotel_texts.SECRET_PANEL_BODY.format(
                    voice_channel=vc.mention
                ),
                color=config.COLOR_WHITE
            )
            await text_channel.send(embed=embed,view=HotelSecretView())

        # VCチャットへ通知
        await HotelManager.send_create_log(interaction,plan,vc)

        # ログへ通知
        await HotelManager.send_create_log(interaction,plan)

        if text_channel:
            msg = hotel_texts.CREATED_WITH_TEXT.format(
                voice_channel=vc.mention,
                text_channel=text_channel.mention
            )

        else:
            msg = hotel_texts.CREATED.format(voice_channel=vc.mention)

        if payment_text:
            msg += f"\n\n{payment_text}"

        await interaction.followup.send(msg,ephemeral=True)
class HotelManager:

    @staticmethod
    async def change_visibility(interaction, public):

        await interaction.response.defer(ephemeral=True)

        vc, hotel = await HotelManager.check_owner(interaction)

        if vc is None or hotel is None:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        owner = interaction.guild.get_member(hotel[2])

        if owner is None:
            await interaction.followup.send(
                hotel_texts.OWNER_NOT_FOUND,
                ephemeral=True
            )
            return None

        text_channel = interaction.guild.get_channel(hotel[1])

        await HotelManager.apply_permissions(vc,text_channel,owner,public)

        update_hotel_private(vc.id,not public)

        return vc, owner

    @staticmethod
    def get_permission_list(
        vc: discord.VoiceChannel,
        owner: discord.Member,
        public: bool
    ):
        roles = []
        users = []

        for target, overwrite in vc.overwrites.items():
            if target == vc.guild.default_role:
                continue

            if target == owner:
                continue

            visible = overwrite.view_channel is not False

            if public:
                # 公開時は見れない一覧
                if visible:
                    continue
            else:
                # 非公開時は見れる一覧
                if not visible:
                    continue

            if isinstance(target, discord.Role):
                roles.append(
                    hotel_texts.PERMISSION_LIST_ROLE.format(name=target.name)
                )
            else:
                users.append(
                    hotel_texts.PERMISSION_LIST_USER.format(
                        name=target.display_name
                    )
                )

        return (
            "\n".join(roles + users)
            if roles or users
            else hotel_texts.PERMISSION_LIST_EMPTY
        )

    @staticmethod
    async def send_visibility_dm(
        interaction: discord.Interaction,
        vc: discord.VoiceChannel,
        owner: discord.Member,
        public: bool
    ):
        text = HotelManager.get_permission_list(vc,owner,public)

        mode = (
            hotel_texts.VISIBILITY_MODE_PUBLIC
            if public
            else hotel_texts.VISIBILITY_MODE_PRIVATE
        )
        title = (
            hotel_texts.VISIBILITY_HEADING_HIDDEN
            if public
            else hotel_texts.VISIBILITY_HEADING_VISIBLE
        )
        color = (
            config.COLOR_GREEN
            if public
            else config.COLOR_RED
        )

        embed = discord.Embed(
            title=hotel_texts.VISIBILITY_DM_TITLE,
            description=hotel_texts.VISIBILITY_DM_BODY.format(
                channel=vc.name,
                mode=mode,
                heading=title,
                targets=text
            ),
            color=color
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        try:
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            pass

    @staticmethod
    async def check_hotel_permission(interaction):

        if interaction.user.voice is None:
            return None

        vc = interaction.user.voice.channel

        hotel = get_hotel_by_channel(vc.id)

        if hotel is None:
            return None

        if (
            hotel[2] != interaction.user.id
            and not is_hotel_manager(
                vc.id,
                interaction.user.id
            )
        ):
            return None

        return vc

    @staticmethod
    async def get_hotel(interaction):

        vc = await HotelManager.check_hotel_permission(
            interaction
        )

        if vc is None:
            return None, None

        hotel = get_hotel_by_channel(
            vc.id
        )

        return vc, hotel

    @staticmethod
    async def send_create_log(interaction: discord.Interaction,plan: str,channel=None):
        if channel is None:
            channel = interaction.guild.get_channel(config.CHANNEL_HOTEL_LOG)

        if channel is None:
            return

        utc_now = datetime.now(timezone.utc)
        expire = (utc_now + timedelta(hours=config.HOTEL_DURATION_HOURS)).astimezone(JST)

        embed = discord.Embed(
            title=hotel_texts.CREATE_LOG_TITLE,
            description=hotel_texts.CREATE_LOG_BODY.format(
                user=interaction.user.mention,
                plan=plan,
                expires_at=expire.strftime('%m/%d %H:%M')
            ),
            color=config.COLOR_WHITE
        )

        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await channel.send(embed=embed)


    @staticmethod
    async def send_permission_log(
        interaction: discord.Interaction,
        mode: str,
        target: discord.Member | discord.Role,
        vc: discord.VoiceChannel
    ):
        log = interaction.guild.get_channel(config.HOTEL_PERMISSION_LOG_CHANNEL_ID)
        kind = (
            hotel_texts.TARGET_KIND_USER
            if isinstance(target, discord.Member)
            else hotel_texts.TARGET_KIND_ROLE
        )

        if log is None:
            return

        embed = discord.Embed(
            title=hotel_texts.PERMISSION_LOG_TITLE,
            description=hotel_texts.PERMISSION_LOG_BODY.format(
                actor=interaction.user.mention,
                channel=vc.name,
                mode=mode,
                kind=kind,
                target=target.mention
            ),
            color=config.COLOR_PURPLE
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await log.send(embed=embed)

    @staticmethod
    async def pay(interaction: discord.Interaction,plan: str) -> tuple[bool, str, int]:

        price = config.HOTEL_PLANS[plan]["price"]

        has_free_role = any(role.id in config.HOTEL_FREE_ROLES for role in interaction.user.roles)

        # 本メンバー以上はツーショ常時無料
        if (plan == hotel_texts.PLAN_STANDARD and has_free_role):
            return True,"",0

        # 本メンバー以上はシークレット・プレミアムで無料抽選
        if (
            plan in (hotel_texts.PLAN_SECRET,hotel_texts.PLAN_PREMIUM)
            and has_free_role
        ):

            free_rate = get_hotel_free_rate(interaction.user.id)
            if free_rate > 0:

                lottery_number = random.randint(1,100)
                if lottery_number <= free_rate:

                    return (True, hotel_texts.FREE_LOTTERY_WON, 0)

        success = subtract_balance_if_enough(interaction.user.id,price)

        if not success:
            await interaction.followup.send(hotel_texts.NOT_ENOUGH_BALANCE,ephemeral=True)

            return False,"",0

        return True,"",price

    @staticmethod
    async def apply_permissions(
        vc: discord.VoiceChannel,
        text_channel: discord.TextChannel,
        owner: discord.Member,
        public: bool
    ):
        """
        public=True  : 公開
        public=False : 非公開
        """

        # @everyone
        await vc.set_permissions(
            vc.guild.default_role,
            view_channel=None if public else False,
            connect=None if public else False
        )
        if text_channel:
            await text_channel.set_permissions(
                text_channel.guild.default_role,
                view_channel=None if public else False,
                send_messages=None if public else False,
                read_message_history=None if public else False
            )

        # 利用禁止ロール
        for role_id in config.HOTEL_DENY_ROLES:
            role = vc.guild.get_role(role_id)
            if role:
                await vc.set_permissions(role,view_channel=False,connect=False)

                if text_channel:
                    await text_channel.set_permissions(
                        role,
                        view_channel=False,
                        send_messages=False,
                        read_message_history=False
                    )

        # 枠主
        await vc.set_permissions(owner,view_channel=True,connect=True)

        if text_channel:
            await text_channel.set_permissions(
                owner,
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

    @staticmethod
    async def check_owner(interaction: discord.Interaction):

        hotel = get_hotel_by_text_channel(interaction.channel.id)
        if hotel is None:
            return None, None

        vc = interaction.guild.get_channel(hotel[0])
        if vc is None:
            return None, None

        if (
            hotel[2] != interaction.user.id
            and not is_hotel_manager(vc.id,interaction.user.id)
        ):
            return None, None

        return vc, hotel

# ==========================================
# プレミアムVC公開設定
# ==========================================
class HotelPremiumView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 公開
    @discord.ui.button(label=hotel_texts.BUTTON_OPEN,style=discord.ButtonStyle.success,custom_id="hotel_premium:open")
    async def open_room(self,interaction: discord.Interaction,button: discord.ui.Button):

        result = await HotelManager.change_visibility(interaction,True)
        if result is None:
            return

        vc, owner = result

        await HotelManager.send_visibility_dm(interaction,vc,owner,True)
        await interaction.followup.send(hotel_texts.OPENED,ephemeral=True)

    # 非公開
    @discord.ui.button(label=hotel_texts.BUTTON_CLOSE,style=discord.ButtonStyle.danger,custom_id="hotel_premium:close")
    async def close_room(self,interaction: discord.Interaction,button: discord.ui.Button):

        result = await HotelManager.change_visibility(interaction,False)

        if result is None:
            return

        vc, owner = result

        await HotelManager.send_visibility_dm(interaction,vc,owner,False)
        await interaction.followup.send(hotel_texts.CLOSED,ephemeral=True)

    # 招待
    @discord.ui.button(label=hotel_texts.BUTTON_INVITE,style=discord.ButtonStyle.success,custom_id="hotel_premium:invite")
    async def invite(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelTargetView("allow"),ephemeral=True)

    # 出禁
    @discord.ui.button(label=hotel_texts.BUTTON_DENY,style=discord.ButtonStyle.danger,custom_id="hotel_premium:deny")
    async def deny(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelTargetView("deny"),ephemeral=True)

    # 共有
    @discord.ui.button(label=hotel_texts.BUTTON_SHARE,style=discord.ButtonStyle.primary,custom_id="hotel_premium:share")
    async def manager(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.send_message(
            view=HotelSelectView(HotelShareSelect()),ephemeral=True
        )

class HotelSecretView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=hotel_texts.BUTTON_INVITE,style=discord.ButtonStyle.success,custom_id="hotel_secret:invite")
    async def invite(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelTargetView("allow"),ephemeral=True)

    @discord.ui.button(label=hotel_texts.BUTTON_DENY,style=discord.ButtonStyle.danger,custom_id="hotel_secret:deny")
    async def deny(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelTargetView("deny"),ephemeral=True)

# ==========================================
# インチャット用ボタン
# ==========================================
class HotelTargetView(discord.ui.View):
    def __init__(self, mode):
        super().__init__(timeout=180)
        self.mode = mode

    @discord.ui.button(label=hotel_texts.BUTTON_TARGET_USER, style=discord.ButtonStyle.primary)
    async def user_button(self, interaction, button):
        await interaction.response.send_message(
            view=HotelSelectView(HotelUserSelect(self.mode)),ephemeral=True
        )

    @discord.ui.button(label=hotel_texts.BUTTON_TARGET_ROLE, style=discord.ButtonStyle.secondary)
    async def role_button(self, interaction, button):
        await interaction.response.send_message(
            view=HotelSelectView(HotelRoleSelect(self.mode)),ephemeral=True
        )

# ==========================================
# ボタン設定
# ==========================================
class HotelSelectView(discord.ui.View):
    def __init__(self, item):
        super().__init__(timeout=180)
        self.add_item(item)

class HotelUserSelect(discord.ui.UserSelect):
    def __init__(self, mode):
        self.mode = mode
        super().__init__(
            placeholder=hotel_texts.USER_SELECT_PLACEHOLDER,
            min_values=1,
            max_values=1
        )
    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        vc, hotel = await HotelManager.check_owner(interaction)

        if vc is None or hotel is None:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        target = self.values[0]
        if target.bot:
            await interaction.followup.send(
                hotel_texts.TARGET_BOT,
                ephemeral=True
            )
            return

        allow = self.mode == "allow"

        if (
            allow
            and any(
                role.id in config.HOTEL_DENY_ROLES
                for role in target.roles
            )
        ):
            await interaction.followup.send(hotel_texts.INVITE_DENIED_USER,ephemeral=True)
            return

        await vc.set_permissions(
            target,
            view_channel=allow,
            connect=allow
        )

        text_channel = interaction.guild.get_channel(hotel[1])

        if text_channel:
            await text_channel.set_permissions(
                target,
                view_channel=allow,
                send_messages=allow,
                read_message_history=allow
            )

        if not allow:
            if target.voice and target.voice.channel == vc:
                await target.move_to(None)

        text = hotel_texts.MODE_INVITE if allow else hotel_texts.MODE_DENY

        await HotelManager.send_permission_log(interaction,text,target,vc)
        await interaction.followup.send(
            hotel_texts.TARGET_UPDATED.format(target=target.mention,mode=text),
            ephemeral=True
        )

class HotelRoleSelect(discord.ui.RoleSelect):
    def __init__(self, mode):
        self.mode = mode
        super().__init__(
            placeholder=hotel_texts.ROLE_SELECT_PLACEHOLDER,
            min_values=1,
            max_values=1
        )
    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        vc, hotel = await HotelManager.check_owner(interaction)
        if vc is None or hotel is None:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        role = self.values[0]
        if role.id in config.HOTEL_DENY_ROLES:
            await interaction.followup.send(hotel_texts.TARGET_DENIED_ROLE,ephemeral=True)
            return

        allow = self.mode == "allow"
        await vc.set_permissions(role,view_channel=allow,connect=allow)

        text_channel = interaction.guild.get_channel(hotel[1])
        if text_channel:
            await text_channel.set_permissions(
                role,
                view_channel=allow,
                send_messages=allow,
                read_message_history=allow
            )

        if not allow:
            for member in vc.members:
                if member.id == hotel[2]:
                    continue

                if role in member.roles:
                    await member.move_to(None)

        text = hotel_texts.MODE_INVITE if allow else hotel_texts.MODE_DENY

        await HotelManager.send_permission_log(interaction,text,role,vc)
        await interaction.followup.send(
            hotel_texts.TARGET_UPDATED.format(target=role.mention,mode=text),
            ephemeral=True
        )

class HotelShareSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder=hotel_texts.SHARE_SELECT_PLACEHOLDER,
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        vc, hotel = await HotelManager.check_owner(interaction)

        if vc is None or hotel is None:
            await interaction.followup.send(common_texts.NO_PERMISSION,ephemeral=True)
            return

        user = self.values[0]

        if user.bot:
            await interaction.followup.send(
                hotel_texts.SHARE_BOT,
                ephemeral=True
            )
            return

        if any(
            role.id in config.HOTEL_DENY_ROLES
            for role in user.roles
        ):
            await interaction.followup.send(hotel_texts.SHARE_DENIED_USER,ephemeral=True)
            return

        # 枠主自身
        if user.id == hotel[2]:
            await interaction.followup.send(
                hotel_texts.SHARE_SELF,
                ephemeral=True
            )
            return

        # VC参加者のみ
        if user not in vc.members:
            await interaction.followup.send(
                hotel_texts.SHARE_NOT_IN_VOICE,
                ephemeral=True
            )
            return

        if is_hotel_manager(vc.id, user.id):
            await interaction.followup.send(hotel_texts.SHARE_ALREADY,ephemeral=True)
            return

        await vc.set_permissions(
            user,
            view_channel=True,
            connect=True
        )

        text_channel = interaction.guild.get_channel(hotel[1])

        if text_channel:
            await text_channel.set_permissions(
                user,
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

        add_hotel_manager(vc.id,user.id)

        await interaction.followup.send(
            hotel_texts.SHARE_DONE.format(user=user.mention),
            ephemeral=True
        )
