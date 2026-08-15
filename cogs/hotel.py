"""coinで作成する一時的な宿屋チャンネルを管理するCog。"""

import logging
import discord
import config
import asyncio
import random

from discord.ext import commands, tasks
from datetime import datetime, timedelta,timezone
from database import (
    subtract_balance_if_enough,
    add_balance,
    get_hotel_free_rate,
    create_hotel_room,
    get_hotel_by_channel,
    update_hotel_limit,
    update_hotel_private,
    add_hotel_manager,
    is_hotel_manager,
    get_all_hotels,
    delete_hotel_room,
    get_hotel_by_text_channel
)
from utils import ensure_panel_message

JST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)

# ==========================================
# 宿屋パネル
# ==========================================
class HotelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 入室プラン
    @discord.ui.button(label="入室プラン",style=discord.ButtonStyle.primary,custom_id="hotel:plan")
    async def hotel_plan(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelPlanSelect(),ephemeral=True)

    # VC名前変更
    @discord.ui.button(label="VC名変更",style=discord.ButtonStyle.secondary,custom_id="hotel:rename")
    async def rename_vc(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_modal(RenameVCModal())

    # 人数変更
    @discord.ui.button(label="人数変更",style=discord.ButtonStyle.secondary,custom_id="hotel:limit")
    async def change_limit(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_modal(ChangeLimitModal())

    # ステータス変更
    @discord.ui.button(label="ステータス変更",style=discord.ButtonStyle.secondary,custom_id="hotel:status")
    async def change_status(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_modal(StatusModal())

# ==========================================
# 宿屋Modal
# ==========================================
class ChangeLimitModal(discord.ui.Modal, title="人数変更"):

    limit = discord.ui.TextInput(
        label="人数",
        placeholder="1～3（プレミアムは0で無制限）",
        required=True,
        max_length=2
    )

    async def on_submit(self, interaction: discord.Interaction):

        vc, hotel = await HotelManager.get_hotel(interaction)

        if vc is None:
            await interaction.response.send_message(
                "VCにいるときだけ使用できます。",
                ephemeral=True
            )
            return

        try:
            limit = int(self.limit.value)
        except ValueError:
            await interaction.response.send_message(
                "数字を入力してください。",
                ephemeral=True
            )
            return

        plan = hotel[3]

        if plan == "プレミアム":
            if limit == 0:
                await vc.edit(user_limit=0)
                update_hotel_limit(vc.id,0)

            elif 1 <= limit <= 99:
                await vc.edit(user_limit=limit)
                update_hotel_limit(vc.id,limit)

            else:
                await interaction.response.send_message(
                    "0～99で入力してください。",
                    ephemeral=True
                )
                return

        else:
            if not 1 <= limit <= 3:
                await interaction.response.send_message(
                    "1～3人にしてください。",
                    ephemeral=True
                )
                return

            await vc.edit(user_limit=limit)

            update_hotel_limit(vc.id,limit)

        await interaction.response.send_message(
            f"人数を **{limit if limit else '無制限'}** に変更しました。",
            ephemeral=True
        )

class RenameVCModal(discord.ui.Modal, title="VC名変更"):

    name = discord.ui.TextInput(label="VC名",max_length=30)

    async def on_submit(self, interaction: discord.Interaction):

        vc, hotel = await HotelManager.get_hotel(interaction)

        if vc is None:
            await interaction.response.send_message("VCにいるときだけ使用できます。",ephemeral=True)
            return

        await vc.edit(name=self.name.value)
        await interaction.response.send_message("VC名を変更しました。",ephemeral=True)

class StatusModal(discord.ui.Modal, title="ステータス変更"):

    status = discord.ui.TextInput(
        label="ステータス",
        placeholder="雑談中・ゲーム中など",
        required=False,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):

        vc, hotel = await HotelManager.get_hotel(interaction)

        if vc is None:
            await interaction.response.send_message(
                "VCにいるときだけ使用できます。",
                ephemeral=True
            )
            return

        await vc.edit(status=self.status.value)
        await interaction.response.send_message("ステータスを変更しました。",ephemeral=True)

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
                label="ツーショ",
                description="2人まで利用可能"
            ),
            discord.SelectOption(
                label="シークレット",
                description="通話相手のみ閲覧可能"
            ),
            discord.SelectOption(
                label="プレミアム",
                description="人数・用途フリー"
            )
        ]
        super().__init__(
            placeholder="プランを選択してください",
            options=options
        )
    async def callback(self,interaction: discord.Interaction):
        plan = self.values[0]
        plan_data = config.HOTEL_PLANS[plan]

        await interaction.response.defer(ephemeral=True)

        # 利用禁止ロール
        if any(role.id in config.HOTEL_DENY_ROLES for role in interaction.user.roles):
            await interaction.followup.send("現在のロールでは宿屋を利用できません。",ephemeral=True)

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

            if plan == "ツーショ":
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
                name=f"{plan}-{interaction.user.display_name}",
                overwrites=overwrites,
                user_limit=max_users
            )

            if plan != "ツーショ":

                text_channel = await category.create_text_channel(
                    name=f"宿設定-{plan}",
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
                (
                    "宿屋の作成に失敗しました。\n"
                    + (
                        f"支払った **{paid_amount:,} coin** は返金しました。"
                        if paid_amount > 0
                        else "coinの支払いは発生していません。"
                    )
                ),
                ephemeral=True
            )

            return

        #インチャット用ボタン
        if plan == "プレミアム":

            embed = discord.Embed(
                title="権限設定",
                description=(
                    f"{vc.mention}\n"
                    "ここは宿を作成した人しか見えないよ！\n"
                    "-# ※共有したら見えるようになるよ\n\n"
                    "🔓 公開\n"
                    "🔒 非公開\n\n"
                    "👥 招待\n"
                    "🚫 出禁\n"
                    "🤝 共有"
                ),
                color=config.COLOR_WHITE
            )
            await text_channel.send(embed=embed,view=HotelPremiumView())

        elif plan == "シークレット":
            embed = discord.Embed(
                title="権限設定",
                description=(
                    f"{vc.mention}\n"
                    f"※ここは宿を作成した人しか見えないよ！\n\n"
                    "👥 招待\n"
                    "🚫 出禁"
                ),
                color=config.COLOR_WHITE
            )
            await text_channel.send(embed=embed,view=HotelSecretView())

        # VCチャットへ通知
        await HotelManager.send_create_log(interaction,plan,vc)

        # ログへ通知
        await HotelManager.send_create_log(interaction,plan)

        if text_channel:
            msg = (
                f"{vc.mention} を作成しました。\n"
                f"{text_channel.mention} で宿の設定ができます。"
            )

        else:
            msg = (f"{vc.mention} を作成しました。")

        if payment_text:
            msg += f"\n\n{payment_text}"

        await interaction.followup.send(msg,ephemeral=True)

# ==========================================
# 宿屋パネル
# ==========================================
class HotelCog(commands.Cog):
    def __init__(self,bot):
        self.bot = bot

        self.hotel_panel.start()
        self.hotel_delete_task.start()

    # ==========================================
    # 宿屋パネル自動設置
    # ==========================================
    @tasks.loop(minutes=5)
    async def hotel_panel(self):

        guild = self.bot.get_guild(config.GUILD_ID)

        if guild is None:
            return

        embed = discord.Embed(
            title="入室プラン",
            description=(
                "\u200b\n"
                f"**ツーショ：{config.HOTEL_STANDARD_PRICE:,} coin**\n"
                "-# ※七聖・騎士は無料\n\n"
                f"**シークレット：{config.HOTEL_SECRET_PRICE:,} coin**\n"
                "ー ツーショ＋指定した人だけが見える\n\n"
                f"**プレミアム：{config.HOTEL_PREMIUM_PRICE:,} coin**\n"
                "ー すべて自由にできる＋人数も無制限\n\n"
                "-# ※宿は15時間後に自動削除します"
            ),
            color=config.COLOR_WHITE
        )

        await ensure_panel_message(
            self.bot,
            guild,
            config.CHANNEL_HOTEL_PANEL,
            panel_title="入室プラン",
            embed=embed,
            view=HotelView(),
            panel_name="宿屋パネル",
        )

    @hotel_panel.before_loop
    async def before_hotel_panel(self):

        await self.bot.wait_until_ready()
        await asyncio.sleep(5)

    # 宿の自動削除機能
    @tasks.loop(minutes=1)
    async def hotel_delete_task(self):

        now = datetime.now(timezone.utc)

        for hotel in get_all_hotels():

            channel_id = hotel[0]
            text_channel_id = hotel[1]
            expires_at = hotel[2]

            if datetime.fromisoformat(expires_at) > now:
                continue

            vc = self.bot.get_channel(channel_id)
            text_channel = (
                self.bot.get_channel(text_channel_id)
                if text_channel_id is not None
                else None
            )

            if text_channel:
                try:
                    await text_channel.delete()
                except discord.NotFound:
                    pass

            if vc:
                try:
                    await vc.delete()
                except discord.NotFound:
                    pass

            delete_hotel_room(channel_id)

    @hotel_delete_task.before_loop
    async def before_hotel_delete(self):
        await self.bot.wait_until_ready()

    def cog_unload(self):

        self.hotel_panel.cancel()
        self.hotel_delete_task.cancel()

# ==========================================
# 宿屋管理
# ==========================================
class HotelManager:

    @staticmethod
    async def change_visibility(interaction, public):

        await interaction.response.defer(ephemeral=True)

        vc, hotel = await HotelManager.check_owner(interaction)

        if vc is None or hotel is None:
            await interaction.followup.send("権限がありません。",ephemeral=True)
            return

        owner = interaction.guild.get_member(hotel[2])

        if owner is None:
            await interaction.followup.send(
                "宿の購入者が見つかりません。",
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
                roles.append(f"（ロール）{target.name}")
            else:
                users.append(f"（ユーザー）{target.display_name}")

        return "\n".join(roles + users) if roles or users else "なし"

    @staticmethod
    async def send_visibility_dm(
        interaction: discord.Interaction,
        vc: discord.VoiceChannel,
        owner: discord.Member,
        public: bool
    ):
        text = HotelManager.get_permission_list(vc,owner,public)

        mode = "公開" if public else "非公開"
        title = "閲覧不可" if public else "閲覧可能"
        color = (
            config.COLOR_GREEN
            if public
            else config.COLOR_RED
        )

        embed = discord.Embed(
            title="【宿】設定変更",
            description=(
                f"{vc.name} を {mode} にしました。\n"
                f"### {title}\n"
                f"{text}"
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
            title="購入通知",
            description=(
                f"購入者：{interaction.user.mention}\n"
                f"プラン：**{plan}**\n"
                f"終了時刻：{expire.strftime('%m/%d %H:%M')}"
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
        kind = "ユーザー" if isinstance(target, discord.Member) else "ロール"

        if log is None:
            return

        embed = discord.Embed(
            title="権限変更",
            description=(
                f"実行者：{interaction.user.mention}\n"
                f"通話：{vc.name}\n"
                f"設定：**{mode}**\n"
                f"対象：{kind}（{target.mention}）"
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
        if (plan == "ツーショ" and has_free_role):
            return True,"",0

        # 本メンバー以上はシークレット・プレミアムで無料抽選
        if (plan in ("シークレット","プレミアム") and has_free_role):

            free_rate = get_hotel_free_rate(interaction.user.id)
            if free_rate > 0:

                lottery_number = random.randint(1,100)
                if lottery_number <= free_rate:

                    return (True, "宿屋無料抽選に当選したため、無料になりました。", 0)

        success = subtract_balance_if_enough(interaction.user.id,price)

        if not success:
            await interaction.followup.send("残高が不足しています。",ephemeral=True)

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
    @discord.ui.button(label="🔓 公開",style=discord.ButtonStyle.success,custom_id="hotel_premium:open")
    async def open_room(self,interaction: discord.Interaction,button: discord.ui.Button):

        result = await HotelManager.change_visibility(interaction,True)
        if result is None:
            return

        vc, owner = result

        await HotelManager.send_visibility_dm(interaction,vc,owner,True)
        await interaction.followup.send("宿を公開しました。",ephemeral=True)

    # 非公開
    @discord.ui.button(label="🔒 非公開",style=discord.ButtonStyle.danger,custom_id="hotel_premium:close")
    async def close_room(self,interaction: discord.Interaction,button: discord.ui.Button):

        result = await HotelManager.change_visibility(interaction,False)

        if result is None:
            return

        vc, owner = result

        await HotelManager.send_visibility_dm(interaction,vc,owner,False)
        await interaction.followup.send("宿を非公開にしました。",ephemeral=True)

    # 招待
    @discord.ui.button(label="👥 招待",style=discord.ButtonStyle.success,custom_id="hotel_premium:invite")
    async def invite(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelTargetView("allow"),ephemeral=True)

    # 出禁
    @discord.ui.button(label="🚫 出禁",style=discord.ButtonStyle.danger,custom_id="hotel_premium:deny")
    async def deny(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelTargetView("deny"),ephemeral=True)

    # 共有
    @discord.ui.button(label="🤝 共有",style=discord.ButtonStyle.primary,custom_id="hotel_premium:share")
    async def manager(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.send_message(
            view=HotelSelectView(HotelShareSelect()),ephemeral=True
        )

class HotelSecretView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="👥 招待",style=discord.ButtonStyle.success,custom_id="hotel_secret:invite")
    async def invite(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelTargetView("allow"),ephemeral=True)

    @discord.ui.button(label="🚫 出禁",style=discord.ButtonStyle.danger,custom_id="hotel_secret:deny")
    async def deny(self,interaction: discord.Interaction,button: discord.ui.Button):
        await interaction.response.send_message(view=HotelTargetView("deny"),ephemeral=True)

# ==========================================
# インチャット用ボタン
# ==========================================
class HotelTargetView(discord.ui.View):
    def __init__(self, mode):
        super().__init__(timeout=180)
        self.mode = mode

    @discord.ui.button(label="👤 ユーザー", style=discord.ButtonStyle.primary)
    async def user_button(self, interaction, button):
        await interaction.response.send_message(
            view=HotelSelectView(HotelUserSelect(self.mode)),ephemeral=True
        )

    @discord.ui.button(label="🏷 ロール", style=discord.ButtonStyle.secondary)
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
            placeholder="ユーザーを選択してください",
            min_values=1,
            max_values=1
        )
    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        vc, hotel = await HotelManager.check_owner(interaction)

        if vc is None or hotel is None:
            await interaction.followup.send("権限がありません。",ephemeral=True)
            return

        target = self.values[0]
        if target.bot:
            await interaction.followup.send(
                "Botは指定できません。",
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
            await interaction.followup.send("宿屋を利用できないユーザーは招待できません。",ephemeral=True)
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

        text = "招待" if allow else "出禁"

        await HotelManager.send_permission_log(interaction,text,target,vc)
        await interaction.followup.send(f"{target.mention} を **{text}**",ephemeral=True)

class HotelRoleSelect(discord.ui.RoleSelect):
    def __init__(self, mode):
        self.mode = mode
        super().__init__(
            placeholder="ロールを選択してください",
            min_values=1,
            max_values=1
        )
    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        vc, hotel = await HotelManager.check_owner(interaction)
        if vc is None or hotel is None:
            await interaction.followup.send("権限がありません。",ephemeral=True)
            return

        role = self.values[0]
        if role.id in config.HOTEL_DENY_ROLES:
            await interaction.followup.send("このロールは指定できません。",ephemeral=True)
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

        text = "招待" if allow else "出禁"

        await HotelManager.send_permission_log(interaction,text,role,vc)
        await interaction.followup.send(f"{role.mention} を **{text}**",ephemeral=True)

class HotelShareSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(
            placeholder="共有するユーザーを選択",
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):

        await interaction.response.defer(ephemeral=True)

        vc, hotel = await HotelManager.check_owner(interaction)

        if vc is None or hotel is None:
            await interaction.followup.send("権限がありません。",ephemeral=True)
            return

        user = self.values[0]

        if user.bot:
            await interaction.followup.send(
                "Botには共有できません。",
                ephemeral=True
            )
            return

        if any(
            role.id in config.HOTEL_DENY_ROLES
            for role in user.roles
        ):
            await interaction.followup.send("宿屋を利用できないユーザーには共有できません。",ephemeral=True)
            return

        # 枠主自身
        if user.id == hotel[2]:
            await interaction.followup.send(
                "自分には付与できません。",
                ephemeral=True
            )
            return

        # VC参加者のみ
        if user not in vc.members:
            await interaction.followup.send(
                "指定したユーザーがVCにいません。",
                ephemeral=True
            )
            return

        if is_hotel_manager(vc.id, user.id):
            await interaction.followup.send("既に共有しています。",ephemeral=True)
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

        await interaction.followup.send(f"{user.mention} に権限を付与しました。",ephemeral=True)

async def setup(bot):
    await bot.add_cog(HotelCog(bot),guild=discord.Object(id=config.GUILD_ID))

    bot.add_view(HotelView())
    bot.add_view(HotelPremiumView())
    bot.add_view(HotelSecretView())

    logger.info("Hotel Cog 登録完了")
