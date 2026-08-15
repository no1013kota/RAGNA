"""coin残高、送金、ATMパネル、月次報酬を管理するCog。"""

import logging
import discord
import config

from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timezone, timedelta
from discord.app_commands import Choice
from database import (
    get_balance,
    add_balance,
    add_transaction,
    transfer_balance,
    get_monthly_reward_state,
    set_monthly_reward_state,
    grant_monthly_reward,
)
from utils import ensure_panel_message

JST = timezone(timedelta(hours=9))
logger = logging.getLogger(__name__)

class Coin(commands.Cog):
    def __init__(self,bot):
        self.bot = bot

        self.create_atm_panel.start()
        self.monthly_reward.start()

    # 負債状態更新
    async def update_debt_status(self,guild: discord.Guild,member: discord.Member):

        balance = get_balance(member.id)

        demoted_role = guild.get_role(config.ROLE_DEMOTED)
        associate_member_role = guild.get_role(config.ROLE_ASSOCIATE_MEMBER)
        trial_member_role = guild.get_role(config.ROLE_TRIAL_MEMBER)

        member_group_roles = [
            guild.get_role(role_id)
            for role_id in config.ROLE_GROUP_MEMBER
            if role_id != config.ROLE_MANAGER
        ]

        S_class_role = guild.get_role(config.ROLE_CLASS_S)
        A_class_role = guild.get_role(config.ROLE_CLASS_A)
        B_class_role = guild.get_role(config.ROLE_CLASS_B)
        C_class_role = guild.get_role(config.ROLE_CLASS_C)
        A_plus_role = guild.get_role(config.ROLE_A_PLUS)
        B_plus_role = guild.get_role(config.ROLE_B_PLUS)

        debt_log = guild.get_channel(config.CHANNEL_DEBT_LOG)
        clear_log = guild.get_channel(config.CHANNEL_DEBT_CLEAR_LOG)

        # coinがマイナス
        if balance < 0:

            # 仮メンバーだった場合は評価スレッドを保存
            trial_member_cog = self.bot.get_cog("TrialMember")

            if trial_member_cog is not None:

                archived = await trial_member_cog.archive_demoted_trial_member(member)

                if not archived:
                    logger.warning(f"評価落ち移行時の評価保存に失敗しました：{member.id}")

            if demoted_role not in member.roles:
                remove_roles = []
                removed_role_names = []

                for role in (
                    S_class_role,
                    A_class_role,
                    B_class_role,
                    C_class_role,

                    A_plus_role,
                    B_plus_role,

                    associate_member_role,
                    trial_member_role,

                    *member_group_roles
                ):

                    if role and role in member.roles:
                        remove_roles.append(role)
                        removed_role_names.append(role.name)

                if remove_roles:
                    await member.remove_roles(*remove_roles)

                if demoted_role:
                    await member.add_roles(demoted_role)

                try:
                    embed = discord.Embed(
                        title="負債通知",
                        description=(
                            "残高（coin）がマイナスになりました。\n"
                            "『魔物』となり、利用制限がかかります。"
                        ),
                        color=config.COLOR_RED
                    )

                    await member.send(embed=embed)

                except discord.Forbidden:
                    pass

                except discord.HTTPException:
                    pass

                removed = (
                    ", ".join(removed_role_names)
                    if removed_role_names
                    else "なし"
                )

                if debt_log:
                    embed = discord.Embed(
                        title="負債通知",
                        description=(
                            f"対象：{member.mention}\n"
                            f"残高：**{balance:,} coin**\n"
                            f"削除ロール：{removed}"
                        ),
                        color=config.COLOR_RED
                    )

                    embed.set_thumbnail(url=member.display_avatar.url)

                    await debt_log.send(embed=embed)

        # coinが0以上に戻った
        else:

            if demoted_role in member.roles:
                await member.remove_roles(demoted_role)

                if associate_member_role:
                    await member.add_roles(associate_member_role)

                try:
                    embed = discord.Embed(
                        title="返済通知",
                        description=(
                            "返済が完了したため『小人』となります。"
                        ),
                        color=config.COLOR_YELLOW
                    )

                    await member.send(embed=embed)

                except discord.Forbidden:
                    pass

                except discord.HTTPException:
                    pass

                if clear_log:
                    embed = discord.Embed(
                        title="返済通知",
                        description=(
                            f"対象：{member.mention}\n"
                            f"残高：**{balance:,} coin**"
                        ),
                        color=config.COLOR_YELLOW
                    )

                    embed.set_thumbnail(url=member.display_avatar.url)

                    await clear_log.send(embed=embed)

    #残高変更ログ
    async def send_balance_log(
        self,
        guild: discord.Guild,
        executor: discord.Member,
        target: str,
        amount: int,
        action: str
    ):

        channel = guild.get_channel(config.CHANNEL_BALANCE_LOG)

        if channel is None:
            return

        embed = discord.Embed(
            title="残高変更通知",
            description=(
                f"実行者：{executor.mention}\n"
                f"対象：{target}\n"
                f"結果：**{amount:,} coin {action}**"
            ),
            color=config.COLOR_YELLOW
        )
        embed.set_thumbnail(url=executor.display_avatar.url)
        await channel.send(embed=embed)

    #送金共通処理
    async def execute_transfer(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: int,
        note: str = ""
    ):

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        # 自分への送金禁止
        if user.id == interaction.user.id:
            await interaction.followup.send("自分には送金できません。",ephemeral=True)
            return

        # Botへの送金禁止
        if user.bot:
            await interaction.followup.send("Botには送金できません。",ephemeral=True)
            return

        # 最低送金額
        if amount < 1000:
            await interaction.followup.send("1000coin以上から送金できます。",ephemeral=True)
            return

        # 残高確認から履歴保存までDB内で一括実行し、同時送金による不整合を防ぐ。
        transfer_succeeded = transfer_balance(
            sender_id=interaction.user.id,
            target_id=user.id,
            amount=amount,
            note=note,
        )
        if not transfer_succeeded:
            await interaction.followup.send("残高不足です。",ephemeral=True)
            return

        # ロール更新や通知は、DBへの送金が確定してから行う。
        await self.update_debt_status(interaction.guild,interaction.user)
        await self.update_debt_status(interaction.guild,user)

        # 送金ログ
        log_channel = interaction.guild.get_channel(
            config.CHANNEL_TRANSFER_LOG
        )

        if log_channel:
            description = (
                f"送金：{interaction.user.mention}\n"
                f"対象：{user.mention}\n"
                f"金額：{amount:,} coin"
            )

            if note:
                description += f"\n備考：{note}"

            embed = discord.Embed(
                title="送金ログ",
                description=description,
                color=config.COLOR_YELLOW
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)

            await log_channel.send(embed=embed)

        # 受取人へDM
        try:
            embed = discord.Embed(
                title="RAGNA Bank",
                description=(
                    f"**{interaction.user.display_name}** から "
                    f"**{amount:,} coin** 送金されました。\n"
                    f"備考：{note if note else 'なし'}"
                ),
                color=config.COLOR_YELLOW
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)

            await user.send(embed=embed)

        except discord.Forbidden:
            pass

        except discord.HTTPException:
            pass

        # 送金者へ完了通知
        embed = discord.Embed(
            title="送金通知",
            description=(f"{user.mention} へ **{amount:,} coin** 送金"),
            color=config.COLOR_YELLOW
        )
        embed.set_thumbnail(url=user.display_avatar.url)

        await interaction.followup.send(embed=embed,ephemeral=True)

    # ==================================================
    # ATMパネル自動設置
    # ==================================================
    @tasks.loop(minutes=1)
    async def create_atm_panel(self):

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        for channel_id in config.ATM_PANEL_CHANNELS:
            embed = discord.Embed(
                title="ATM",
                color=config.COLOR_GREY
            )
            await ensure_panel_message(
                self.bot,
                guild,
                channel_id,
                panel_title="ATM",
                embed=embed,
                view=ATMView(),
                panel_name="ATMパネル",
            )

    @create_atm_panel.before_loop
    async def before_create_atm_panel(self):

        await self.bot.wait_until_ready()

    @tasks.loop(hours=1)
    async def monthly_reward(self):
        """未処理の月次報酬を確認し、停止期間があっても現在月へ追いつく。"""

        try:
            await self.process_monthly_rewards()
        except Exception:
            # 一時的なDB・Discord障害でループ自体が永久停止しないよう、次回再試行する。
            logger.exception("月次報酬の処理に失敗しました。1時間後に再試行します")

    async def process_monthly_rewards(self):
        """現在月の未支給分だけを処理する。"""

        today = datetime.now(JST)
        year_month = today.strftime("%Y-%m")
        saved_year_month = get_monthly_reward_state()

        # 新規DBでは過去分を遡って支給せず、現在月を開始地点として記録する。
        if saved_year_month is None:
            set_monthly_reward_state(year_month)
            logger.info("月次報酬の管理年月を初期化しました: %s", year_month)
            return

        if saved_year_month == year_month:
            return

        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        for role_id, amount in config.MONTHLY_ROLE_REWARDS.items():

            role = guild.get_role(role_id)
            if role is None:
                raise RuntimeError(f"月次報酬の対象ロールが見つかりません: role_id={role_id}")

            for member in role.members:
                if member.bot:
                    continue

                granted = grant_monthly_reward(
                    year_month=year_month,
                    user_id=member.id,
                    role_id=role.id,
                    amount=amount,
                    role_name=role.name,
                )
                if not granted:
                    continue

                try:
                    await self.update_debt_status(guild,member)
                except Exception:
                    logger.exception(
                        "月次報酬後の負債ロール更新に失敗しました: user_id=%s role_id=%s",
                        member.id,
                        role.id,
                    )

                balance = get_balance(member.id)
                action = "支給" if amount >= 0 else "徴収"
                embed = discord.Embed(
                    title="RAGNA Bank",
                    description=(
                        f"月次報酬（{role.name}）\n\n"
                        f"{abs(amount):,} coin を{action}しました。\n"
                        f"現在の残高：**{balance:,} coin**"
                    ),
                    color=config.COLOR_YELLOW
                )
                embed.set_thumbnail(url=member.display_avatar.url)

                try:
                    await member.send(embed=embed)
                except discord.Forbidden:
                    # DMを閉じている利用者がいても、支給処理と運営ログは継続する。
                    pass
                except discord.HTTPException:
                    logger.exception("月次報酬DMの送信に失敗しました: user_id=%s", member.id)

                try:
                    await self.send_balance_log(
                        guild,
                        guild.me,
                        member.mention,
                        abs(amount),
                        action,
                    )
                except discord.HTTPException:
                    logger.exception("月次報酬ログの送信に失敗しました: user_id=%s", member.id)

        set_monthly_reward_state(year_month)
        logger.info("月次報酬を処理しました: %s", year_month)

    @monthly_reward.before_loop
    async def before_monthly_reward(self):
        await self.bot.wait_until_ready()

    #指定残高変更
    @app_commands.guilds(discord.Object(id=config.GUILD_ID))
    @app_commands.default_permissions(administrator=True)
    @app_commands.command(name="残高変更", description="ユーザーまたはロールの残高変更")
    @app_commands.choices(
        operation=[
            Choice(name="増加", value="add"),
            Choice(name="減少", value="subtract")
        ]
    )
    async def change_balance(
        self,
        interaction: discord.Interaction,
        operation: Choice[str],
        amount: app_commands.Range[int, 1],
        user: discord.Member | None = None,
        role: discord.Role | None = None
    ):
        await interaction.response.defer(ephemeral=True)

        # どちらも未指定
        if user is None and role is None:
            await interaction.followup.send("ユーザーまたはロールを指定してください。",ephemeral=True)
            return

        # 両方指定
        if user is not None and role is not None:
            await interaction.followup.send("ユーザーとロールは同時に指定できません。",ephemeral=True)
            return

        change = amount if operation.value == "add" else -amount
        action = "増加" if operation.value == "add" else "減少"

        # ------------------------
        # ユーザー
        # ------------------------
        if user is not None:
            add_balance(user.id, change)

            await self.update_debt_status(interaction.guild, user)

            add_transaction(
                transaction_type="残高変更",
                executor_id=interaction.user.id,
                target_id=user.id,
                amount=change,
                note=""
            )

            await self.send_balance_log(
                interaction.guild,
                interaction.user,
                user.mention,
                amount,
                action
            )

            embed = discord.Embed(
                title="残高変更",
                description=(
                    f"{user.mention} の残高を\n"
                    f"**{amount:,} coin {action}**しました。"
                ),
                color=config.COLOR_YELLOW
            )
            embed.set_thumbnail(url=user.display_avatar.url)

            await interaction.followup.send(embed=embed,ephemeral=True)
            return

        # ------------------------
        # ロール
        # ------------------------
        count = 0

        for member in role.members:
            if member.bot:
                continue

            add_balance(member.id, change)

            await self.update_debt_status(interaction.guild, member)

            add_transaction(
                transaction_type="残高変更",
                executor_id=interaction.user.id,
                target_id=member.id,
                amount=change,
                note=role.name
            )

            count += 1

        await self.send_balance_log(interaction.guild,interaction.user,role.mention,amount,action)
        await interaction.followup.send(
            f"{role.mention}（{count}人）の残高を **{amount:,} coin {action}**しました。",
            ephemeral=True
        )

    def cog_unload(self):

        self.create_atm_panel.cancel()
        self.monthly_reward.cancel()

# ==========================================
# ATMパネル
# ==========================================
class ATMView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 送金
    @discord.ui.button(label="送金",style=discord.ButtonStyle.primary,custom_id="atm:transfer")
    async def transfer(self,interaction: discord.Interaction,button: discord.ui.Button):

        await interaction.response.send_message(
            "送金先を選択してください。",
            view=TransferUserSelectView(),
            ephemeral=True
        )

    # 残高照会
    @discord.ui.button(label="残高照会",style=discord.ButtonStyle.secondary,custom_id="atm:balance")
    async def balance(self,interaction: discord.Interaction,button: discord.ui.Button):

        balance = get_balance(interaction.user.id)
        embed = discord.Embed(
            title="残高照会",
            description=(f"現在の残高は **{balance:,} coin**"),
            color=config.COLOR_YELLOW
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        await interaction.response.send_message(embed=embed,ephemeral=True)


# ==========================================
# 送金先選択
# ==========================================
class TransferUserSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

        self.add_item(TransferUserSelect())


class TransferUserSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="送金先を選択してください",min_values=1,max_values=1)

    async def callback(self,interaction: discord.Interaction):

        selected_user = self.values[0]

        # Guild内のMemberとして取得
        target = interaction.guild.get_member(selected_user.id)

        if target is None:
            await interaction.response.send_message("送金先のユーザーを取得できませんでした。",ephemeral=True)
            return

        if target.bot:
            await interaction.response.send_message("Botには送金できません。",ephemeral=True)
            return

        if target.id == interaction.user.id:
            await interaction.response.send_message("自分には送金できません。",ephemeral=True)
            return

        await interaction.response.send_modal(TransferModal(target))

# ==========================================
# 送金内容入力
# ==========================================
class TransferModal(discord.ui.Modal):
    def __init__(self,target: discord.Member):
        super().__init__(title=f"{target.display_name} へ送金")

        self.target = target
        self.amount = discord.ui.TextInput(
            label="送金額",
            placeholder="1000以上の数字を入力",
            required=True,
            max_length=10
        )
        self.note = discord.ui.TextInput(
            label="コメント",
            placeholder="任意",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500
        )

        self.add_item(self.amount)
        self.add_item(self.note)

    async def on_submit(self,interaction: discord.Interaction):

        try:
            amount = int(self.amount.value.strip())

        except ValueError:
            await interaction.response.send_message("送金額は数字で入力してください。",ephemeral=True)
            return

        if amount < 1000:
            await interaction.response.send_message("1000coin以上から送金できます。",ephemeral=True)
            return

        note = (
            self.note.value.strip()
            or ""
        )

        coin_cog = interaction.client.get_cog("Coin")

        if coin_cog is None:
            await interaction.response.send_message("coin管理機能を取得できませんでした。",ephemeral=True)
            return

        await coin_cog.execute_transfer(interaction=interaction,user=self.target,amount=amount,note=note)

async def setup(bot):
    await bot.add_cog(Coin(bot),guild=discord.Object(id=config.GUILD_ID))

    bot.add_view(ATMView())

    logger.info("Coin Cog 登録完了")
